"""E4-PDF (ADR-0003 appendix D, task V3): about 30 public PDFs kept in .local/web-eval/e4-pdf (never in this
repository, so that no third-party document is republished), each annotated by hand with passages a correct reading
must contain, a scorer for any command that reads a PDF, and adversarial files built here byte by byte.

  python3 -m tools.web_eval.e4pdf fetch ID URL        fetch URL as web_fetch does (address check on every hop,
                                                      robots.txt, 5 MiB, 15 s, the same user agent); keep the bytes
  python3 -m tools.web_eval.e4pdf verify [--write]    look every passage up in two reference extractors
  python3 -m tools.web_eval.e4pdf check               the annotations are well formed, on their pages, the split holds
  python3 -m tools.web_eval.e4pdf split [--write]     the development / held-out split (a rule, no results)
  python3 -m tools.web_eval.e4pdf table               the set, one document a line
  python3 -m tools.web_eval.e4pdf refused             the addresses refused while choosing, and by whose doing
  python3 -m tools.web_eval.e4pdf score --cmd CMD     run CMD once per document (the PDF on stdin), score what it prints
  python3 -m tools.web_eval.e4pdf adversarial --out D write the synthetic hostile files; with score --adversarial D
                                                      each must end within the limits with a reason, never text

Data (all under --root, default .local/web-eval/e4-pdf, or $E4PDF_ROOT):
  docs/<id>.pdf, docs/<id>.json   the bytes and where/when they came from
  ann.jsonl                       one document a line:
      {"id", "lang": "zh"|"en", "category", "traits": [...], "pages", "scanned": bool, "passages": [...], "notes"}
      passage: {"text", "page", "kind": "text"|"table", "cells": [...] (table only), "ref": "both"|"one"|"none",
                "reread": true (a passage no reference found, read again on its page and kept)}
  split.json, refused.jsonl, verify.json, held-out-runs.log

Command protocol (score): the PDF on stdin; exit 0 and the text on stdout, or a non-zero exit and one line on stderr
that starts with a reason word (no_text_layer, encrypted, too_many_pages, malformed, too_slow, too_large). Other
stderr lines (warnings, logs) may come before it: the first line that starts with a reason word counts.
"""
import argparse
import hashlib
import json
import os
import re
import resource
import shlex
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import zlib
from pathlib import Path

from . import fetch

DEFAULT_ROOT = ".local/web-eval/e4-pdf"
REASONS = ("no_text_layer", "encrypted", "too_many_pages", "malformed", "too_slow", "too_large")
PAGE_LIMIT = 200          # ADR-0003 4.3: pages read; passages on later pages are "beyond", reported, not required
TIME_LIMIT = 5.0          # seconds per document (plan, E4-PDF row)
MEM_LIMIT_MIB = 256       # MiB per document (plan, E4-PDF row)
AS_LIMIT_MIB = 8192       # safety net only (RLIMIT_AS); not the criterion, see run_command
KEEP_TARGET = 0.95        # plan: passages kept >= 95%
TABLE_GAP = 60            # table cells: the next cell must start within this many characters of the previous one
OUT_CAP = 64 << 20        # more than this on stdout is cut and counts as a failure
CATEGORIES = {"customs", "tariff", "carrier", "regulation", "standard", "company", "paper", "notice", "legal", "other"}


def root(args=None) -> Path:
    return Path(getattr(args, "root", None) or os.environ.get("E4PDF_ROOT") or DEFAULT_ROOT)


# ---------------------------------------------------------------------------------------------------- matching

_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef\U00020000-\U0002fa1f]")
_INVISIBLE = re.compile(r"\u00ad\s*|[\u200b-\u200d\u2060\ufeff]")  # a soft hyphen, and the line break after it, vanish
_LINE_END_HYPHEN = re.compile(r"(?<=[^\W\d_])[-\u2010]\s*\n\s*(?=[^\W\d_])")
_HYPHENS = re.compile(r"[-\u2010]")


_REASON = re.compile(r"(" + "|".join(REASONS) + r")\b")


def has_cjk(s: str) -> bool:
    return bool(_CJK.search(s))


def norm(s: str, cjk: bool) -> str:
    """Text as it is compared. In order: Unicode NFKC (ligatures, full-width forms, compatibility characters); soft
    hyphens and zero-width characters removed; a hyphen between two letters at the end of a line joins the line with
    the next ("cross-\\nborder" -> "crossborder"); every remaining hyphen deleted (the extractor may or may not keep
    the hyphen of a hyphenated word, and passage and text are both cut alike); then white space: cjk -> all removed
    (Chinese has none, and extractors invent some around Latin words and digits), otherwise collapsed to one space."""
    s = unicodedata.normalize("NFKC", s)
    s = _INVISIBLE.sub("", s)
    s = _LINE_END_HYPHEN.sub("", s)
    s = _HYPHENS.sub("", s)
    return re.sub(r"\s+", "", s) if cjk else re.sub(r"\s+", " ", s).strip()


class Text:
    """The text a command printed, normalised once for each mode."""

    def __init__(self, raw: str):
        self.raw = raw
        self._n: dict = {}

    def get(self, cjk: bool) -> str:
        if cjk not in self._n:
            self._n[cjk] = norm(self.raw, cjk)
        return self._n[cjk]


