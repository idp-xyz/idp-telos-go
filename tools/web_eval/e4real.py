"""E4 on real pages (ADR-0003 13.5): public pages mirrored to .local/web-eval/e4-real (never into this repository, so
that no third-party content is republished), each annotated by hand with passages of its main text that the extracted
text must keep and page furniture it must drop.

  python3 -m tools.web_eval.e4real mirror ID URL   fetch URL as web_fetch does (address check on every hop, robots.txt,
                                                   5 MiB, 15 s, the same user agent) and keep the raw bytes
  python3 -m tools.web_eval.e4real view ID         the page's visible text, one text node a line with its tag path,
                                                   to annotate from without running any extractor
  python3 -m tools.web_eval.run e4-real            score the extractors on the annotated pages

ann-<group>.jsonl, one annotated page a line (refused-<group>.jsonl: the URLs refused while choosing them):
  {"id", "url", "lang": "zh"|"en", "kind", "script_only": bool, "contain": [passage, ...], "not_contain": [furniture, ...], "notes"}
"""
import html.parser
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from . import fetch

ROOT = Path(".local/web-eval/e4-real")
MODES = ("precision", "balanced", "recall", "stdlib")


def mirror(pid: str, url: str) -> int:
    if not re.fullmatch(r"[a-z0-9-]{3,40}", pid):
        raise SystemExit("ID: 3-40 lowercase letters, digits and dashes")
    meta = {"id": pid, "url": url, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        if not fetch.allowed_by_robots(url, frozenset()):
            raise fetch.Refused("robots.txt does not allow it")
        final, ctype, body = fetch._get(url, frozenset())
    except fetch.Refused as e:
        print(json.dumps({**meta, "refused": str(e)}, ensure_ascii=False))
        return 2
    d = ROOT / "pages"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.raw").write_bytes(body)
    meta |= {"final": final, "type": ctype, "bytes": len(body), "charset": fetch._charset(ctype, body)}
    (d / f"{pid}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def body(pid: str) -> tuple[dict, str]:
    meta = json.loads((ROOT / "pages" / f"{pid}.json").read_text())
    raw = (ROOT / "pages" / f"{pid}.raw").read_bytes()
    return meta, raw.decode(meta["charset"], "replace")


class _Nodes(html.parser.HTMLParser):
    """Every non-blank text node outside script, style, template and noscript, with the path of tags around it."""
    HIDDEN = {"script", "style", "template", "noscript", "svg"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack, self.lines = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        a = dict(attrs)
        label = tag + (f"#{a['id']}" if a.get("id") else "") + (f".{a['class'].split()[0]}" if a.get("class", "").split() else "")
        self.stack.append((tag, label[:40]))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if any(t in self.HIDDEN for t, _ in self.stack):
            return
        text = " ".join(data.split())
        if text:
            path = ">".join(label for t, label in self.stack if t not in ("html",))[-110:]
            self.lines.append(f"{path} | {text}")


def view(pid: str) -> int:
    meta, text = body(pid)
    p = _Nodes()
    p.feed(text)
    print(f"# {meta['id']} {meta.get('final', meta['url'])} ({meta.get('bytes')} bytes, {meta.get('charset')})")
    print("\n".join(p.lines))
    return 0


KINDS = {"government", "statistics", "news", "press-release", "docs", "release-notes", "advisory", "blog", "forum", "wiki", "company"}


def check(path: str) -> int:
    """Each annotation is well formed, and each passage and each piece of furniture is on its page as written."""
    bad = 0
    for page in load(Path(path)):
        errs = []
        if not set(page) >= {"id", "url", "lang", "kind", "script_only", "contain", "not_contain"}:
            errs.append("missing keys")
        if page.get("lang") not in ("zh", "en") or page.get("kind") not in KINDS:
            errs.append("lang or kind")
        try:
            _, text = body(page["id"])
        except FileNotFoundError:
            errs.append("not mirrored")
            text = ""
        p = _Nodes()
        p.feed(text)
        visible = norm("".join(line.split(" | ", 1)[1] for line in p.lines))
        if not page.get("script_only") and len(page.get("contain", [])) < 3:
            errs.append("fewer than 3 passages")
        if not page.get("script_only") and len(page.get("not_contain", [])) < 2:
            errs.append("fewer than 2 pieces of furniture")
        for s in page.get("contain", []) + page.get("not_contain", []):
            if norm(s) not in visible:
                errs.append(f"not on the page as written: {s[:50]}")
        for f in page.get("not_contain", []):
            if any(norm(f) in norm(c) for c in page.get("contain", [])):
                errs.append(f"furniture inside a passage: {f[:50]}")
        print(f"{page.get('id')}: {'ok' if not errs else '; '.join(errs)}")
        bad += bool(errs)
    return 1 if bad else 0


def why(reason: str) -> str:
    """A refusal met while choosing pages, by whose doing: the site's (what idp-web will meet anywhere) or this
    machine's network (a resolver that answers blocked names with a fake address, a proxy that fails)."""
    r = reason.lower()
    if "robots.txt" in r:
        return "site: robots.txt"
    if "bot" in r or "captcha" in r or "unblock." in r:
        return "site: bot check page"
    if "http 4" in r or "http 5" in r:
        return "site: HTTP " + r.split("http ")[1][:3]
    if "not a public address" in r:
        return "network here: fake DNS answer"
    if "tunnel" in r or "timed out" in r or "cannot connect" in r:
        return "network here: proxy or timeout"
    return "other"


def norm(s: str) -> str:
    """Compare text as words only: Markdown marks, escapes and all white space are dropped (extractors join inline
    elements with or without spaces, and Chinese has none to begin with)."""
    return re.sub(r"[\s*_`#|>\\]+", "", unicodedata.normalize("NFKC", s))


def score(text: str, page: dict) -> dict:
    t = norm(text)
    kept = [c for c in page["contain"] if norm(c) in t]
    leaked = [f for f in page["not_contain"] if norm(f) in t]
    return {"kept": len(kept), "of": len(page["contain"]), "leaked": len(leaked), "furniture": len(page["not_contain"]),
            "missing": [c for c in page["contain"] if c not in kept], "leaked_text": leaked, "chars": len(text)}


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_all(root: Path = ROOT, prefix: str = "ann-") -> list[dict]:
    """The annotated pages of every group (ann-<group>.jsonl), or the refusals met while choosing them (refused-)."""
    return [r for p in sorted(root.glob(f"{prefix}*.jsonl")) for r in load(p)]


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["mirror"] and len(sys.argv) == 4:
        sys.exit(mirror(sys.argv[2], sys.argv[3]))
    if cmd == ["view"] and len(sys.argv) == 3:
        sys.exit(view(sys.argv[2]))
    if cmd == ["check"] and len(sys.argv) == 3:
        sys.exit(check(sys.argv[2]))
    sys.exit(__doc__)
