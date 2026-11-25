#!/usr/bin/env python3
"""The mirror of the third-party images (releases/mirror-images.yaml): the list, the references that must follow it, the tags a
developer machine gets. Standard library only.

  check  [--repos DIR...] [--strict]   validate the list; with --repos also: references in those repositories that are not in
                                       the list, references that still name the SOURCE of a listed image (they must name the
                                       mirror: run `switch`), references to the mirror that are not in the list or pin another
                                       digest, list entries that nothing references, references that cannot be checked
  switch --repos DIR... [--dry-run]    rewrite every source reference of a listed image to its mirror name in place, keeping a
                                       digest pin (a pin that is not the listed digest becomes the listed one: only that one is copied)
  plan   --repos DIR...                every occurrence: repository, file, line, current reference -> mirror reference
  retag  [--dry-run]                   tag the images already in the local Docker daemon with their mirror names (never pulls)
  resolve [--via HOST...] [--verify]   fill every sha256:TODO from the registry (crane digest), or, with --via, through registry
                                       mirrors of which at least --min (2) must agree: the entry is then marked resolvedVia: mirrors;
                                       --verify: compare all digests with the registries' current ones, changes nothing
  aliases                              alias TAB digest, one line per alias (for the workflow)
  list                                 one line per entry for the workflow: source@digest TAB target:tag TAB tag (tab separated)

Two markers in a file say that a line is intentionally not a plain mirror reference (on the line or on the line before it):
  mirror:upstream   the line names the SOURCE of an image on purpose (documentation of what to watch upstream, an example of the rule,
                    an image that is deliberately not mirrored); it is not checked
  mirror:variable   the line builds a reference from variables (or hands one on) that was reviewed by hand; it is not reported as unchecked

Naming rule (no exceptions): every mirrored image is a TAG of the one package ghcr.io/idp-xyz/mirror (one package, so one
visibility switch for all of them). The source is written in full, host first; the tag encodes it:
    <host>__<path components joined by __>__<source tag>
e.g. public.ecr.aws/docker/library/postgres:17-alpine -> ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine
(docker.io/library/... for Docker official images, never a short name). Tags use [A-Za-z0-9_.-], at most 128 characters; `check`
refuses a list in which two entries encode to the same tag. The list keeps the full source next to the tag: nobody decodes.
The scope (which registries are mirrored, and why) is written in releases/mirror-images.yaml and in README.md.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "releases" / "mirror-images.yaml"
TARGET_REPO = "ghcr.io/idp-xyz/mirror"
MAX_TAG = 128
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TODO_DIGEST = "sha256:TODO"   # a digest not resolved yet: check and the workflow fail on it
# registries whose images are third party and that this tool knows by host; a reference to any other host is not recognised
KNOWN_HOSTS = ("public.ecr.aws", "docker.io", "quay.io", "ghcr.io", "registry.k8s.io", "k8s.gcr.io", "gcr.io", "mcr.microsoft.com",
               "registry-1.docker.io", "index.docker.io")
HOST_ALIASES = {"registry-1.docker.io": "docker.io", "index.docker.io": "docker.io"}
# our own images (built here, or published by platform.yml) are never mirrored
OWN_PREFIXES = ("idp-", "eer-", "local/")
OWN_OWNERS = ("idp-xyz", "idpxyz")
OWN_NAMES = ("scratch",)
# registries that are never ours and never mirrored: the developer's own fallback mirrors in idp-eer's local demo scripts
IGNORED_HOSTS = ("docker.m.daocloud.io", "docker.1ms.run")


# ---------------------------------------------------------------- references

@dataclass(frozen=True)
class Ref:
    host: str
    path: str
    tag: str = ""
    digest: str = ""

    @property
    def repo(self) -> str:
        return f"{self.host}/{self.path}"

    @property
    def key(self) -> str:
        """host/path:tag, the identity of a list entry."""
        return f"{self.repo}:{self.tag or 'latest'}"

    def text(self) -> str:
        return self.repo + (f":{self.tag}" if self.tag else "") + (f"@{self.digest}" if self.digest else "")


def parse_ref(s: str) -> Ref | None:
    """Parse a fully literal image reference; None when it is not one."""
    s = s.strip()
    digest = ""
    if "@" in s:
        s, digest = s.split("@", 1)
        if not DIGEST_RE.match(digest):
            return None
    tag = ""
    last = s.rsplit("/", 1)[-1]
    if ":" in last:
        s, tag = s.rsplit(":", 1)
        if not re.fullmatch(r"[\w][\w.-]{0,127}", tag):
            return None
    parts = s.split("/")
    if not all(re.fullmatch(r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*", p) or (i == 0 and re.fullmatch(r"[a-z0-9.-]+(:\d+)?", p))
               for i, p in enumerate(parts)):
        return None
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        host, path = parts[0], "/".join(parts[1:])
    else:
        host, path = "docker.io", "/".join(parts)
        if not parts[0][:1].isalpha():       # 65534:65534 (a user), 7233:7233 (ports) are not images
            return None
    host = HOST_ALIASES.get(host, host)
    if host == "docker.io" and "/" not in path:
        path = "library/" + path
    if not path or path.endswith("/"):
        return None
    return Ref(host, path, tag, digest)


def is_own(ref: Ref) -> bool:
    name = ref.path.rsplit("/", 1)[-1] if ref.host == "docker.io" and ref.path.startswith("library/") else ref.path
    if ref.host == "ghcr.io" and ref.path.split("/")[0] in OWN_OWNERS:
        return True
    if ref.host == "docker.io" and ref.path.startswith("library/") and name in OWN_NAMES:
        return True
    return ref.host == "docker.io" and any(name.startswith(p) for p in OWN_PREFIXES)


def encode_tag(ref: Ref, tag: str | None = None) -> str:
    """The naming rule: host, path components and source tag joined by __ (a tag cannot hold a /)."""
    return "__".join([ref.host, *ref.path.split("/"), tag if tag is not None else (ref.tag or "latest")])


def target_of(ref: Ref, tag: str | None = None) -> str:
    return f"{TARGET_REPO}:{encode_tag(ref, tag)}"


# ---------------------------------------------------------------- the list

@dataclass
class Entry:
    source: str            # host/path@sha256:digest
    tag: str
    target: str
    aliases: list[str] = field(default_factory=list)
    line: int = 0
    resolved_via: str = ""   # "mirrors": the digest was looked up through registry mirrors, not yet verified by a run on GitHub

    @property
    def todo(self) -> bool:
        return self.source.endswith("@" + TODO_DIGEST)

    @property
    def ref(self) -> Ref | None:
        r = parse_ref(self.source[: -len(TODO_DIGEST) - 1]) if self.todo else parse_ref(self.source)
        return Ref(r.host, r.path, self.tag, TODO_DIGEST if self.todo else r.digest) if r else None


def _scalar(v: str) -> str:
    v = re.sub(r"\s+#.*$", "", v).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def parse_list(text: str) -> list[Entry]:
    """The small YAML subset the list uses: `images:` then `- key: value` mappings, `aliases: [a, b]` inline, # comments."""
    entries: list[Entry] = []
    cur: dict | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("images:"):
            continue
        m = re.match(r"^\s*(- )?([A-Za-z_]+):\s*(.*)$", line)
        if not m:
            raise ValueError(f"line {n}: cannot read {raw!r}")
        dash, key, val = m.groups()
        if dash:
            cur = {"_line": n}
            entries.append(cur)  # type: ignore[arg-type]
        if cur is None:
            raise ValueError(f"line {n}: a key outside an entry")
        val = val.strip()
        if val.startswith("["):
            inner = re.sub(r"\s+#.*$", "", val).strip()
            if not inner.endswith("]"):
                raise ValueError(f"line {n}: an inline list must close on its line")
            cur[key] = [_scalar(x) for x in inner[1:-1].split(",") if x.strip()]
        else:
            cur[key] = _scalar(val)
    out = []
    for d in entries:
        d = dict(d)  # type: ignore[arg-type]
        unknown = set(d) - {"source", "tag", "target", "aliases", "resolvedVia", "_line"}
        if unknown:
            raise ValueError(f"line {d['_line']}: unknown key {sorted(unknown)}")
        out.append(Entry(d.get("source", ""), d.get("tag", ""), d.get("target", ""), list(d.get("aliases", [])), d["_line"], d.get("resolvedVia", "")))
    return out