def _find_cells(t: str, cells: list, gap: int) -> bool:
    """cells in order, each starting within gap characters after the end of the previous one."""
    pos = t.find(cells[0])
    while pos != -1:
        end, ok = pos + len(cells[0]), True
        for c in cells[1:]:
            nxt = t.find(c, end, end + gap + len(c))
            if nxt == -1:
                ok = False
                break
            end = nxt + len(c)
        if ok:
            return True
        pos = t.find(cells[0], pos + 1)
    return False


def passage_found(text: Text, passage: dict, gap: int = TABLE_GAP) -> bool:
    """A text passage is a substring of the normalised text; a table passage is its cells, in order, each within
    `gap` characters of the one before (other columns' text may sit between them, a different row may not)."""
    if passage.get("kind") == "table" and passage.get("cells"):
        cjk = any(has_cjk(c) for c in passage["cells"])
        cells = [norm(c, cjk) for c in passage["cells"]]
        return all(cells) and _find_cells(text.get(cjk), cells, gap)
    cjk = has_cjk(passage["text"])
    p = norm(passage["text"], cjk)
    return bool(p) and p in text.get(cjk)


# ---------------------------------------------------------------------------------------------------- the split

def split_rule(langs: dict) -> dict:
    """{id: "dev" | "held"}. Rule, independent of any result: within each language, order the ids by the SHA-256 of
    the id; the first (n + 1) // 3 of them are held out (for 15 + 15 documents: 5 + 5)."""
    out = {}
    for lang in sorted(set(langs.values())):
        ids = sorted((i for i, l in langs.items() if l == lang), key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        k = (len(ids) + 1) // 3
        for n, i in enumerate(ids):
            out[i] = "held" if n < k else "dev"
    return out


def load_ann(r: Path) -> list:
    p = r / "ann.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def current_split(r: Path, ann: list) -> dict:
    return split_rule({d["id"]: d["lang"] for d in ann})


# ---------------------------------------------------------------------------------------------------- running a command

def _proc_group_rss_mib(pgid: int) -> float:
    """Resident set of every process in the process group (the command and what it started), in MiB. Reads /proc."""
    total = 0
    try:
        names = os.listdir("/proc")
    except OSError:
        return 0.0
    page = os.sysconf("SC_PAGE_SIZE")
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open(f"/proc/{n}/stat", "rb") as f:
                stat = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        rest = stat[stat.rfind(")") + 2:].split()  # fields after "(comm)": state ppid pgrp ...
        try:
            if int(rest[2]) == pgid:
                total += int(rest[21]) * page      # rss in pages (field 24 overall)
        except (IndexError, ValueError):
            continue
    return total / (1 << 20)


def run_command(cmd: str, data: bytes, time_limit: float = TIME_LIMIT, mem_limit_mib: float = MEM_LIMIT_MIB,
                as_limit_mib: int = AS_LIMIT_MIB) -> dict:
    """Run cmd (split like a shell would, no shell) with data on stdin. Returns
      {status: "text"|"reason"|"timeout"|"memory"|"crashed"|"bad_exit"|"cannot_start"|"output_too_large",
       text, reason, detail, seconds, peak_mib, exit}
    Wall clock: the clock runs from start to exit; at time_limit the whole process group is killed (status timeout).
    Memory: peak_mib is the larger of (a) the sum of the resident sets of the process group sampled every 20 ms and
    (b) the child's own ru_maxrss from wait4, which also covers the processes it reaped (so a short spike between
    two samples is still seen, afterwards). The group is killed at the first sample above mem_limit_mib (status
    memory); a run that only shows its peak in (b) is not killed, but its peak is over the limit and is reported.
    Limits of the measurement: resident memory, not virtual; a descendant that left the process group (setsid) or one
    that is not reaped before the command exits is not counted; the interpreter or runtime's own baseline is
    included (uv, python and pypdf together weigh tens of MiB). as_limit_mib is RLIMIT_AS, a safety net against a
    runaway allocation between two samples, not the criterion (0 turns it off)."""
    def pre():
        os.setsid()
        if as_limit_mib:
            resource.setrlimit(resource.RLIMIT_AS, (as_limit_mib << 20, as_limit_mib << 20))
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             preexec_fn=pre)
    except OSError as e:
        return {"status": "cannot_start", "text": "", "reason": "", "detail": str(e), "seconds": 0.0, "peak_mib": 0.0, "exit": None}
    out, err, state = bytearray(), bytearray(), {"over": False}

    def feed():
        try:
            p.stdin.write(data)
        except OSError:
            pass
        finally:
            try:
                p.stdin.close()
            except OSError:
                pass

    def drain(stream, buf, cap):
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if len(buf) < cap:
                buf += chunk[:cap - len(buf)]
            if len(buf) >= cap and cap == OUT_CAP:
                state["over"] = True

    threads = [threading.Thread(target=feed, daemon=True), threading.Thread(target=drain, args=(p.stdout, out, OUT_CAP), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, err, 1 << 16), daemon=True)]
    for t in threads:
        t.start()
    killed, peak_sample, ru = "", 0.0, None
    while True:
        pid, status, ru = os.wait4(p.pid, os.WNOHANG)
        if pid:
            break
        now = time.monotonic() - t0
        peak_sample = max(peak_sample, _proc_group_rss_mib(p.pid))
        if peak_sample > mem_limit_mib:
            killed = "memory"
        elif now > time_limit:
            killed = "timeout"
        elif state["over"]:
            killed = "output_too_large"
        if killed:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            _, status, ru = os.wait4(p.pid, 0)
            break
        time.sleep(0.02)
    seconds = time.monotonic() - t0
    try:
        os.killpg(p.pid, signal.SIGKILL)  # anything left behind
    except OSError:
        pass
    for t in threads:
        t.join(2)
    peak = max(peak_sample, ru.ru_maxrss / 1024.0)
    code = os.waitstatus_to_exitcode(status)
    p.returncode = code  # reaped by wait4 above, not by Popen
    for stream in (p.stdout, p.stderr):
        stream.close()
    text = out.decode("utf-8", "replace")
    lines = [l.strip() for l in err.decode("utf-8", "replace").splitlines() if l.strip()]
    # the reason is the first stderr line that starts with a reason word: libraries and runtimes write warnings first
    line = next((l for l in lines if _REASON.match(l)), lines[0] if lines else "")
    res = {"text": text, "reason": "", "detail": line[:120], "seconds": seconds, "peak_mib": peak, "exit": code, "status": "text"}
    if killed:
        res["status"] = killed
        res["text"] = ""
    elif code < 0:
        res["status"] = "crashed"
        res["detail"] = f"signal {-code}"
    elif code != 0:
        m = _REASON.match(line)
        res["status"], res["reason"] = ("reason", m.group(1)) if m else ("bad_exit", "")
        res["text"] = ""
    return res


