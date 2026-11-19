"""Reading a web page the way idp-web will (ADR-0003 D4): only public addresses, every redirect checked again, at
most 5 redirects, 5 MiB and 15 seconds, HTML / plain text / PDF only, robots.txt obeyed; then the main text as
Markdown, served in pages of at most 12 KiB.

The address check resolves the name and refuses it when any address is not a public one (private, loopback,
link-local, multicast, reserved, cloud metadata). idp-web will also connect to the address it checked (no second
lookup); this harness goes through the shell's proxy when one is set, so it checks and lets the proxy connect.
"""
import html
import html.parser
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib

MAX_BYTES = 5 << 20
TIMEOUT = 15.0
MAX_REDIRECTS = 5
PAGE_BYTES = 12 << 10
USER_AGENT = "idp-web-eval/0.1 (+https://github.com/idp-xyz/idp-telos-go)"
TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/pdf")


class Refused(Exception):
    """The address may not be read; the message says why."""


def check_address(url: str, allow: frozenset = frozenset()) -> None:
    """Raise Refused unless url is http(s) to a public address. allow: host:port pairs that may be private (the
    test origin only; the counterpart of IDP_EGRESS_ALLOW_PRIVATE, but for named hosts)."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise Refused(f"scheme {u.scheme or '(none)'} is not http or https")
    if not u.hostname:
        raise Refused("no host")
    if u.username or u.password:
        raise Refused("credentials in the address")
    port = u.port or (443 if u.scheme == "https" else 80)
    if f"{u.hostname}:{port}" in allow:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise Refused(f"cannot resolve {u.hostname}: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise Refused(f"{u.hostname} resolves to {ip}, which is not a public address")


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, allow):
        self.allow = allow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_address(newurl, self.allow)  # every hop again: a public page may redirect inward
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(url: str, allow: frozenset):
    u = urllib.parse.urlsplit(url)
    local = f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}" in allow
    proxy = urllib.request.ProxyHandler({}) if local else urllib.request.ProxyHandler()
    return urllib.request.build_opener(proxy, _Redirects(allow))


def _get(url: str, allow: frozenset, max_bytes: int = MAX_BYTES):
    check_address(url, allow)
    deadline = time.monotonic() + TIMEOUT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": ", ".join(TYPES) + ";q=0.9, */*;q=0.1",
                                               "Accept-Encoding": "gzip, deflate"})
    try:
        resp = _opener(url, allow).open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        raise Refused(f"HTTP {e.code}") from None
    except Refused:
        raise
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Refused(f"cannot connect: {getattr(e, 'reason', e)}") from None
    with resp:
        body = bytearray()
        while len(body) <= max_bytes:
            if time.monotonic() > deadline:
                raise Refused(f"took longer than {TIMEOUT:.0f} s")
            chunk = resp.read(65536)
            if not chunk:
                break
            body += chunk
        if len(body) > max_bytes:
            raise Refused(f"larger than {max_bytes >> 20} MiB")
        body = _decoded(bytes(body), resp.headers.get("Content-Encoding", ""), max_bytes)
        return resp.geturl(), resp.headers.get("Content-Type", ""), body


def _decoded(body: bytes, encoding: str, max_bytes: int) -> bytes:
    """Undo the compression the server applied: many send gzip whether or not it was asked for (found on real pages,
    E4). The size limit holds for the decoded body too, so a few compressed kilobytes cannot expand past it."""
    enc = encoding.strip().lower()
    if enc in ("", "identity"):
        return body
    if enc not in ("gzip", "x-gzip", "deflate"):
        raise Refused(f"content encoding {enc} is not read")
    # gzip has its own header (wbits 31); deflate is meant to be zlib-wrapped (15) but some servers send it raw (-15)
    for wbits in ((31,) if enc != "deflate" else (15, -15)):
        d = zlib.decompressobj(wbits)
        try:
            out = d.decompress(body, max_bytes + 1)
        except zlib.error:
            continue
        if len(out) > max_bytes or d.unconsumed_tail:
            raise Refused(f"larger than {max_bytes >> 20} MiB")
        return out
    raise Refused(f"content encoding {enc} could not be decoded")


_robots: dict = {}


def allowed_by_robots(url: str, allow: frozenset) -> bool:
    u = urllib.parse.urlsplit(url)
    root = f"{u.scheme}://{u.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            _, _, body = _get(root + "/robots.txt", allow, max_bytes=512 << 10)
            rp.parse(body.decode("utf-8", "replace").splitlines())
        except Refused:
            rp.parse([])  # no robots.txt (or none we can read): everything allowed
        _robots[root] = rp
    return _robots[root].can_fetch(USER_AGENT, url)


def _charset(ctype: str, body: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    cs = m.group(1) if m else "utf-8"
    return cs.decode() if isinstance(cs, bytes) else cs


class _Text(html.parser.HTMLParser):
    """The fallback extractor (no trafilatura): text outside script, style and page furniture, headings marked."""
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "template"}
    BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.out, self.skip, self.title, self.in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n" + ("#" * int(tag[1]) + " " if tag[0] == "h" and tag[1:].isdigit() else ""))

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


FALLBACK_SHARE = 0.10


def extract(body: str, url: str, mode: str = "balanced") -> tuple[str, str, str]:
    """(title, main text as Markdown, extractor name). mode: balanced (what web_fetch uses), precision or recall
    (trafilatura's other settings), or stdlib (the fallback alone).

    On 51 annotated real pages (E4, ADR-0003 13.1) trafilatura's balanced setting kept more of the main text than
    precision (which had looked better on the short pages of our own) with no more furniture, and kept tables alike.
    All three settings fail on some pages (gov.cn's documents: nothing, or the title alone); so when trafilatura
    finds nothing, or less than FALLBACK_SHARE of the text the plain reader finds, the plain reader's text is used.
    """
    p = _Text()
    p.feed(body)
    plain = re.sub(r"[ \t]+", " ", "".join(p.out))
    plain = html.unescape(re.sub(r"\n\s*\n+", "\n\n", plain).strip())
    title = html.unescape(p.title.strip())
    if mode != "stdlib":
        try:
            import trafilatura  # optional; the harness says which extractor ran
            meta = trafilatura.extract_metadata(body)
            text = trafilatura.extract(body, url=url, output_format="markdown", include_tables=True, include_links=False,
                                       include_comments=False, favor_precision=mode == "precision", favor_recall=mode == "recall")
            if text and len(text) >= FALLBACK_SHARE * len(plain):
                return (meta.title if meta and meta.title else title), text, "trafilatura"
        except ImportError:
            pass
    return title, plain, "stdlib"


def _pdf_text(body: bytes) -> str:
    try:
        import io
        import pypdf
    except ImportError:
        raise Refused("PDF text extraction is not available (pypdf)") from None
    reader = pypdf.PdfReader(io.BytesIO(body))
    return "\n\n".join((pg.extract_text() or "") for pg in reader.pages).strip()


def read(url: str, allow: frozenset = frozenset(), robots: bool = True) -> dict:
    """{url (final), title, text, bytes, type, extractor}; raises Refused."""
    if robots and not allowed_by_robots(url, allow):
        raise Refused("robots.txt does not allow it")
    final, ctype, body = _get(url, allow)
    kind = ctype.split(";")[0].strip().lower()
    if kind not in TYPES:
        raise Refused(f"content type {kind or '(none)'} is not read")
    if kind == "application/pdf":
        return {"url": final, "title": "", "text": _pdf_text(body), "bytes": len(body), "type": kind, "extractor": "pypdf"}
    decoded = body.decode(_charset(ctype, body), "replace")
    if kind == "text/plain":
        return {"url": final, "title": "", "text": decoded.strip(), "bytes": len(body), "type": kind, "extractor": "plain"}
    title, text, how = extract(decoded, final)
    return {"url": final, "title": title, "text": text, "bytes": len(body), "type": kind, "extractor": how}


def page(text: str, offset: int = 0, limit: int = PAGE_BYTES) -> tuple[str, int, int]:
    """(the part of text from byte offset, at most limit UTF-8 bytes, cut at a character boundary; next offset or -1;
    total bytes)."""
    raw = text.encode("utf-8")
    offset = max(0, min(offset, len(raw)))
    end = min(len(raw), offset + limit)
    while end < len(raw) and (raw[end] & 0xC0) == 0x80:  # do not cut inside a character
        end -= 1
    return raw[offset:end].decode("utf-8", "ignore"), (end if end < len(raw) else -1), len(raw)