def load_list(path: Path = LIST) -> list[Entry]:
    return parse_list(path.read_text())


def validate(entries: list[Entry]) -> list[str]:
    errors: list[str] = []
    seen_keys: dict[str, int] = {}
    seen_targets: dict[str, int] = {}
    seen_alias: dict[str, int] = {}
    if not entries:
        errors.append("the list is empty")
    for e in entries:
        where = f"entry at line {e.line} ({e.source or '?'})"
        if e.todo:
            errors.append(f"TODO: {where}: the digest is not resolved (python3 tools/mirror_images.py resolve)")
        sr = parse_ref(e.source[: -len(TODO_DIGEST) - 1] if e.todo else e.source)
        r = e.ref
        if sr is None or r is None or sr.tag or not (e.todo or sr.digest):
            errors.append(f"{where}: source must be host/path@sha256:<64 hex>, without a tag")
            continue
        if r.host not in KNOWN_HOSTS or r.host in HOST_ALIASES:
            errors.append(f"{where}: source host {r.host} is not one of {', '.join(h for h in KNOWN_HOSTS if h not in HOST_ALIASES)}")
        if is_own(r):
            errors.append(f"{where}: our own image is never mirrored")
        if not re.fullmatch(r"[\w][\w.-]{0,127}", e.tag or ""):
            errors.append(f"{where}: tag {e.tag!r} is not a tag")
            continue
        want = target_of(r, e.tag)
        if len(encode_tag(r, e.tag)) > MAX_TAG or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", encode_tag(r, e.tag)):
            errors.append(f"{where}: the encoded tag {encode_tag(r, e.tag)} is longer than {MAX_TAG} characters or outside [A-Za-z0-9_.-]")
        if e.resolved_via not in ("", "mirrors"):
            errors.append(f"{where}: resolvedVia must be 'mirrors' or absent")
        if e.target != want:
            errors.append(f"{where}: target is {e.target or '(missing)'}, the naming rule gives {want}")
        key = f"{r.repo}:{e.tag}"
        if key in seen_keys:
            errors.append(f"{where}: duplicate of line {seen_keys[key]}")
        seen_keys[key] = e.line
        if e.target in seen_targets and seen_targets[e.target] != e.line:
            errors.append(f"{where}: encodes to the same tag as the entry at line {seen_targets[e.target]} ({e.target})")
        seen_targets[e.target] = e.line
        for a in e.aliases:
            ar = parse_ref(a)
            if ar is None or ar.digest or not ar.tag:
                errors.append(f"{where}: alias {a!r} must be a literal reference with a tag, no digest")
            elif ar.tag != e.tag:
                errors.append(f"{where}: alias {a} has another tag than the entry")
            elif ar.key in seen_alias or ar.key in seen_keys:
                errors.append(f"{where}: alias {a} is already used")
            else:
                seen_alias[ar.key] = e.line
    return errors