# ---------------------------------------------------------------------------------------------------- scoring

def kept_passages(doc: dict, text: Text) -> dict:
    """Passages of a text document found in text, in three classes: scored (reference both/one, within the page
    limit), `none` (no reference extractor found them: reported apart), `beyond` (on a page after PAGE_LIMIT)."""
    out = {"scored": [0, 0], "none": [0, 0], "beyond": [0, 0], "missing": []}
    for p in doc["passages"]:
        cls = "beyond" if p["page"] > PAGE_LIMIT else ("none" if p.get("ref") == "none" else "scored")
        hit = passage_found(text, p)
        out[cls][0] += hit
        out[cls][1] += 1
        if not hit and cls != "beyond":
            out["missing"].append(p["text"][:40])
    return out


def score_document(doc: dict, res: dict, time_limit: float, mem_limit: float) -> dict:
    """The verdict on one document from one run (res from run_command)."""
    r = {"id": doc["id"], "lang": doc["lang"], "scanned": bool(doc.get("scanned")), "status": res["status"],
         "reason": res["reason"], "seconds": res["seconds"], "peak_mib": res["peak_mib"], "detail": res["detail"],
         "over_time": res["status"] == "timeout" or res["seconds"] > time_limit,
         "over_mem": res["status"] == "memory" or res["peak_mib"] > mem_limit}
    if doc.get("scanned"):
        r["correct_scan"] = res["status"] == "reason" and res["reason"] == "no_text_layer"
        r["kept"], r["of"] = 0, 0
        r["all_kept"] = None
        return r
    k = kept_passages(doc, Text(res["text"])) if res["status"] == "text" else \
        {"scored": [0, sum(1 for p in doc["passages"] if p["page"] <= PAGE_LIMIT and p.get("ref") != "none")],
         "none": [0, sum(1 for p in doc["passages"] if p.get("ref") == "none" and p["page"] <= PAGE_LIMIT)],
         "beyond": [0, 0], "missing": [p["text"][:40] for p in doc["passages"] if p["page"] <= PAGE_LIMIT]}
    r.update(kept=k["scored"][0], of=k["scored"][1], kept_none=k["none"][0], of_none=k["none"][1],
             beyond_read=k["beyond"][0], of_beyond=k["beyond"][1], missing=k["missing"])
    r["all_kept"] = r["status"] == "text" and r["kept"] == r["of"]
    return r


def totals(rows: list, include_none: bool = False) -> dict:
    text = [r for r in rows if not r["scanned"]]
    scans = [r for r in rows if r["scanned"]]
    kept = sum(r["kept"] + (r.get("kept_none", 0) if include_none else 0) for r in text)
    of = sum(r["of"] + (r.get("of_none", 0) if include_none else 0) for r in text)
    return {"docs": len(rows), "text_docs": len(text), "kept": kept, "of": of, "pct": 100.0 * kept / of if of else 0.0,
            "all_kept": sum(1 for r in text if r["all_kept"]), "scans": len(scans),
            "scans_correct": sum(1 for r in scans if r["correct_scan"]),
            "over_time": sum(r["over_time"] for r in rows), "over_mem": sum(r["over_mem"] for r in rows),
            "max_seconds": max((r["seconds"] for r in rows), default=0.0), "max_mib": max((r["peak_mib"] for r in rows), default=0.0),
            "kept_none": sum(r.get("kept_none", 0) for r in text), "of_none": sum(r.get("of_none", 0) for r in text),
            "beyond_read": sum(r.get("beyond_read", 0) for r in text), "of_beyond": sum(r.get("of_beyond", 0) for r in text),
            "failures": {s: sum(1 for r in rows if r["status"] == s) for s in
                         ("reason", "timeout", "memory", "crashed", "bad_exit", "cannot_start", "output_too_large")
                         if any(r["status"] == s for r in rows)}}


