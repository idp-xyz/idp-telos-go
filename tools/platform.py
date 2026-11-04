"""IDP TELOS platform integration tool (docs/adr/ADR-0001-platform-integration.md T3, T4).

  python tools/platform.py bom     [--root DIR]   regenerate releases/platform-bom.yaml from the side-by-side checkouts
  python tools/platform.py matrix  [--root DIR]   regenerate releases/compatibility-matrix.yaml from the pinned contract copies
  python tools/platform.py check   [--root DIR]   verify catalog, BOM and matrix; exit 0 passed, 1 failed, 2 something could not run

DIR is the workspace root (default: this repository). Component repositories are expected as
DIR/<name> (the side-by-side clone layout). A missing checkout is NOT_RUN, never a pass.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]
PIN = re.compile(r"(?:^|/)contracts/(idp\.telos\.[a-z]+)/(\d+\.\d+\.\d+)/schema\.json$")
SKIP_DIRS = {"node_modules", ".git", ".venv", "build", "dist", ".local"}


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dump_yaml(path, header, data):
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)
    path.write_text(header + body, encoding="utf-8")


def version_tuple(v):
    return tuple(int(x) for x in v.split("."))


class Platform:
    def __init__(self, root):
        self.root = Path(root)
        self.registry = json.loads((self.root / "idp-contracts/registry.json").read_text(encoding="utf-8"))
        self.components = load_yaml(self.root / "catalog/components.yaml")["components"]
        self.integrations = load_yaml(self.root / "catalog/integrations.yaml")["integrations"]
        self.ownership = load_yaml(self.root / "catalog/ownership.yaml")["ownership"]

    def checkout(self, name):
        path = self.root / name
        return path if (path / ".git").exists() else None

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def registered(self, namespace):
        return self.registry["namespaces"].get(namespace)

    def registered_files(self, namespace):
        """version -> schema path (relative to idp-contracts) for the current and previous versions."""
        n = self.registered(namespace)
        if not n:
            return {}
        out = {n["version"]: n["schema"]}
        for p in n.get("previous", []):
            if p.get("schema"):
                out[p["version"]] = p["schema"]
        return out

    # ---- BOM (T3) ----
    def bom(self, docker=True):
        components = []
        for c in self.components:
            repo = self.checkout(c["name"])
            entry = {"name": c["name"], "repository": c["repository"]}
            if repo is None:
                entry["status"] = "NOT_RUN (no checkout)"
                components.append(entry)
                continue
            entry["commit"] = self.git(repo, "rev-parse", "HEAD")
            entry["clean"] = self.git(repo, "status", "--porcelain", "--untracked-files=no") == ""
            produced = c["contracts"]["produces"]
            if produced:
                schema = json.loads((repo / "api/contracts/schema.json").read_text(encoding="utf-8"))
                entry["contract"] = {"namespace": produced, "version": schema["$id"].rsplit(":", 1)[1]}
            if docker and c.get("image") and c.get("platform"):  # only images the platform acceptance built and ran
                digest = self.image_digest(c["image"] + ":dev")
                if digest:
                    entry["image"] = {"reference": c["image"] + ":dev", "id": digest}
            components.append(entry)
        contracts = {ns: n["version"] for ns, n in sorted(self.registry["namespaces"].items()) if n.get("status") == "PUBLISHED"}
        return {"platform": "idp-telos", "release": "0.1.0-dev", "contracts": contracts, "components": components}

    @staticmethod
    def image_digest(ref):
        try:
            out = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", ref], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    # ---- compatibility matrix (T3) ----
    def pins(self, name):
        """(namespace, version, path) of contract copies a component pins (its own api/contracts excluded)."""
        repo = self.checkout(name)
        if repo is None:
            return None
        found = []
        for path in sorted(repo.rglob("schema.json")):
            rel = path.relative_to(repo)
            if SKIP_DIRS & set(rel.parts) or rel.parts[:2] == ("api", "contracts"):
                continue
            m = PIN.search(rel.as_posix())
            if m:
                found.append((m.group(1), m.group(2), rel.as_posix(), path))
        return found

    def matrix(self):
        rows = []
        for c in self.components:
            pins = self.pins(c["name"])
            if pins is None:
                rows.append({"consumer": c["name"], "status": "NOT_RUN (no checkout)"})
                continue
            pinned_namespaces = set()
            for namespace, version, rel, path in pins:
                pinned_namespaces.add(namespace)
                rows.append(self.judge(c["name"], namespace, version, rel, path))
            for namespace in c["contracts"]["consumes"]:
                if namespace not in pinned_namespaces:
                    rows.append({"consumer": c["name"], "namespace": namespace, "pinned": None,
                                 "registered": (self.registered(namespace) or {}).get("version"),
                                 "status": "UNPINNED", "note": "consumed without a pinned contract copy; no consumer-side contract test"})
        return {"rule": "compatible = same major, registered >= pinned, the pinned version's schema is in idp-contracts "
                        "(so tools/validate.py proves every later minor additive) and the consumer's copy is byte-identical to it",
                "rows": rows}

    def judge(self, consumer, namespace, version, rel, path):
        row = {"consumer": consumer, "namespace": namespace, "pinned": version, "copy": rel}
        n = self.registered(namespace)
        if not n:
            return {**row, "status": "INCOMPATIBLE", "note": "namespace not registered"}
        row["registered"] = n["version"]
        files = self.registered_files(namespace)
        if version not in files or not (self.root / "idp-contracts" / files[version]).exists():
            return {**row, "status": "INCOMPATIBLE", "note": "pinned version is not in idp-contracts"}
        if version_tuple(version)[0] != version_tuple(n["version"])[0]:
            return {**row, "status": "INCOMPATIBLE", "note": "different major version"}
        if version_tuple(version) > version_tuple(n["version"]):
            return {**row, "status": "INCOMPATIBLE", "note": "pinned version is newer than the registered one"}
        registered_bytes = (self.root / "idp-contracts" / files[version]).read_bytes()
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(registered_bytes).digest():
            return {**row, "status": "INCOMPATIBLE", "note": "the consumer's copy differs from the registered file of that version"}
        lag = "current" if version == n["version"] else f"behind ({version} -> {n['version']}, additive)"
        return {**row, "status": "COMPATIBLE", "note": lag}

    # ---- check (T4) ----
    def check(self, bom_doc, matrix_doc, allow_missing=()):
        problems, not_run = [], []
        names = {c["name"] for c in self.components} | {"idp-telos-go"}
        for c in self.components:
            p = c["contracts"]["produces"]
            if p and not self.registered(p):
                problems.append(f"{c['name']}: produces {p}, which is not registered")
            for ns in c["contracts"]["consumes"]:
                if not self.registered(ns):
                    problems.append(f"{c['name']}: consumes {ns}, which is not registered")
        producers = {c["contracts"]["produces"] for c in self.components if c["contracts"]["produces"]}
        for ns in self.registry["namespaces"]:
            if ns not in producers:
                problems.append(f"registered namespace {ns} has no producer in the catalog")
        ids = set()
        for i in self.integrations:
            if i["id"] in ids:
                problems.append(f"integration {i['id']} listed twice")
            ids.add(i["id"])
            for end in (i["from"], i["to"]):
                if end not in names:
                    problems.append(f"integration {i['id']}: {end} is not a catalogued component")
            if i.get("contract") and not self.registered(i["contract"]):
                problems.append(f"integration {i['id']}: contract {i['contract']} is not registered")
            if not i.get("evidence"):
                problems.append(f"integration {i['id']}: no evidence")
        owners = {}
        for o in self.ownership:
            if o["owner"] not in names:
                problems.append(f"ownership: {o['owner']} is not a catalogued component")
            for kind in (k.strip() for k in o["data"].split(",")):
                if kind in owners and owners[kind] != o["owner"]:
                    problems.append(f"ownership: {kind} has two owners ({owners[kind]}, {o['owner']})")
                owners[kind] = o["owner"]
        # the BOM pins the platform: checkouts must be at its commits, contracts as registered
        if bom_doc.get("contracts") != {ns: n["version"] for ns, n in sorted(self.registry["namespaces"].items()) if n.get("status") == "PUBLISHED"}:
            problems.append("platform-bom.yaml: contract versions differ from idp-contracts (run tools/platform.py bom)")
        for entry in bom_doc.get("components", []):
            repo = self.checkout(entry["name"])
            if repo is None:
                not_run.append(f"{entry['name']}: no checkout")
                continue
            if "commit" not in entry:
                problems.append(f"platform-bom.yaml: {entry['name']} has no commit")
                continue
            head = self.git(repo, "rev-parse", "HEAD")
            if head != entry["commit"]:
                problems.append(f"{entry['name']}: checkout at {head[:12]}, BOM pins {entry['commit'][:12]} (check out the BOM, or regenerate it)")
            if not entry.get("clean", False):
                problems.append(f"platform-bom.yaml: {entry['name']} was not clean when the BOM was made")
            want = entry.get("contract")
            if want and (self.registered(want["namespace"]) or {}).get("version") != want["version"]:
                problems.append(f"{entry['name']}: produces {want['namespace']} {want['version']}, registered is different")
        # the committed matrix must be what the checkouts say now, and have no incompatibility
        current = self.matrix()
        for row in current["rows"]:
            if str(row.get("status", "")).startswith("NOT_RUN"):
                not_run.append(f"matrix: {row['consumer']}: no checkout")
            elif row["status"] == "INCOMPATIBLE":
                problems.append(f"matrix: {row['consumer']} pins {row['namespace']} {row.get('pinned')}: {row['note']}")
        missing = {line.split(":")[0] for line in not_run} | {line.split(": ")[1] for line in not_run if line.startswith("matrix: ")}
        if not not_run:
            if matrix_doc != current:
                problems.append("compatibility-matrix.yaml is out of date (run tools/platform.py matrix)")
        else:  # compare the rows that could be computed
            keep = lambda rows: [r for r in rows if r["consumer"] not in missing]
            if keep(matrix_doc.get("rows", [])) != keep(current["rows"]):
                problems.append("compatibility-matrix.yaml is out of date (run tools/platform.py matrix)")
        allowed = [line for line in not_run if any(line.startswith(a + ":") or line.startswith("matrix: " + a + ":") for a in allow_missing)]
        return problems, [line for line in not_run if line not in allowed] + [f"{line} (allowed)" for line in allowed]


HEADERS = {
    "bom": "# Generated by tools/platform.py bom (ADR-0001 T3): the platform release = these component commits.\n"
           "# CI checks the components out at these commits; regenerate after moving a component.\n",
    "matrix": "# Generated by tools/platform.py matrix (ADR-0001 T3) from the contract copies each component pins.\n",
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["bom", "matrix", "check"])
    ap.add_argument("--root", type=Path, default=HERE)
    ap.add_argument("--no-docker", action="store_true", help="bom: do not record local image ids")
    ap.add_argument("--allow-missing", action="append", default=[], metavar="NAME",
                    help="check: a component that cannot be checked out here (e.g. a private repository in CI) is reported, not failed")
    args = ap.parse_args(argv)
    p = Platform(args.root)
    bom_path, matrix_path = args.root / "releases/platform-bom.yaml", args.root / "releases/compatibility-matrix.yaml"
    if args.command == "bom":
        dump_yaml(bom_path, HEADERS["bom"], p.bom(docker=not args.no_docker))
        print("wrote", bom_path)
        return 0
    if args.command == "matrix":
        dump_yaml(matrix_path, HEADERS["matrix"], p.matrix())
        print("wrote", matrix_path)
        return 0
    problems, not_run = p.check(load_yaml(bom_path), load_yaml(matrix_path), args.allow_missing)
    for line in problems:
        print("FAIL:", line)
    for line in not_run:
        print("NOT_RUN:", line)
    if problems:
        return 1
    if any(not line.endswith("(allowed)") for line in not_run):
        return 2
    print("PASSED: catalog, BOM and compatibility matrix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