# ---------------------------------------------------------------- finding references in the repositories

@dataclass
class Found:
    repo: str          # repository directory name
    file: str
    line: int
    text: str          # as written (after variable expansion)
    ref: Ref | None    # None: cannot be checked
    why: str = ""
    marker: str = ""   # "upstream" or "variable": the line (or the one before) carries mirror:upstream / mirror:variable


SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".jar", ".lock",
                 ".sum", ".svg", ".webp", ".mp4", ".wasm", ".bin", ".so", ".pyc")
SKIP_FILES = {"releases/mirror-images.yaml", "tools/mirror_images.py", "tools/tests/test_mirror_images.py"}
# an image reference as a token: optional registry, path, optional tag, optional digest; $VAR / ${VAR:-x} allowed inside
TOKEN = r"[A-Za-z0-9_./$:{}@+-]+"
VAR_RE = re.compile(r"\$\{(\w+)(?::?[-=?]([^}]*))?\}|\$(\w+)")
ENV_RE = re.compile(r"(?:^|\s)(?:export\s+|ARG\s+|ENV\s+)?([A-Za-z_]\w*)\s*(?:\?=|:=|=)\s*[\"']?([^\s\"'`]+)")
IMAGE_VAR = re.compile(r"[A-Z][A-Z0-9_]*(?:IMAGE|IMG|BASE)\b")
# a bare (no registry) reference with a slash and a version-like tag, or any reference with a digest
BARE_G = re.compile(r"(?<![\w./$:-])([a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)+(?::[0-9v][\w.-]*)?(?:@sha256:[0-9a-f]{64})?)(?![\w/$:{-])")
QUALIFIED = re.compile(r"(?<![\w./-])((?:" + "|".join(re.escape(h) for h in KNOWN_HOSTS) + r")/[\w./$:{}@-]+)")
CMD_RE = re.compile(r"(?:docker\s+(?:image\s+)?(?:run|pull|create)\b|pull[_-]image\.sh|ensure_image|crane\s+(?:pull|digest|copy|manifest)|--image(?:=|\s+)|kind\s+create)")
STRONG_UNRESOLVED = re.compile(r"(?:pull[_-]image\.sh|ensure_image)\s+(.*)")