def format_rows(rows: list) -> str:
    lines = [f"{'id':<22}{'lang':<5}{'result':<34}{'time':>7}{'MiB':>7}  flags"]
    for r in rows:
        if r["scanned"]:
            res = "scan: no_text_layer (correct)" if r["correct_scan"] else f"scan: WRONG ({r['status']} {r['reason'] or r['detail'][:30]})"
        elif r["status"] == "text":
            res = f"{r['kept']}/{r['of']} kept" + (f" (+{r['kept_none']}/{r['of_none']} unverified)" if r["of_none"] else "")
        else:
            res = f"{r['status']} {r['reason']}".strip()
        flags = " ".join(f for f, on in (("OVER-TIME", r["over_time"]), ("OVER-MEM", r["over_mem"])) if on)
        lines.append(f"{r['id']:<22}{r['lang']:<5}{res:<34}{r['seconds']:>6.2f}s{r['peak_mib']:>7.0f}  {flags}")
        if not r["scanned"] and r["status"] == "text" and r.get("missing"):
            lines.append(f"{'':<27}missing: " + " | ".join(m[:28] for m in r["missing"]))
    return "\n".join(lines)


def format_totals(t: dict, label: str, time_limit: float, mem_limit: float) -> str:
    ok_pct = t["pct"] >= 100 * KEEP_TARGET
    return "\n".join([
        f"== {label}: {t['docs']} documents ({t['text_docs']} with a text layer, {t['scans']} scanned)",
        f"passages kept (scored, reference-verified): {t['kept']}/{t['of']} = {t['pct']:.1f}%   target >= {100 * KEEP_TARGET:.0f}%: {'met' if ok_pct else 'NOT met'}",
        f"documents with every scored passage kept: {t['all_kept']}/{t['text_docs']}",
        f"scanned documents correctly reported as no_text_layer: {t['scans_correct']}/{t['scans']}",
        f"over the time limit ({time_limit:g} s): {t['over_time']}   over the memory limit ({mem_limit:g} MiB): {t['over_mem']}"
        f"   slowest {t['max_seconds']:.2f} s, largest peak {t['max_mib']:.0f} MiB",
        f"passages no reference extractor found (not scored): kept {t['kept_none']}/{t['of_none']}",
        f"passages beyond page {PAGE_LIMIT} read anyway (informational): {t['beyond_read']}/{t['of_beyond']}",
        "runs that ended otherwise than with text: " + (", ".join(f"{k} {v}" for k, v in t["failures"].items()) or "none")])


HELD_BANNER = ("HELD-OUT SET: these documents are for a single final confirmation of the chosen reader. They are not "
               "to be used to choose between candidates or to tune one; every run is logged in held-out-runs.log.")


def score_set(args) -> int:
    r = root(args)
    ann = load_ann(r)
    if not ann:
        raise SystemExit(f"no annotations in {r}/ann.jsonl")
    sp = current_split(r, ann)
    stored = r / "split.json"
    if stored.exists() and json.loads(stored.read_text())["assignment"] != sp:
        raise SystemExit("split.json does not match the rule applied to ann.jsonl: the set changed after the split was fixed")
    want = "held" if args.held_out else "dev"
    docs = [d for d in ann if sp[d["id"]] == want and (not args.docs or d["id"] in args.docs.split(","))]
    if args.held_out:
        print(HELD_BANNER)
        with (r / "held-out-runs.log").open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {args.cmd!r} {len(docs)} documents\n")
    rows = []
    for d in docs:
        res = run_command(args.cmd, (r / "docs" / f"{d['id']}.pdf").read_bytes(), args.time_limit, args.mem_limit, args.as_limit)
        rows.append(score_document(d, res, args.time_limit, args.mem_limit))
    print(format_rows(rows))
    label = "held-out" if args.held_out else "development"
    for sub, name in ((None, label), ("zh", label + " zh"), ("en", label + " en")):
        part = [x for x in rows if sub is None or x["lang"] == sub]
        if part:
            print(format_totals(totals(part, args.include_none), name, args.time_limit, args.mem_limit))
    if args.json:
        Path(args.json).write_text(json.dumps({"cmd": args.cmd, "set": want, "rows": rows, "totals": totals(rows, args.include_none)},
                                              ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------------------------------------------- adversarial files

def _pdf(objects: dict, trailer_extra: str = "", header: bytes = b"%PDF-1.4\n", root_obj: int = 1) -> bytes:
    """A PDF from {number: body bytes}, with a correct xref table. Objects are written in numeric order."""
    out = bytearray(header)
    offs = {}
    for n in sorted(objects):
        offs[n] = len(out)
        out += b"%d 0 obj\n" % n + objects[n] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for n in range(1, size):
        out += (b"%010d 00000 n \n" % offs[n]) if n in offs else b"0000000000 65535 f \n"
    out += b"trailer\n<< /Size %d /Root %d 0 R %s >>\nstartxref\n%d\n%%%%EOF\n" % (size, root_obj, trailer_extra.encode(), xref)
    return bytes(out)


def _stream(body: bytes, dict_extra: str = "") -> bytes:
    return b"<< /Length %d %s >>\nstream\n" % (len(body), dict_extra.encode()) + body + b"\nendstream"


def _simple(content: bytes = b"0 0 m 10 10 l S") -> bytes:
    """One page, no text, nothing hostile: the base the others are broken from."""
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>",
                 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << >> >>",
                 4: _stream(content)})


