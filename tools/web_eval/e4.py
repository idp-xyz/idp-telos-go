"""E4 (reading) and the address checks of ADR-0003 section 7.3 (T3, T7), without a model.

E4: pages of our own text served by the local origin, each with sentences the extracted text must contain and page
furniture it must not. The pages cover what the reader meets: an article inside navigation and footers, a table, a
long page read in parts, a page whose text only a script writes (a plain reader cannot see it: what the renderer of
phase 2 is for), GBK-encoded Chinese, a redirect chain, plain text, and pages that must be refused (too large, too
slow, an unsupported type, a redirect into a private address).

SSRF: addresses that must be refused before any connection (T3).
"""
import html

LONG_PARAS = 300


def _page(title, body, lang="en", charset="utf-8"):
    return (f"<!doctype html><html lang='{lang}'><head><meta charset='{charset}'><title>{html.escape(title)}</title></head><body>"
            "<header><a href='/'>Home</a> · <a href='/rates'>Rates</a> · Subscribe to our newsletter</header>"
            "<nav><ul><li>Products</li><li>Careers</li><li>Investors</li></ul></nav>"
            f"<main><article><h1>{html.escape(title)}</h1>{body}</article></main>"
            "<aside>Related: Five tips for faster shipping. Advertisement: buy boxes in bulk.</aside>"
            "<footer>Copyright 2026 Example Logistics Group. Privacy · Cookies · Imprint</footer></body></html>")


FURNITURE = ["Subscribe to our newsletter", "Advertisement: buy boxes in bulk", "Privacy · Cookies · Imprint"]


def pages() -> tuple[dict, list[dict]]:
    """(pages for the origin, the checks)."""
    p, checks = {}, []

    art = ["Example Logistics opened a new sorting hub in Rotterdam on 3 November 2026.",
           "The hub handles up to 42,000 parcels an hour and serves the Benelux region.",
           "Construction took 19 months and cost EUR 86 million."]
    p["/article.html"] = ("text/html; charset=utf-8", _page("Rotterdam hub opens", "".join(f"<p>{s}</p>" for s in art)).encode())
    checks.append({"id": "e4-article", "path": "/article.html", "expect": "ok", "contain": art, "not_contain": FURNITURE})

    rows = [("Zone 1", "4.20"), ("Zone 2", "5.10"), ("Zone 3", "6.75")]
    table = "<table><tr><th>Zone</th><th>Price (EUR)</th></tr>" + "".join(f"<tr><td>{z}</td><td>{v}</td></tr>" for z, v in rows) + "</table>"
    p["/table.html"] = ("text/html; charset=utf-8", _page("Parcel prices 2027", "<p>Prices from 1 January 2027.</p>" + table).encode())
    checks.append({"id": "e4-table", "path": "/table.html", "expect": "ok", "contain": ["Zone 3", "6.75", "Prices from 1 January 2027."], "not_contain": FURNITURE})

    long_body = "".join(f"<p>Paragraph {i}: the archive lists consignment {1000 + i} as delivered on time.</p>" for i in range(LONG_PARAS))
    p["/long.html"] = ("text/html; charset=utf-8", _page("Delivery archive", long_body).encode())
    checks.append({"id": "e4-long", "path": "/long.html", "expect": "ok", "pages_at_least": 2,
                   "contain": ["consignment 1000 as delivered", f"consignment {1000 + LONG_PARAS - 1} as delivered"], "not_contain": FURNITURE})

    script = ("<div id='app'></div><script>document.getElementById('app').innerHTML="
              "'<p>The tracking portal moves to track.example.com on 1 February 2027.</p>'</script>")
    p["/script.html"] = ("text/html; charset=utf-8", _page("Tracking portal", script).encode())
    checks.append({"id": "e4-script-only", "path": "/script.html", "expect": "needs-render",
                   "contain": ["The tracking portal moves to track.example.com on 1 February 2027."], "not_contain": []})

    zh = ["示例物流于 2026 年 11 月 3 日在鹿特丹启用新分拣中心。", "该中心每小时最多处理 42000 件包裹。"]
    p["/gbk.html"] = ("text/html; charset=gbk", _page("鹿特丹分拣中心启用", "".join(f"<p>{s}</p>" for s in zh), "zh", "gbk").encode("gbk"))
    checks.append({"id": "e4-gbk", "path": "/gbk.html", "expect": "ok", "contain": zh, "not_contain": []})

    p["/r1"] = ("redirect", "/r2")
    p["/r2"] = ("redirect", "/r3")
    p["/r3"] = ("redirect", "/article.html")
    checks.append({"id": "e4-redirects", "path": "/r1", "expect": "ok", "contain": art[:1], "not_contain": []})

    txt = "Service bulletin 2026-47: depot Leipzig closes early on 24 December 2026."
    p["/bulletin.txt"] = ("text/plain; charset=utf-8", txt.encode())
    checks.append({"id": "e4-plain", "path": "/bulletin.txt", "expect": "ok", "contain": [txt], "not_contain": []})

    p["/big.html"] = ("text/html", b"<p>" + b"x" * (6 << 20) + b"</p>")
    checks.append({"id": "e4-too-large", "path": "/big.html", "expect": "refused", "why": "larger than"})
    p["/archive.zip"] = ("application/zip", b"PK\x03\x04")
    checks.append({"id": "e4-type", "path": "/archive.zip", "expect": "refused", "why": "content type"})
    p["/inward"] = ("redirect", "http://10.0.0.1/admin")
    checks.append({"id": "e4-redirect-inward", "path": "/inward", "expect": "refused", "why": "not a public address"})
    p["/slow.html"] = ("slow", 30)
    checks.append({"id": "e4-slow", "path": "/slow.html", "expect": "refused", "why": "took longer", "slow": True})
    p["/robots.txt"] = ("text/plain", b"User-agent: *\nDisallow: /private/\n")
    p["/private/page.html"] = ("text/html", _page("Private", "<p>not for robots</p>").encode())
    checks.append({"id": "e4-robots", "path": "/private/page.html", "expect": "refused", "why": "robots.txt"})
    return p, checks


SSRF = [
    "http://127.0.0.1/", "http://localhost:8080/", "http://[::1]/", "http://0.0.0.0/", "http://2130706433/", "http://0x7f.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/", "http://10.0.0.1/", "http://172.16.5.4/", "http://192.168.1.1/",
    "http://100.64.0.1/", "http://[::ffff:127.0.0.1]/", "http://224.0.0.1/", "http://keycloak:7080/", "http://user:pass@example.com/",
    "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/", "javascript:alert(1)",
]