def _marker(lines: list[str], ln: int) -> str:
    """mirror:upstream / mirror:variable on line ln (1-based) or on the line before it."""
    for i in (ln - 1, ln - 2):
        if 0 <= i < len(lines):
            m = re.search(r"mirror:(upstream|variable)\b", lines[i])
            if m:
                return m.group(1)
    return ""


def expand(s: str, env: dict[str, str]) -> str:
    """Expand $VAR, ${VAR} and ${VAR:-default} with the file's own assignments; unknown ones stay as written."""
    for _ in range(4):
        def sub(m: re.Match) -> str:
            name = m.group(1) or m.group(3)
            default = m.group(2)
            if name in env and env[name]:
                return env[name]
            if default is not None and m.group(1):
                return default
            return m.group(0)
        n = VAR_RE.sub(sub, s)
        if n == s:
            break
        s = n
    return s


def _classify(text: str) -> tuple[Ref | None, str]:
    """(ref, why): a literal third-party reference, or (None, why) when it cannot be checked, or (None, '') when it is not one."""
    t = text.strip().strip("\"'`,;()[]").rstrip("\\")
    if not t or t.startswith(("-", "/", ".", "~")) or "://" in t:
        return None, ""
    if "$" in t or "{" in t:
        return None, "built from a variable"
    r = parse_ref(t)
    if r is not None and r.repo == TARGET_REPO:
        return r, ""             # a reference to the mirror itself: checked against the list, never an error to have
    if r is None or is_own(r) or r.host in IGNORED_HOSTS:
        return None, ""
    return r, ""


def _is_text(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        if path.stat().st_size > 2_000_000:
            return False
        with path.open("rb") as f:
            return b"\0" not in f.read(8192)
    except OSError:
        return False


def list_files(repo: Path) -> list[str]:
    if (repo / ".git").exists():
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True).stdout
        return sorted(f for f in out.decode().split("\0") if f)
    files = []
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in (".git", "node_modules", "__pycache__") and not (Path(dp, d, ".git")).exists()]
        files += [str(Path(dp, f).relative_to(repo)) for f in fns]
    return sorted(files)


def short_names(entries: list["Entry"]) -> list[str]:
    """The Docker Hub / ECR library spellings of the listed images that are written without a registry (python:3.12-slim, prom/prometheus:v3):
    nothing in a file says they are images, so they are looked for by name."""
    out: set[str] = set()
    for e in entries:
        for r in [e.ref] + [parse_ref(a) for a in e.aliases]:
            if r is None or not r.tag:
                continue
            path = r.path
            if r.host == "public.ecr.aws" and path.startswith("docker/library/"):
                out.add(f"{path[len('docker/library/'):]}:{r.tag}")
            elif r.host == "docker.io":
                out.add(f"{path[len('library/'):] if path.startswith('library/') else path}:{r.tag}")
    return sorted(out)