def make_bomb(mib: int) -> bytes:
    """A page whose content stream is Flate data that inflates to `mib` MiB of spaces: a few hundred KiB on disk."""
    comp = zlib.compressobj(9)
    chunk = b" " * (1 << 20)
    body = b"".join(comp.compress(chunk) for _ in range(mib)) + comp.flush()
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>",
                 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << >> >>",
                 4: _stream(body, "/Filter /FlateDecode")})


def make_deep_pages(depth: int) -> bytes:
    """Pages nodes nested `depth` deep, one leaf page at the bottom."""
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    for n in range(2, 2 + depth):
        objs[n] = b"<< /Type /Pages /Kids [%d 0 R] /Count 1 /Parent %d 0 R >>" % (n + 1, n - 1 if n > 2 else 1)
    leaf = 2 + depth
    objs[leaf] = b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 200 200] /Resources << >> >>" % (leaf - 1)
    return _pdf(objs)


def make_page_cycle() -> bytes:
    """The root Pages node lists itself, and a second Pages node that lists the first, as kids."""
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>",
                 2: b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>",
                 3: b"<< /Type /Pages /Kids [2 0 R] /Count 1 /Parent 2 0 R >>"})


def make_many_pages(count: int) -> bytes:
    """A page tree of `count` kids (all the one page object) and /Count to match."""
    kids = b" ".join([b"3 0 R"] * count)
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>",
                 2: b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % count,
                 3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>"})


def make_circular_resources() -> bytes:
    """A font whose /Resources is the page's own resource dictionary, a form XObject that draws itself, and a
    resource dictionary whose /XObject points back at the dictionary that holds it."""
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>",
                 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources 5 0 R >>",
                 4: _stream(b"/X0 Do /F1 12 Tf"),
                 5: b"<< /Font << /F1 6 0 R >> /XObject << /X0 7 0 R >> /Self 5 0 R >>",
                 6: b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 10 10] /FontMatrix [1 0 0 1 0 0] /CharProcs 6 0 R /Encoding 6 0 R /Resources 5 0 R /FirstChar 0 /LastChar 0 /Widths [1] >>",
                 7: _stream(b"/X0 Do", "/Type /XObject /Subtype /Form /BBox [0 0 10 10] /Resources 5 0 R")})


def make_encrypted() -> bytes:
    """A document with an /Encrypt dictionary (standard handler, 40-bit RC4, made-up O and U, so that no password
    opens it) and one page whose content stream is therefore unreadable."""
    o, u = b"A" * 32, b"B" * 32
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>",
                 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << >> >>",
                 4: _stream(bytes(range(40))),
                 5: b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -44 /O <" + o.hex().encode() + b"> /U <" + u.hex().encode() + b"> >>"},
                trailer_extra="/Encrypt 5 0 R /ID [<00112233445566778899aabbccddeeff> <00112233445566778899aabbccddeeff>]")


