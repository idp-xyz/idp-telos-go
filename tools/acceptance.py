#!/usr/bin/env python3
"""The tiered platform acceptance: the path manifest (tests/paths.yaml), the selection of paths, the check of a log, the timings.

  acceptance.py select --changed <comp,...|auto|all> [--base REF] [--root DIR]   the ordered paths to run, one line, space separated
  acceptance.py select --t3                                                      the paths that need real embeddings, plus the ones flagged t3
  acceptance.py select --paths "P2 P3"                                           explicit paths, plus their needs and the smoke path
  acceptance.py verify --log FILE --paths "A B ..."|all [--embed double|real]    every selected path's PASS ids are in the log; no FAIL, no BLOCKED
  acceptance.py validate --paths "A B ..."                                       the ids exist (an unset PATHS is all of them)
  acceptance.py record --timings FILE --id P --start S --end E                   one path's start and end (epoch seconds) into the JSON file
  acceptance.py report --timings FILE [--mode compose|kind] [--embed ...]        the table of seconds against budgets, WARN lines, the total (never fails)

`select --changed auto` derives the changed set from the working tree against --base (default origin/main, or its merge base with HEAD):
  * releases/platform-bom.yaml: the components whose commit or image id differs from the base's file are the changed components;
  * a path script (the manifest's `script:`) or a file in a path's `files:` selects that path;
  * deploy/keycloak/**, deploy/compose/platform.yaml, deploy/compose/e2e.sh, deploy/compose/lib_acceptance.sh, tools/acceptance.py and
    tests/paths.yaml select every path; so does any other file under deploy/compose/ that no path claims (it could belong to any), except the
    manual tools p_only.sh and github_live.sh;
  * anything else (docs, kind files, workflows, other tools) selects nothing by itself: the kind script has its own trigger (kind.yml).
The smoke path (the manifest's `smoke`, PATH A) is always selected, and so is every path a selected path `needs`, transitively.
Exit codes: 0 ok, 1 a check failed (verify), 2 a bad argument, manifest or repository.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ALL = object()   # "every component": select(m, ALL) is every path
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "tests/paths.yaml"
ID_RE = re.compile(r"^[A-Za-z0-9]+$")
EMBED_MODES = ("double", "real")

# what selects every path (see the module docstring)
GLOBAL_ALL = [
    "deploy/keycloak/**",
    "deploy/compose/platform.yaml",
    "deploy/compose/e2e.sh",
    "deploy/compose/lib_acceptance.sh",
    "tools/acceptance.py",
    "tests/paths.yaml",
]
COMPOSE_MANUAL_TOOLS = ["deploy/compose/p_only.sh", "deploy/compose/github_live.sh", "deploy/compose/__pycache__/**"]


class ManifestError(Exception):
    pass


class SelectionError(Exception):
    pass


@dataclass
class Path_:
    id: str
    pass_ids: list
    components: list
    needs: list
    budget: int
    real_embedding: list = field(default_factory=list)
    real_budget: int | None = None
    kind: bool = False
    kind_budget: int | None = None
    kind_extras: list = field(default_factory=list)
    script: str | None = None
    files: list = field(default_factory=list)
    t3: bool = False
    uses: list = field(default_factory=list)
    erases: list = field(default_factory=list)
    notes: str = ""


@dataclass
class Manifest:
    paths: list
    components: list
    smoke: list
    kind_order: list = field(default_factory=list)

    @property
    def ids(self):
        return [p.id for p in self.paths]

    def path(self, pid):
        for p in self.paths:
            if p.id == pid:
                return p
        raise SelectionError(f"unknown path {pid!r}")


def _list(d, key, where, required=False):
    v = d.get(key, None)
    if v is None:
        if required:
            raise ManifestError(f"{where}: {key} is required")
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ManifestError(f"{where}: {key} must be a list of strings")
    return list(v)


def _budget(d, key, where, required=False):
    v = d.get(key)
    if v is None:
        if required:
            raise ManifestError(f"{where}: {key} must be a positive integer")
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ManifestError(f"{where}: {key} must be a positive integer, not {v!r}")
    return v


def parse_manifest(data) -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError("the manifest must be a mapping")
    comps = _list(data, "components", "manifest", required=True)
    raw = data.get("paths")
    if not isinstance(raw, list) or not raw:
        raise ManifestError("manifest: paths must be a non-empty list")
    paths, seen, pass_seen = [], set(), {}
    for i, d in enumerate(raw):
        if not isinstance(d, dict):
            raise ManifestError(f"paths[{i}] must be a mapping")
        pid = d.get("id")
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ManifestError(f"paths[{i}]: id {pid!r} must be letters and digits only")
        if pid in seen:
            raise ManifestError(f"duplicate path id {pid}")
        seen.add(pid)
        where = f"path {pid}"
        pass_ids = _list(d, "pass", where, required=True)
        if not pass_ids:
            raise ManifestError(f"{where}: pass must list at least one PASS id")
        for x in pass_ids:
            if not ID_RE.match(x):
                raise ManifestError(f"{where}: PASS id {x!r} must be letters and digits only")
            if x in pass_seen:
                raise ManifestError(f"duplicate PASS id {x} ({pass_seen[x]} and {pid})")
            pass_seen[x] = pid
        real = _list(d, "realEmbedding", where)
        if not set(real) <= set(pass_ids):
            raise ManifestError(f"{where}: realEmbedding {sorted(set(real) - set(pass_ids))} is not among the path's PASS ids")
        cs = _list(d, "components", where, required=True)
        for c in cs:
            if c not in comps:
                raise ManifestError(f"{where}: unknown component {c!r}")
        paths.append(Path_(
            id=pid, pass_ids=pass_ids, components=cs, needs=_list(d, "needs", where), budget=_budget(d, "budgetSeconds", where, True),
            real_embedding=real, real_budget=_budget(d, "realBudgetSeconds", where), kind=bool(d.get("kind", False)),
            kind_budget=_budget(d, "kindBudgetSeconds", where), kind_extras=_list(d, "kindExtras", where), script=d.get("script"),
            files=_list(d, "files", where), t3=bool(d.get("t3", False)), uses=_list(d, "uses", where), erases=_list(d, "erases", where),
            notes=str(d.get("notes", ""))))
    index = {p.id: i for i, p in enumerate(paths)}
    for p in paths:
        for n in p.needs:
            if n == p.id:
                raise ManifestError(f"path {p.id} needs itself")
            if n not in index:
                raise ManifestError(f"path {p.id} needs an unknown path {n}")
    # cycles first (a cycle is also an order violation, but the cycle is the better message)
    state = {}

    def visit(pid, stack):
        if state.get(pid) == 2:
            return
        if state.get(pid) == 1:
            raise ManifestError("cycle in needs: " + " -> ".join(stack[stack.index(pid):] + [pid]))
        state[pid] = 1
        for n in paths[index[pid]].needs:
            visit(n, stack + [pid])
        state[pid] = 2
    for p in paths:
        visit(p.id, [])
    for p in paths:
        for n in p.needs:
            if index[n] >= index[p.id]:
                raise ManifestError(f"path {p.id} needs {n}, which must come before it in the manifest")
    smoke = _list(data, "smoke", "manifest") or [paths[0].id]
    for s in smoke:
        if s not in index:
            raise ManifestError(f"smoke path {s} is not in the manifest")
    # a subject that a path erases is gone for every later path: none may use it (bob after P0, carol after P2)
    gone = {}
    for p in paths:
        for u in p.uses:
            if u in gone and gone[u] != p.id:
                raise ManifestError(f"path {p.id} uses {u}, whom path {gone[u]} erased before it")
        for e in p.erases:
            gone.setdefault(e, p.id)
    ko = _list(data, "kindOrder", "manifest")
    for k in ko:
        if k not in index:
            raise ManifestError(f"kindOrder: unknown path {k}")
    return Manifest(paths=paths, components=comps, smoke=smoke, kind_order=ko)


def load_manifest(path=DEFAULT_MANIFEST) -> Manifest:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ManifestError(f"{path}: {e}") from e
    return parse_manifest(data)


# ---------- selection ----------

def closure(m: Manifest, ids) -> list:
    index = {p.id: p for p in m.paths}
    want, todo = set(), list(ids)
    for i in todo:
        if i not in index:
            raise SelectionError(f"unknown path {i!r}")
    while todo:
        i = todo.pop()
        if i in want:
            continue
        want.add(i)
        todo.extend(index[i].needs)
    return [p.id for p in m.paths if p.id in want]


def select_paths(m: Manifest, ids, smoke=True) -> list:
    ids = [i.strip() for i in ids if i.strip()]
    return closure(m, list(ids) + (list(m.smoke) if smoke else []))


def select(m: Manifest, changed) -> list:
    if changed is ALL:
        return list(m.ids)
    changed = {c.strip() for c in changed if c.strip()}
    for c in sorted(changed):
        if c not in m.components:
            raise SelectionError(f"unknown component {c!r} (known: {', '.join(m.components)})")
    return closure(m, [p.id for p in m.paths if changed & set(p.components)] + list(m.smoke))


def select_t3(m: Manifest) -> list:
    return closure(m, [p.id for p in m.paths if p.real_embedding or p.t3] + list(m.smoke))


def _glob(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"; i += 3
        elif pattern.startswith("**", i):
            out += ".*"; i += 2
        elif pattern[i] == "*":
            out += "[^/]*"; i += 1
        elif pattern[i] == "?":
            out += "[^/]"; i += 1
        else:
            out += re.escape(pattern[i]); i += 1
    return re.compile("^" + out + "$")


def _match(path, patterns):
    return any(_glob(p).match(path) for p in patterns)


@dataclass
class Classification:
    paths: set
    everything: bool
    reasons: list


def classify_files(m: Manifest, files) -> Classification:
    paths, everything, reasons = set(), False, []
    scripts = {p.script: p.id for p in m.paths if p.script}
    for raw in files:
        f = raw.strip().replace("\\", "/")
        if not f:
            continue
        if _match(f, GLOBAL_ALL):
            everything = True; reasons.append(f"{f}: everything"); continue
        if f in scripts:
            paths.add(scripts[f]); reasons.append(f"{f}: PATH {scripts[f]}"); continue
        claimed = [p.id for p in m.paths if _match(f, p.files)]
        if claimed:
            paths.update(claimed); reasons.append(f"{f}: PATH {' '.join(claimed)}"); continue
        if _match(f, COMPOSE_MANUAL_TOOLS):
            continue
        if f.startswith("deploy/compose/"):
            everything = True; reasons.append(f"{f}: unclaimed, everything"); continue
    return Classification(paths, everything, reasons)


def bom_changed_components(old_text, new_text) -> set:
    def comps(text):
        if text is None:
            return {}
        try:
            d = yaml.safe_load(text) or {}
            return {c["name"]: (c.get("commit"), (c.get("image") or {}).get("id")) for c in (d.get("components") or [])}
        except (yaml.YAMLError, AttributeError, KeyError, TypeError) as e:
            raise SelectionError(f"the BOM is not readable: {e}") from e
    a, b = comps(old_text), comps(new_text)
    return {n for n in set(a) | set(b) if a.get(n) != b.get(n)}


def auto_selection(m: Manifest, files, old_bom, new_bom) -> list:
    c = classify_files(m, files)
    if c.everything:
        return list(m.ids)
    comps = {n for n in bom_changed_components(old_bom, new_bom) if n in m.components}
    return closure(m, set(select(m, comps)) | c.paths | set(m.smoke))


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r


def auto_from_git(m: Manifest, root, base) -> list:
    if git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        raise SelectionError(f"the base {base!r} is not a commit of {root}")
    mb = git(root, "merge-base", base, "HEAD")
    ref = mb.stdout.strip() if mb.returncode == 0 and mb.stdout.strip() else base
    diff = git(root, "diff", "--name-only", ref)
    if diff.returncode != 0:
        raise SelectionError(f"git diff against {ref} failed: {diff.stderr.strip()}")
    files = diff.stdout.splitlines() + git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    old = git(root, "show", f"{ref}:releases/platform-bom.yaml")
    bom = Path(root) / "releases/platform-bom.yaml"
    new = bom.read_text(encoding="utf-8") if bom.exists() else None
    return auto_selection(m, files, old.stdout if old.returncode == 0 else None, new)


# ---------- verification ----------

def verify_log(m: Manifest, log: str, paths, embed="double") -> list:
    if embed not in EMBED_MODES:
        raise SelectionError(f"embed must be one of {EMBED_MODES}, not {embed!r}")
    paths = [p.strip() for p in paths if p.strip()]
    if not paths:
        raise SelectionError("an empty selection: there is nothing to verify")
    for p in paths:
        m.path(p)
    lines = log.replace("\r\n", "\n").split("\n")
    problems = [f"{kind} line: {l[:160]}" for kind in ("FAIL", "BLOCKED") for l in lines if l.startswith(kind + ":")]
    for pid in paths:
        p = m.path(pid)
        for x in p.pass_ids:
            need_pass = re.compile(r"^PASS: PATH %s(?![A-Za-z0-9])" % re.escape(x))
            ok = any(need_pass.match(l) for l in lines)
            if not ok and embed == "double" and x in p.real_embedding:
                nr = re.compile(r"^NOT_RUN: PATH %s(?![A-Za-z0-9])" % re.escape(x))
                ok = any(nr.match(l) for l in lines)
            if not ok:
                problems.append(f"PATH {x} (of {pid}) did not pass: no line '^PASS: PATH {x}'" + (" or NOT_RUN" if embed == "double" and x in p.real_embedding else ""))
    return problems


# ---------- timings ----------

def record_timing(file, pid, start, end, status="ok"):
    start, end = float(start), float(end)
    if end < start:
        raise SelectionError(f"path {pid}: it ended ({end}) before it started ({start})")
    f = Path(file)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
            raise ValueError
    except (OSError, ValueError):
        data = {"paths": {}}
    data["paths"][pid] = {"start": start, "end": end, "seconds": round(end - start, 3), "status": status}
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, f)


def _budget_for(p: Path_, mode, embed):
    if mode == "kind":
        return p.kind_budget
    if embed == "real" and p.real_budget:
        return p.real_budget
    return p.budget


def timings_report(m: Manifest, timings: dict, mode="compose", embed="double"):
    rows = timings.get("paths", {}) if isinstance(timings, dict) else {}
    order = [i for i in m.ids if i in rows] + sorted(i for i in rows if i not in m.ids and i != "_run")
    lines = [f"{'PATH':<6}{'seconds':>8}{'budget':>8}  note"]
    warns, total = [], 0.0
    for pid in order:
        r = rows[pid]
        sec = float(r.get("seconds", 0))
        total += sec
        p = next((x for x in m.paths if x.id == pid), None)
        b = _budget_for(p, mode, embed) if p else None
        note = r.get("status", "ok") if r.get("status", "ok") != "ok" else ""
        if b is not None and sec > b:
            warns.append(f"WARN: PATH {pid} took {int(round(sec))}s (budget {b}s)")
            note = (note + " over budget").strip()
        lines.append(f"{pid:<6}{int(round(sec)):>8}{(str(b) if b is not None else '-'):>8}  {note}".rstrip())
    lines.append(f"{'total':<6}{int(round(total)):>8}{'':>8}  (the paths; setup and teardown not included)")
    if "_run" in rows:
        lines.append(f"{'run':<6}{int(round(float(rows['_run'].get('seconds', 0)))):>8}{'':>8}  (the whole script, setup included)")
    return "\n" + "\n".join(lines) + "\n", warns


# ---------- command line ----------

def _ids(text):
    return [x for x in re.split(r"[\s,]+", text or "") if x]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="acceptance.py", description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", default=os.environ.get("ACCEPTANCE_MANIFEST", str(DEFAULT_MANIFEST)))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--changed")
    s.add_argument("--base", default="origin/main")
    s.add_argument("--root", default=".")
    s.add_argument("--t3", action="store_true")
    s.add_argument("--paths")
    s.add_argument("--format", choices=["space", "lines", "csv"], default="space")
    v = sub.add_parser("verify")
    v.add_argument("--log", required=True)
    v.add_argument("--paths", required=True)
    v.add_argument("--embed", default=os.environ.get("EMBED") or "double")
    va = sub.add_parser("validate")
    va.add_argument("--paths", default="")
    r = sub.add_parser("record")
    r.add_argument("--timings", required=True)
    r.add_argument("--id", required=True)
    r.add_argument("--start", required=True)
    r.add_argument("--end", required=True)
    r.add_argument("--status", default="ok")
    rp = sub.add_parser("report")
    rp.add_argument("--timings", required=True)
    rp.add_argument("--mode", choices=["compose", "kind"], default="compose")
    rp.add_argument("--embed", default=os.environ.get("EMBED") or "double")
    a = ap.parse_args(argv)
    try:
        m = load_manifest(a.manifest)
        if a.cmd == "select":
            if a.t3:
                out = select_t3(m)
            elif a.paths is not None:
                out = select_paths(m, _ids(a.paths))
            elif a.changed is None:
                raise SelectionError("select needs --changed, --t3 or --paths")
            elif a.changed == "all":
                out = select(m, ALL)
            elif a.changed == "auto":
                out = auto_from_git(m, a.root, a.base)
            else:
                out = select(m, _ids(a.changed))
            print({"space": " ", "lines": "\n", "csv": ","}[a.format].join(out))
            return 0
        if a.cmd == "validate":
            for i in _ids(a.paths):
                m.path(i)
            return 0
        if a.cmd == "verify":
            paths = m.ids if a.paths.strip() == "all" else _ids(a.paths)
            try:
                log = Path(a.log).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                raise SelectionError(f"cannot read the log: {e}") from e
            problems = verify_log(m, log, paths, embed=a.embed)
            for p in problems:
                print(f"FAIL: {p}", file=sys.stderr)
            if not problems:
                print(f"verified: {len(paths)} paths ({' '.join(paths)})")
            return 1 if problems else 0
        if a.cmd == "record":
            record_timing(a.timings, a.id, a.start, a.end, a.status)
            return 0
        if a.cmd == "report":
            try:
                t = json.loads(Path(a.timings).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                t = {"paths": {}}
            text, warns = timings_report(m, t, mode=a.mode, embed=a.embed)
            print(text, end="")
            for w in warns:
                print(w)
            return 0
    except (ManifestError, SelectionError) as e:
        print(f"acceptance.py: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