def scan_text(repo: str, rel: str, text: str, shorts: list[str] | None = None) -> list[Found]:
    """Every third-party image reference in one file."""
    found: dict[tuple[int, str], Found] = {}
    is_doc = rel.endswith((".md", ".txt", ".rst"))
    is_go = rel.endswith((".go", ".sql"))
    base = rel.rsplit("/", 1)[-1]
    is_dockerfile = base.startswith("Dockerfile") or base.endswith(".Dockerfile") or base == "Containerfile"
    lines = text.splitlines()
    env: dict[str, str] = {}
    for raw in lines:
        if raw.lstrip().startswith("#"):
            continue
        for m in ENV_RE.finditer(raw):
            if "$" not in m.group(2):
                env.setdefault(m.group(1), m.group(2))
    arg_line: dict[str, int] = {}
    stages: set[str] = set()

    def add(n: int, raw_text: str, strong: bool, from_var: str = "", at_line: int | None = None) -> None:
        shown = expand(raw_text, env)
        ref, why = _classify(shown)
        if ref is None and not why:
            return
        if ref is None and not strong:
            return
        if ref is not None and not strong and not (ref.tag or ref.digest):
            return
        ln = at_line or n
        found.setdefault((ln, shown), Found(repo, rel, ln, shown, ref, why, _marker(lines, ln)))

    short_re = re.compile(r"(?<![\w./$-])(" + "|".join(re.escape(x) for x in shorts) + r")(?![\w.-])") if shorts and not is_doc else None
    for n, raw in enumerate(lines, 1):
        line = re.sub(r"\s+#.*$", "", raw) if not is_doc else raw
        if short_re is not None and not raw.lstrip().startswith(("#", "//")) and not re.match(r"\s*(?:-\s*)?image:", line):
            for sm in short_re.finditer(line):
                add(n, sm.group(1), True)
        if is_dockerfile:
            m = re.match(r"^\s*ARG\s+(\w+)=(\S+)", line)
            if m:
                arg_line[m.group(1)] = n
            m = re.match(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
            if m:
                src, stage = m.group(1), m.group(2)
                if src not in stages:
                    mv = re.fullmatch(r"\$\{?(\w+)\}?", src)
                    if mv and mv.group(1) in arg_line:
                        add(n, env.get(mv.group(1), src), True, at_line=arg_line[mv.group(1)])
                    else:
                        add(n, src, True)
                if stage:
                    stages.add(stage)
                continue
        if is_go:
            for q in QUALIFIED.findall(line):
                add(n, q.rstrip(".,"), False)
            continue
        # image: key (YAML, compose, Kubernetes manifests, GitHub workflow services)
        m = re.match(r"^\s*(?:-\s*)?image:\s*[\"']?(\S+?)[\"']?\s*$", line)
        if m and not is_doc:
            add(n, m.group(1), True)
            continue
        # variables named ...IMAGE/IMG/BASE, and their ${NAME:-default}
        for m in re.finditer(r"(" + IMAGE_VAR.pattern + r")\s*(?:\?=|:=|=|:)\s*[\"']?(" + TOKEN + ")", line):
            if not is_doc and not m.group(2).startswith("$"):
                add(n, m.group(2), False)
        for m in re.finditer(r"\$\{" + IMAGE_VAR.pattern + r":?-([^}]+)\}", line):
            if not is_doc:
                add(n, m.group(1), False)
        if not is_doc:
            for cm in CMD_RE.finditer(line):
                rest = re.split(r"\s*(?:\||;|&&|\|\|)\s*", line[cm.end():])[0]
                for tok in rest.split():
                    tok = tok.strip("\"'")
                    r, why = _classify(tok)
                    strong = bool(STRONG_UNRESOLVED.search(line)) and cm.group(0).startswith(("pull", "ensure"))
                    if r is not None and (r.tag or r.digest):
                        add(n, tok, False)
                    elif why and strong and VAR_RE.fullmatch(tok):
                        add(n, tok, True)
            for m in BARE_G.finditer(line):
                g = m.group(1)
                if "@" in g or re.search(r":[0-9v]", g):
                    add(n, g, False)
        # a registry-qualified reference anywhere (also in documentation: only with a tag or digest there)
        for q in QUALIFIED.findall(line):
            q = q.rstrip(".,;:)")
            if is_doc and not re.search(r":[\w][\w.-]*$|@sha256:", q):
                continue
            if is_doc and re.search(r"[<>*]|vX|\.\.\.", q):
                continue
            if q.endswith(("/", "-")):
                continue
            add(n, q, not is_doc)
    return sorted(found.values(), key=lambda f: (f.line, f.text))


def scan_repo(repo: Path, shorts: list[str] | None = None) -> list[Found]:
    out: list[Found] = []
    name = repo.resolve().name
    for rel in list_files(repo):
        if rel in SKIP_FILES or not _is_text(repo / rel):
            continue
        try:
            text = (repo / rel).read_text(errors="replace")
        except OSError:
            continue
        out += scan_text(name, rel, text, shorts)
    return out


def index_entries(entries: list[Entry]) -> dict[str, Entry]:
    idx: dict[str, Entry] = {}
    for e in entries:
        r = e.ref
        if r:
            idx[r.key] = e
        for a in e.aliases:
            ar = parse_ref(a)
            if ar:
                idx[ar.key] = e
    return idx


def match(ref: Ref, idx: dict[str, Entry]) -> tuple[Entry | None, str]:
    e = idx.get(ref.key)
    if e is None and ref.tag == "" and ref.digest:
        # pinned by digest only: any entry of that repository with that digest
        for x in idx.values():
            r = x.ref
            if r and r.repo == ref.repo and r.digest == ref.digest:
                return x, ""
        return None, "pinned by a digest that is not in the list"
    if e is None:
        return None, "not in the list"
    if ref.digest and e.ref and ref.digest != e.ref.digest:
        return e, f"pinned to {ref.digest[:19]}..., the list has {e.ref.digest[:19]}..."
    return e, ""


def mirror_text(ref: Ref, e: Entry) -> str:
    return e.target + (f"@{ref.digest}" if ref.digest else "")


def target_index(entries: list[Entry]) -> dict[str, Entry]:
    return {e.target: e for e in entries}


def cmd_check(args: argparse.Namespace) -> int:
    entries = load_list(Path(args.list))
    errors = validate(entries)
    for e in errors:
        print(f"ERROR: {e}")
    code = 1 if errors else 0
    print(f"list: {len(entries)} entries, {'invalid' if errors else 'valid'}")
    if not args.repos:
        return code
    idx = index_entries(entries)
    shorts = short_names(entries)
    targets = target_index(entries)
    referenced: set[int] = set()
    missing: dict[str, list[Found]] = {}
    mismatched: list[tuple[Found, str]] = []
    unswitched: list[Found] = []
    unchecked: list[Found] = []
    total = mirrored = upstream = reviewed = 0
    for d in args.repos:
        for f in scan_repo(Path(d), shorts):
            total += 1
            if f.ref is not None and f.ref.repo == TARGET_REPO and not (f.ref.tag or f.ref.digest):
                total -= 1               # the package's name in a sentence, not an image
                continue
            if f.marker == "variable" and (f.ref is None or f.ref.repo == TARGET_REPO and f.ref.tag.endswith("__")):
                reviewed += 1            # built from variables, reviewed by hand
                continue
            if f.ref is not None and f.ref.repo == TARGET_REPO:
                e = targets.get(f"{TARGET_REPO}:{f.ref.tag}")
                if e is None:
                    missing.setdefault(f.ref.text(), []).append(f)
                    continue
                referenced.add(id(e))
                if f.ref.digest and e.ref and f.ref.digest != e.ref.digest:
                    mismatched.append((f, f"pinned to {f.ref.digest[:19]}..., the list has {e.ref.digest[:19]}..."))
                else:
                    mirrored += 1
                continue
            if f.marker == "upstream":
                upstream += 1
                if f.ref is not None:
                    e, _ = match(f.ref, idx)
                    if e is not None:
                        referenced.add(id(e))
                continue
            if f.ref is None:
                if f.marker == "variable":
                    reviewed += 1
                else:
                    unchecked.append(f)
                continue
            e, why = match(f.ref, idx)
            if e is not None:
                referenced.add(id(e))
            if e is None:
                missing.setdefault(f.ref.text(), []).append(f)
            elif why:
                mismatched.append((f, why))
            else:
                unswitched.append(f)
    print(f"references: {total} found in {len(args.repos)} repositories: {mirrored} name the mirror, {upstream} name an upstream on purpose "
          f"(mirror:upstream), {reviewed} are built from variables and reviewed (mirror:variable)")
    for ref, fs in sorted(missing.items()):
        print(f"NOT IN THE LIST: {ref}  ({len(fs)}x, first {fs[0].repo}:{fs[0].file}:{fs[0].line})")
        code = 1
    for f, why in mismatched:
        print(f"DIFFERS FROM THE LIST: {f.repo}:{f.file}:{f.line} {f.text}: {why}")
        code = 1
    for f in unswitched:
        print(f"NOT THE MIRROR: {f.repo}:{f.file}:{f.line} {f.text} -> {mirror_text(f.ref, match(f.ref, idx)[0])}  (python3 tools/mirror_images.py switch --repos <dirs>)")
        code = 1
    unused = [e for e in entries if id(e) not in referenced]
    for e in unused:
        print(f"{'UNUSED' if args.strict else 'note: unused in these repositories'}: {e.target} (source {e.source.split('@')[0]}:{e.tag})")
    if unused and args.strict:
        code = 1
    for f in unchecked:
        print(f"{'UNCHECKED' if args.strict else 'note: cannot be checked'}: {f.repo}:{f.file}:{f.line} {f.text} ({f.why})  [review it and put mirror:variable on the line]")
    if unchecked and args.strict:
        code = 1
    print("OK" if code == 0 else "FAIL")
    return code


def cmd_switch(args: argparse.Namespace) -> int:
    entries = load_list(Path(args.list))
    idx = index_entries(entries)
    shorts = short_names(entries)
    changes = manual = 0
    for d in args.repos:
        repo = Path(d)
        by_file: dict[str, list[Found]] = {}
        for f in scan_repo(repo, shorts):
            if f.ref is None or f.ref.repo == TARGET_REPO or f.marker == "upstream":
                continue
            e, why = match(f.ref, idx)
            if e is None or (why and "pinned to" not in why):
                continue
            by_file.setdefault(f.file, []).append(f)
        for rel, fs in sorted(by_file.items()):
            lines = (repo / rel).read_text().splitlines(keepends=True)
            for f in fs:
                e, why = match(f.ref, idx)
                new = e.target + (f"@{e.ref.digest}" if f.ref.digest else "")
                pat = re.compile(r"(?<![\w./$])(?<!\w-)" + re.escape(f.text.rstrip(".,;")) + r"(?![\w/$:{-])")
                line = lines[f.line - 1]
                out, n = pat.subn(lambda _m: new, line)
                if n == 0:
                    if f.ref.repo + (":" + f.ref.tag if f.ref.tag else "") in line or "mirror" in line:
                        continue
                    print(f"MANUAL {f.repo}:{rel}:{f.line}: {f.text} is not written as such on the line")
                    manual += 1
                    continue
                lines[f.line - 1] = out
                changes += n
                print(f"{'WOULD ' if args.dry_run else ''}SWITCH {f.repo}:{rel}:{f.line} {f.text} -> {new}" + (f"  [{why}]" if why else ""))
            if not args.dry_run:
                (repo / rel).write_text("".join(lines))
    print(f"{changes} {'would be ' if args.dry_run else ''}switched, {manual} to do by hand")
    return 1 if manual else 0


def plan_rows(entries: list[Entry], repos: list[str]) -> list[list[str]]:
    idx = index_entries(entries)
    shorts = short_names(entries)
    rows = []
    for d in repos:
        for f in scan_repo(Path(d), shorts):
            if f.marker == "upstream":
                rows.append([f.repo, f.file, str(f.line), f.text, "STAYS (mirror:upstream)"])
                continue
            if f.ref is None:
                rows.append([f.repo, f.file, str(f.line), f.text, ("REVIEWED (mirror:variable): " if f.marker else "CANNOT BE CHECKED: ") + f.why])
                continue
            if f.ref.repo == TARGET_REPO:
                rows.append([f.repo, f.file, str(f.line), f.ref.text(), "(already the mirror)"])
                continue
            e, why = match(f.ref, idx)
            to = mirror_text(f.ref, e) if e else f"NOT IN THE LIST ({why})"
            if e and why:
                to += f"  [{why}]"
            rows.append([f.repo, f.file, str(f.line), f.ref.text(), to])
    return rows


def cmd_plan(args: argparse.Namespace) -> int:
    entries = load_list(Path(args.list))
    rows = plan_rows(entries, args.repos)
    head = ["repository", "file", "line", "current", "mirror"]
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(5)] if rows else [10] * 5
    for r in [head] + rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[0]] = counts.get(r[0], 0) + 1
    print("\noccurrences per repository: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"; total {len(rows)}")
    return 0


def local_names(e: Entry) -> list[str]:
    r = e.ref
    assert r
    # `docker image inspect` normalises docker.io/library/x:t and x:t to the same image, so one spelling covers both
    return [r.text().split("@")[0]] + list(e.aliases)


def cmd_retag(args: argparse.Namespace) -> int:
    entries = load_list(Path(args.list))
    errors = [x for x in validate(entries) if not x.startswith("TODO:")]
    if errors:
        print("\n".join(f"ERROR: {x}" for x in errors))
        return 1

    def docker(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *a], capture_output=True, text=True)

    try:
        if docker("version", "--format", "{{.Server.Version}}").returncode != 0:
            print("no Docker daemon: nothing to tag", file=sys.stderr)
            return 1
    except FileNotFoundError:
        print("docker is not installed: nothing to tag", file=sys.stderr)
        return 1
    tagged = 0
    for e in entries:
        r = e.ref
        assert r
        done = False
        for name in local_names(e):
            ins = docker("image", "inspect", name, "--format", "{{json .RepoDigests}}")
            if ins.returncode != 0:
                continue
            digests = [d for d in re.findall(r"[^\",\[\]]+@(sha256:[0-9a-f]{64})", ins.stdout)]
            own_repo = [d for d in re.findall(r"([^\",\[\]]+)@(sha256:[0-9a-f]{64})", ins.stdout)]
            note = ""
            if own_repo and not e.todo and r.digest not in digests:
                # a pull of one platform, a docker load, or an older pull of a moved tag: not provably the listed image
                note = f"  (note: local digest {digests[0][:19]}..., the list has {r.digest[:19]}...)"
            if args.dry_run:
                print(f"WOULD  docker tag {name} {e.target}{note}")
            else:
                t = docker("tag", name, e.target)
                if t.returncode != 0:
                    print(f"FAILED docker tag {name} {e.target}: {t.stderr.strip()}")
                    continue
                print(f"TAGGED {name} -> {e.target}{note}")
            tagged += 1
            done = True
            break
        if not done:
            print(f"ABSENT {r.repo}:{e.tag} (not in the local daemon; not pulled)")
    print(f"{tagged} {'would be ' if args.dry_run else ''}tagged, {len(entries) - tagged} absent")
    return 0