def make_truncated() -> bytes:
    full = _simple(b"0 0 m 10 10 l S " * 50)
    return full[:len(full) // 2]


def make_no_xref() -> bytes:
    full = _simple()
    return full[:full.index(b"xref\n")]


def make_wrong_xref() -> bytes:
    full = _simple()
    wrong = re.sub(rb"(\d{10}) 00000 n", lambda m: b"%010d 00000 n" % (int(m.group(1)) + 37), full)
    return re.sub(rb"startxref\n\d+", b"startxref\n99999999", wrong)


def make_garbage_after_header(size: int = 1 << 20) -> bytes:
    x, out = 12345, bytearray(b"%PDF-1.7\n")
    while len(out) < size:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(x >> 16 & 0xFF)
    return bytes(out)


def make_html() -> bytes:
    return (b"<!doctype html><html><head><title>Not a PDF</title></head><body><h1>Error 404</h1>"
            b"<p>The page you requested has moved.</p></body></html>\n")


def make_bad_flate() -> bytes:
    body = zlib.compress(b"0 0 m 10 10 l S " * 100)
    body = body[:20] + bytes(b ^ 0xFF for b in body[20:60]) + body[60:]
    return _pdf({1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << >> >>",
                 4: _stream(body, "/Filter /FlateDecode")})


def make_length_lie() -> bytes:
    return _simple().replace(b"/Length 15", b"/Length 2147483000")


def make_xref_huge() -> bytes:
    full = _simple()
    return full.replace(b"/Size 5", b"/Size 2000000000").replace(b"xref\n0 5", b"xref\n0 2000000000")


DEFAULT_BOMB_MIB = 512
ADVERSARIAL = {
    "empty.pdf": lambda a: b"",
    "html-as-pdf.pdf": lambda a: make_html(),
    "garbage-after-header.pdf": lambda a: make_garbage_after_header(),
    "truncated.pdf": lambda a: make_truncated(),
    "xref-missing.pdf": lambda a: make_no_xref(),
    "xref-wrong.pdf": lambda a: make_wrong_xref(),
    "xref-huge-size.pdf": lambda a: make_xref_huge(),
    "stream-length-lie.pdf": lambda a: make_length_lie(),
    "flate-corrupt.pdf": lambda a: make_bad_flate(),
    "flate-bomb.pdf": lambda a: make_bomb(a.bomb_mib),
    "pages-deep.pdf": lambda a: make_deep_pages(a.depth),
    "pages-cycle.pdf": lambda a: make_page_cycle(),
    "pages-many.pdf": lambda a: make_many_pages(a.pages),
    "resources-circular.pdf": lambda a: make_circular_resources(),
    "encrypted.pdf": lambda a: make_encrypted(),
}
# What a good reader says; informational (any reason word passes the adversarial check)
PREFERRED = {"empty.pdf": "malformed", "html-as-pdf.pdf": "malformed", "garbage-after-header.pdf": "malformed",
             "truncated.pdf": "malformed", "xref-missing.pdf": "malformed", "xref-wrong.pdf": "malformed",
             "xref-huge-size.pdf": "malformed", "stream-length-lie.pdf": "malformed", "flate-corrupt.pdf": "malformed",
             "flate-bomb.pdf": "too_large", "pages-deep.pdf": "malformed", "pages-cycle.pdf": "malformed",
             "pages-many.pdf": "too_many_pages", "resources-circular.pdf": "malformed", "encrypted.pdf": "encrypted"}


def adversarial(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, make in ADVERSARIAL.items():
        data = make(args)
        (out / name).write_bytes(data)
        print(f"{name:<28}{len(data):>10} bytes")
    return 0


def score_adversarial(args) -> int:
    files = sorted(Path(args.adversarial).glob("*.pdf"))
    if not files:
        raise SystemExit(f"no .pdf files in {args.adversarial}")
    bad = 0
    print(f"{'file':<28}{'result':<30}{'time':>7}{'MiB':>7}  verdict")
    for f in files:
        res = run_command(args.cmd, f.read_bytes(), args.time_limit, args.mem_limit, args.as_limit)
        over_mem = res["status"] == "memory" or res["peak_mib"] > args.mem_limit
        ok = res["status"] == "reason" and not over_mem
        shown = f"{res['status']} {res['reason']}".strip() + (f" ({res['detail'][:20]})" if res["status"] in ("bad_exit", "crashed") else "")
        if res["status"] == "text":
            shown = f"TEXT ({len(res['text'])} chars)"
        why = "ok" if ok else ("FAIL: " + {"timeout": "hang (killed at the limit)", "memory": "memory over the limit",
                                          "text": "printed text with exit 0", "crashed": "crashed", "bad_exit": "non-zero exit without a reason word",
                                          "output_too_large": "output without end", "cannot_start": "cannot start"}.get(res["status"], "memory over the limit"))
        pref = PREFERRED.get(f.name)
        note = f" (expected {pref})" if ok and pref and res["reason"] != pref else ""
        print(f"{f.name:<28}{shown:<30}{res['seconds']:>6.2f}s{res['peak_mib']:>7.0f}  {why}{note}")
        bad += not ok
    print(f"== adversarial: {len(files) - bad}/{len(files)} ended within {args.time_limit:g} s and {args.mem_limit:g} MiB with a reason and no text")
    return 1 if bad else 0


# ---------------------------------------------------------------------------------------------------- fetch, verify, check

def _log_refused(r: Path, entry: dict) -> None:
    r.mkdir(parents=True, exist_ok=True)
    with (r / "refused.jsonl").open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fetch_doc(r: Path, did: str, url: str, tries: int = 2) -> int:
    """Fetch url as web_fetch does and keep the bytes as docs/<id>.pdf and the facts as docs/<id>.json. A refusal
    (robots, HTTP error, timeout, too big, not a PDF) is appended to refused.jsonl with its reason."""
    if not re.fullmatch(r"[a-z0-9-]{3,40}", did):
        raise SystemExit("ID: 3-40 lowercase letters, digits and dashes")
    meta = {"id": did, "url": url, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    err = ""
    for _ in range(tries):
        try:
            if not fetch.allowed_by_robots(url, frozenset()):
                raise fetch.Refused("robots.txt does not allow it")
            final, ctype, body = fetch._get(url, frozenset())
            if b"%PDF-" not in body[:1024]:
                raise fetch.Refused(f"not a PDF (content type {ctype or '(none)'})")
            err = ""
            break
        except fetch.Refused as e:
            err = str(e)
            if "robots.txt" in err or "HTTP 4" in err or "not a PDF" in err or "larger than" in err:
                break
        except Exception as e:  # an address the HTTP library will not send (spaces, control characters)
            err = f"cannot fetch: {type(e).__name__}: {e}"
            break
    if err:
        entry = {**meta, "refused": err}
        _log_refused(r, entry)
        print(json.dumps(entry, ensure_ascii=False))
        return 2
    d = r / "docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{did}.pdf").write_bytes(body)
    meta |= {"final": final, "type": ctype, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    (d / f"{did}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def _reference_cmds(args) -> dict:
    """name -> command for the two reference extractors: pypdf and PDFium (pdftotext is used too when installed)."""
    here = Path(__file__).with_name("pdf_reference.py")
    cmds = {"pypdf": f"uv run --no-project --with pypdf==6.1.1 --with cryptography==44.0.2 python -I {here} pypdf --max-pages 100000",
            "pdfium": f"uv run --no-project --with pypdfium2==4.30.0 python -I {here} pdfium --max-pages 100000"}
    if subprocess.run(["which", "pdftotext"], capture_output=True).returncode == 0:
        cmds["pdftotext"] = f"python3 -I {here} pdftotext --max-pages 100000"
    for item in args.ref or []:
        name, _, c = item.partition("=")
        cmds[name] = c
    return cmds


def verify(args) -> int:
    """Look every passage up in each reference extractor (the same matching as the scorer) and classify it: both /
    one (which) / none. Scanned documents: both references must find no text layer. --write puts the class in ann."""
    r = root(args)
    ann = load_ann(r)
    cmds = _reference_cmds(args)
    summary, bad = {"both": 0, "one": 0, "none": 0}, 0
    for d in ann:
        data = (r / "docs" / f"{d['id']}.pdf").read_bytes()
        runs = {n: run_command(c, data, 300, 4096, 0) for n, c in cmds.items()}
        if d.get("scanned"):
            st = {n: (x["reason"] or x["status"]) for n, x in runs.items()}
            print(f"{d['id']}: scanned; references say {st}")
            continue
        for p in d["passages"]:
            found = [n for n, x in runs.items() if x["status"] == "text" and passage_found(Text(x["text"]), p)]
            p["ref"] = "both" if len(found) >= 2 else ("one" if found else "none")
            p["_found"] = found
            summary[p["ref"]] += 1
        miss = [p for p in d["passages"] if p["ref"] == "none"]
        print(f"{d['id']}: " + ", ".join(f"{n} {x['status']}" for n, x in runs.items()) + f"; none: {len(miss)}"
              + "".join(f"\n    NONE p{p['page']}: {p['text'][:60]}" for p in miss))
        bad += len(miss)
    print("passages: " + ", ".join(f"{k} {v}" for k, v in summary.items()))
    if args.write:
        for d in ann:
            for p in d.get("passages", []):
                p.pop("_found", None)
        (r / "ann.jsonl").write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in ann))
        (r / "verify.json").write_text(json.dumps({"summary": summary, "extractors": list(cmds)}, indent=1))
    return 0


def check(args) -> int:
    """Every annotation well formed; the PDF is there and is the one fetched; every passage has a page within the
    document, is 15 to 60 characters (a table: 2 or more cells), is not an exact copy of another; a passage that no
    reference found is marked reread; the split on file is the one the rule gives."""
    r = root(args)
    ann = load_ann(r)
    bad = 0
    seen = set()
    for d in ann:
        errs = []
        need = {"id", "lang", "category", "traits", "pages", "scanned", "passages"}
        if not need <= set(d):
            errs.append("missing keys " + ",".join(sorted(need - set(d))))
        if d.get("lang") not in ("zh", "en"):
            errs.append("lang")
        if d.get("category") not in CATEGORIES:
            errs.append("category")
        pdf, meta = r / "docs" / f"{d['id']}.pdf", r / "docs" / f"{d['id']}.json"
        if not pdf.exists() or not meta.exists():
            errs.append("not fetched")
        else:
            m = json.loads(meta.read_text())
            if m.get("sha256") and hashlib.sha256(pdf.read_bytes()).hexdigest() != m["sha256"]:
                errs.append("bytes differ from the fetched ones")
            if pdf.stat().st_size > fetch.MAX_BYTES:
                errs.append("over 5 MiB")
        ps = d.get("passages", [])
        if d.get("scanned"):
            if ps:
                errs.append("a scanned document has no passages")
        else:
            if not 3 <= len(ps) <= 5:
                errs.append(f"{len(ps)} passages (3 to 5 wanted)")
            if d.get("category") != "legal" and not any(p.get("kind") == "table" for p in ps) and "table" in d.get("traits", []):
                errs.append("traits say table, no table passage")
        for p in ps:
            if not 1 <= p.get("page", 0) <= d.get("pages", 0):
                errs.append(f"page of: {p.get('text', '')[:20]}")
            n = len(p.get("text", ""))
            if p.get("kind") != "table" and not 12 <= n <= 90:
                errs.append(f"length {n}: {p.get('text', '')[:20]}")
            if p.get("kind") == "table" and len(p.get("cells", [])) < 2:
                errs.append("table passage with fewer than 2 cells")
            if p.get("ref") not in ("both", "one", "none"):
                errs.append(f"not verified: {p.get('text', '')[:20]}")
            if p.get("ref") == "none" and not p.get("reread"):
                errs.append(f"no reference found it and it was not re-read: {p['text'][:20]}")
            if (d["id"], p.get("text")) in seen:
                errs.append("duplicate passage")
            seen.add((d["id"], p.get("text")))
        print(f"{d.get('id')}: {'ok' if not errs else '; '.join(errs)}")
        bad += bool(errs)
    stored = r / "split.json"
    if stored.exists() and json.loads(stored.read_text())["assignment"] != current_split(r, ann):
        print("split.json: DIFFERENT from the rule applied to ann.jsonl")
        bad += 1
    elif not stored.exists():
        print("split.json: not written yet (split --write)")
    return 1 if bad else 0


def why(reason: str) -> str:
    """A refusal met while choosing documents, by whose doing: the site's, the size rule's, or this machine's network."""
    r = reason.lower()
    if "robots.txt" in r:
        return "site: robots.txt"
    if "larger than" in r:
        return "reader's rule: over 5 MiB"
    if "not a pdf" in r:
        return "site: answered with a page, not a PDF"
    if "http 4" in r or "http 5" in r:
        return "site: HTTP " + r.split("http ")[1][:3]
    if "not a public address" in r:
        return "network here: fake DNS answer"
    if "tunnel" in r or "timed out" in r or "took longer" in r or "cannot connect" in r:
        return "network here: proxy failure or timeout"
    return "other"


def refused(args) -> int:
    """The addresses refused while choosing the set, the last refusal of each id, and by whose doing. Ids that were
    fetched later (another address, or a retry) are not listed."""
    r = root(args)
    p = r / "refused.jsonl"
    entries = {}
    for line in p.read_text().splitlines() if p.exists() else []:
        e = json.loads(line)
        entries[e["id"]] = e
    kept = {x.stem for x in (r / "docs").glob("*.pdf")}
    tally: dict = {}
    for i, e in sorted(entries.items()):
        if i in kept:
            continue
        w = why(e["refused"])
        tally[w] = tally.get(w, 0) + 1
        print(f"{w:<42}{i:<22}{e['url'][:80]}  [{e['refused'][:50]}]")
    print("== " + ", ".join(f"{k}: {v}" for k, v in sorted(tally.items())))
    return 0


def table(args) -> int:
    r = root(args)
    ann = load_ann(r)
    sp = current_split(r, ann)
    for d in ann:
        meta = json.loads((r / "docs" / f"{d['id']}.json").read_text())
        print(f"| {d['id']} | {d['lang']} | {d['category']} | {', '.join(d['traits'])} | {d['pages']} | {meta['bytes']} | {sp[d['id']]} |")
    return 0


def do_split(args) -> int:
    r = root(args)
    ann = load_ann(r)
    sp = current_split(r, ann)
    held = sorted(i for i, v in sp.items() if v == "held")
    print(f"held out ({len(held)}): {' '.join(held)}")
    print(f"development ({len(sp) - len(held)}): " + " ".join(sorted(i for i, v in sp.items() if v == "dev")))
    if args.write:
        (r / "split.json").write_text(json.dumps({"rule": split_rule.__doc__, "assignment": sp, "fixed": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                                                 ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="e4pdf", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help=f"data directory (default $E4PDF_ROOT or {DEFAULT_ROOT})")
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("id")
    f.add_argument("url")
    v = sub.add_parser("verify")
    v.add_argument("--write", action="store_true")
    v.add_argument("--ref", action="append", help="name=command: one more reference extractor")
    sub.add_parser("check")
    sub.add_parser("table")
    sub.add_parser("refused")
    s = sub.add_parser("split")
    s.add_argument("--write", action="store_true")
    a = sub.add_parser("adversarial")
    a.add_argument("--out", required=True)
    a.add_argument("--bomb-mib", type=int, default=DEFAULT_BOMB_MIB, help="size the bomb inflates to (MiB)")
    a.add_argument("--depth", type=int, default=20000, help="nesting of the page tree")
    a.add_argument("--pages", type=int, default=60000, help="kids of the page tree")
    sc = sub.add_parser("score")
    sc.add_argument("--cmd", required=True, help="the command; PDF on stdin, text on stdout (see the module doc)")
    sc.add_argument("--time-limit", type=float, default=TIME_LIMIT)
    sc.add_argument("--mem-limit", type=float, default=MEM_LIMIT_MIB, help="MiB")
    sc.add_argument("--as-limit", type=int, default=AS_LIMIT_MIB, help="RLIMIT_AS in MiB, 0 = off (safety net)")
    sc.add_argument("--held-out", action="store_true", help="run the held-out documents INSTEAD of the development ones (once, to confirm)")
    sc.add_argument("--adversarial", metavar="DIR", help="run the files of DIR (from `adversarial --out`) instead of the set")
    sc.add_argument("--docs", help="only these ids, comma separated")
    sc.add_argument("--include-none", action="store_true", help="also count the passages no reference extractor found")
    sc.add_argument("--json", help="write the rows and totals here")
    args = ap.parse_args(argv)
    if args.cmd_name == "fetch":
        return fetch_doc(root(args), args.id, args.url)
    if args.cmd_name == "score":
        return score_adversarial(args) if args.adversarial else score_set(args)
    return {"verify": verify, "check": check, "table": table, "refused": refused, "split": do_split, "adversarial": adversarial}[args.cmd_name](args)


if __name__ == "__main__":
    sys.exit(main())
