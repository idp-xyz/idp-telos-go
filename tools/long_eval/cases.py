"""Cases and their success conditions.

A case file is JSON: {"agent": "office-assistant", "cases": [{"id", "title", "turns": [{"say", "attach": [paths]}],
"success": {...}, "baseline": "recorded text"}]}. The files named by "attach" are read relative to the case file, so that the
business files stay next to it in .local and never reach the repository.

The success condition is one of two kinds of file requirement, written either per case or per turn:
  "delivered": [".xlsx"]            the case as a whole delivered a sane file of each kind (the last turn must have completed)
  "delivered_each_turn": [".xlsx"]  every turn completed and delivered a sane file of each kind
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

KINDS = {".xlsx", ".docx", ".pptx", ".pdf", ".md"}


class CaseError(ValueError):
    pass


@dataclass
class Turn:
    say: str
    attach: list = field(default_factory=list)  # absolute paths


@dataclass
class Case:
    id: str
    title: str
    turns: list
    success: dict
    baseline: str = ""


def _kinds(value, where: str) -> list:
    if not isinstance(value, list) or not value or any(not isinstance(k, str) for k in value):
        raise CaseError(f"{where}: a non-empty list of file kinds, such as [\".xlsx\"]")
    bad = [k for k in value if k.lower() not in KINDS]
    if bad:
        raise CaseError(f"{where}: unknown file kinds {bad}; known: {sorted(KINDS)}")
    return [k.lower() for k in value]


def parse(doc: dict, base: Path) -> tuple[str, list]:
    """(agent name, cases) from a decoded case file; base is the directory the attachments are relative to."""
    if not isinstance(doc, dict) or not isinstance(doc.get("cases"), list) or not doc["cases"]:
        raise CaseError("the case file needs a non-empty \"cases\" list")
    cases, seen = [], set()
    for raw in doc["cases"]:
        cid = raw.get("id") if isinstance(raw, dict) else None
        # the id ends up in file names and in the key of a result line, where "|" separates the parts
        if not isinstance(cid, str) or not cid or any(c in cid for c in "|/\\ "):
            raise CaseError(f"a case needs an id without spaces, slashes or '|': {cid!r}")
        if cid in seen:
            raise CaseError(f"case id {cid} appears twice")
        seen.add(cid)
        turns = []
        if not isinstance(raw.get("turns"), list) or not raw["turns"]:
            raise CaseError(f"{cid}: turns must be a non-empty list")
        for i, t in enumerate(raw["turns"], 1):
            if not isinstance(t, dict) or not isinstance(t.get("say"), str) or not t["say"].strip():
                raise CaseError(f"{cid} turn {i}: \"say\" is the member's words and cannot be empty")
            attach = t.get("attach", [])
            if not isinstance(attach, list) or any(not isinstance(a, str) for a in attach):
                raise CaseError(f"{cid} turn {i}: \"attach\" is a list of paths")
            turns.append(Turn(t["say"], [str((base / a).resolve()) for a in attach]))
        success = raw.get("success") or {}
        if not isinstance(success, dict):
            raise CaseError(f"{cid}: success must be an object")
        if "delivered" in success and "delivered_each_turn" in success:
            raise CaseError(f"{cid}: success names \"delivered\" and \"delivered_each_turn\"; choose one")
        for key in ("delivered", "delivered_each_turn"):
            if key in success:
                success[key] = _kinds(success[key], f"{cid} success.{key}")
        cases.append(Case(cid, str(raw.get("title", "")), turns, success, str(raw.get("baseline", ""))))
    return str(doc.get("agent", "")), cases


def load(path, only=None) -> tuple[str, list]:
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CaseError(f"cannot read the case file {p}: {e}")
    agent, cases = parse(doc, p.resolve().parent)
    if only:
        missing = [c for c in only if c not in {x.id for x in cases}]
        if missing:
            raise CaseError(f"no such case: {', '.join(missing)}; the file has {', '.join(x.id for x in cases)}")
        cases = [c for c in cases if c.id in only]
    return agent, cases


def missing_files(cases) -> list:
    """Attachments that do not exist, so that a long evaluation fails before its first run and not in its fourth hour."""
    return sorted({a for c in cases for t in c.turns for a in t.attach if not Path(a).is_file()})


def _sane_kinds(files) -> set:
    return {Path(f["name"]).suffix.lower() for f in files if (f.get("sanity") or {}).get("ok") is True}


def turn_success(spec: dict, status: str, files: list):
    """Whether one turn on its own meets a per-turn condition; None when the condition is per case or there is none."""
    need = spec.get("delivered_each_turn")
    if need is None:
        return None
    return status == "COMPLETED" and set(need) <= _sane_kinds(files)


def rep_success(spec: dict, turns_total: int, records: list):
    """Whether one repetition (all its turn records, in order) meets the case's condition; None when the case has none."""
    if not spec:
        return None
    if len(records) < turns_total:
        return False
    if "delivered_each_turn" in spec:
        return all(r["status"] == "COMPLETED" and set(spec["delivered_each_turn"]) <= _sane_kinds(r["files"]) for r in records)
    if "delivered" in spec:
        got = set()
        for r in records:
            got |= _sane_kinds(r["files"])
        return records[-1]["status"] == "COMPLETED" and set(spec["delivered"]) <= got
    return None