def crane_digest(name: str, tries: int = 3) -> str:
    err = ""
    for i in range(tries):
        r = subprocess.run(["crane", "digest", name], capture_output=True, text=True)
        if r.returncode == 0 and DIGEST_RE.match(r.stdout.strip()):
            return r.stdout.strip()
        err = (r.stderr or r.stdout).strip().splitlines()[-1:] and (r.stderr or r.stdout).strip().splitlines()[-1] or "no answer"
        if i + 1 < tries:
            time.sleep(float(os.environ.get("IDP_MIRROR_WAIT", "5")) * (i + 1))
    raise RuntimeError(err)


def mirror_digest(repo: str, tag: str, via: list[str], need: int) -> tuple[str, dict[str, str]]:
    """The digest that at least `need` of the registry mirrors agree on; raises when there is none."""
    path = repo.split("/", 1)[1]
    answers: dict[str, str] = {}
    for host in via:
        try:
            answers[host] = crane_digest(f"{host}/{path}:{tag}")
        except (RuntimeError, FileNotFoundError):
            continue
    counts: dict[str, int] = {}
    for d in answers.values():
        counts[d] = counts.get(d, 0) + 1
    best = max(counts.items(), key=lambda kv: kv[1], default=("", 0))
    if best[1] < need or len(counts) > 1:
        raise RuntimeError(f"mirrors do not agree (need {need}, got {answers or 'no answer'})")
    return best[0], answers


def cmd_resolve(args: argparse.Namespace) -> int:
    path = Path(args.list)
    lines = path.read_text().splitlines(keepends=True)
    entries = parse_list("".join(lines))
    code = 0
    # work from the last entry up, so that inserting a line does not move the lines of the entries still to do
    for e in reversed(entries):
        if not (e.todo or args.verify):
            continue
        repo = e.source.split("@")[0]
        try:
            if args.via and not args.verify:
                d, answers = mirror_digest(repo, e.tag, args.via, args.min)
            else:
                d, answers = crane_digest(f"{repo}:{e.tag}"), {}
        except (RuntimeError, FileNotFoundError) as x:
            print(f"UNRESOLVED {repo}:{e.tag}: {x}")
            code = 1
            continue
        if args.verify:
            have = e.source.split("@")[1]
            print(f"{'same   ' if d == have else 'DIFFERS'} {repo}:{e.tag} registry {d} list {have}")
            code |= d != have
            continue
        i = e.line - 1
        lines[i] = lines[i].replace(e.source, f"{repo}@{d}")
        if args.via:
            j = next(k for k in range(i, len(lines)) if lines[k].lstrip().startswith("target:"))
            lines.insert(j + 1, "    resolvedVia: mirrors\n")
        print(f"RESOLVED {repo}:{e.tag} {d}" + (f" (via {', '.join(answers)})" if answers else ""))
    if not args.verify:
        path.write_text("".join(lines))
    return code


def cmd_aliases(args: argparse.Namespace) -> int:
    for e in load_list(Path(args.list)):
        for a in e.aliases:
            print(f"{a}\t{e.source.split('@')[1]}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    entries = load_list(Path(args.list))
    errors = validate(entries)
    if errors:
        print("\n".join(f"ERROR: {x}" for x in errors), file=sys.stderr)
        return 1
    for e in entries:
        print(f"{e.source}\t{e.target}\t{e.tag}\t{e.resolved_via or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--list", default=str(LIST), help="the list (default releases/mirror-images.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--repos", nargs="+", metavar="DIR")
    c.add_argument("--strict", action="store_true", help="unused entries and references that cannot be checked also fail")
    c.set_defaults(fn=cmd_check)
    sw = sub.add_parser("switch")
    sw.add_argument("--repos", nargs="+", required=True, metavar="DIR")
    sw.add_argument("--dry-run", action="store_true")
    sw.set_defaults(fn=cmd_switch)
    pl = sub.add_parser("plan")
    pl.add_argument("--repos", nargs="+", required=True, metavar="DIR")
    pl.set_defaults(fn=cmd_plan)
    r = sub.add_parser("retag")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_retag)
    rs = sub.add_parser("resolve")
    rs.add_argument("--verify", action="store_true")
    rs.add_argument("--via", nargs="+", metavar="HOST", help="look the digests up through these registry mirrors (Docker Hub sources)")
    rs.add_argument("--min", type=int, default=2, help="how many mirrors must answer, all with the same digest (default 2)")
    rs.set_defaults(fn=cmd_resolve)
    sub.add_parser("aliases").set_defaults(fn=cmd_aliases)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
