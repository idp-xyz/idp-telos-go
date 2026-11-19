"""Search providers behind one small interface (ADR-0003 D3). Only the query and its filters are sent: no member,
tenant or run identifier. Keys come from the environment (EXA_API_KEY, BRAVE_API_KEY, PERPLEXITY_API_KEY).

Each provider returns at most `count` results: url, title, published (when the provider knows it) and a snippet
(the provider's own excerpt of the page, when it gives one). `usage` counts requests, for cost.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Result:
    url: str
    title: str = ""
    published: str = ""
    snippet: str = ""


@dataclass
class Query:
    text: str
    count: int = 5
    include_domains: list = field(default_factory=list)
    exclude_domains: list = field(default_factory=list)


class ProviderError(Exception):
    pass


class Provider:
    name = "none"

    def __init__(self):
        self.requests = 0
        self.seconds = 0.0

    def search(self, q: Query) -> list[Result]:
        raise NotImplementedError

    def _http(self, method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 30.0) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", **headers,
                                                                              **({"Content-Type": "application/json"} if data else {})})
        start = time.monotonic()
        self.requests += 1
        for attempt in range(4):  # 429 and 5xx: again after 1, 2, 4 s (counted once: a retry is the provider's fault)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    out = json.load(r)
                    self.seconds += time.monotonic() - start
                    return out
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise ProviderError(f"{self.name}: HTTP {e.code} {e.read()[:300]!r}") from None
            except (urllib.error.URLError, OSError) as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise ProviderError(f"{self.name}: {getattr(e, 'reason', e)}") from None
        raise ProviderError(f"{self.name}: no answer")


def _key(var: str) -> str:
    v = os.environ.get(var, "").strip()
    if not v:
        raise ProviderError(f"{var} is not set")
    return v


class Exa(Provider):
    """POST https://api.exa.ai/search, results with highlights (excerpts chosen for the query) in the same call, which
    cost nothing extra (Exa changelog 2026-03-03). Types: auto (default, $7 / 1k) and lite ($4 / 1k, cached content only,
    since 2026-10-19). EXA_BASE_URL=https://api.eu.exa.ai selects the EU endpoint (ZDR by default, auto and lite only)."""
    name = "exa"

    def __init__(self, search_type: str = "auto"):
        super().__init__()
        self.key, self.search_type = _key("EXA_API_KEY"), search_type
        self.base = os.environ.get("EXA_BASE_URL", "https://api.exa.ai").rstrip("/")
        self.name = f"exa-{search_type}"

    def search(self, q: Query) -> list[Result]:
        body = {"query": q.text, "numResults": q.count, "type": self.search_type, "contents": {"highlights": True}}
        if q.include_domains:
            body["includeDomains"] = q.include_domains
        if q.exclude_domains:
            body["excludeDomains"] = q.exclude_domains
        out = self._http("POST", self.base + "/search", {"x-api-key": self.key}, body)
        return [Result(url=r.get("url", ""), title=r.get("title") or "", published=(r.get("publishedDate") or "")[:10],
                       snippet=" … ".join(r.get("highlights") or []) or (r.get("text") or "")[:600])
                for r in out.get("results", [])][: q.count]


class Brave(Provider):
    """GET https://api.search.brave.com/res/v1/web/search, with extra_snippets."""
    name = "brave"

    def __init__(self):
        super().__init__()
        self.key = _key("BRAVE_API_KEY")

    def search(self, q: Query) -> list[Result]:
        text = q.text + "".join(f" site:{d}" for d in q.include_domains[:1])  # Brave filters by operators
        text += "".join(f" -site:{d}" for d in q.exclude_domains)
        params = urllib.parse.urlencode({"q": text, "count": q.count, "extra_snippets": "true"})
        out = self._http("GET", f"https://api.search.brave.com/res/v1/web/search?{params}", {"X-Subscription-Token": self.key})
        res = []
        for r in (out.get("web") or {}).get("results", [])[: q.count]:
            snip = " … ".join([r.get("description", "")] + (r.get("extra_snippets") or []))
            res.append(Result(url=r.get("url", ""), title=r.get("title", ""), published=r.get("page_age", "")[:10], snippet=_strip_tags(snip)))
        return res


class Perplexity(Provider):
    """POST https://api.perplexity.ai/search: raw results (not its answer API)."""
    name = "perplexity"

    def __init__(self):
        super().__init__()
        self.key = _key("PERPLEXITY_API_KEY")

    def search(self, q: Query) -> list[Result]:
        body = {"query": q.text, "max_results": q.count}
        if q.include_domains or q.exclude_domains:
            body["search_domain_filter"] = q.include_domains + [f"-{d}" for d in q.exclude_domains]
        out = self._http("POST", "https://api.perplexity.ai/search", {"Authorization": f"Bearer {self.key}"}, body)
        return [Result(url=r.get("url", ""), title=r.get("title", ""), published=(r.get("date") or "")[:10], snippet=r.get("snippet", ""))
                for r in out.get("results", [])][: q.count]


class Fixture(Provider):
    """Development and tests (ADR-0003 D3): {"<query substring, lower case>": [results...]}; the first key contained
    in the query wins; no match, no results. Results point at the local test origin."""
    name = "fixture"

    def __init__(self, table: dict, base: str = ""):
        super().__init__()
        self.table = {k.lower(): v for k, v in table.items()}
        self.base = base.rstrip("/")

    def search(self, q: Query) -> list[Result]:
        self.requests += 1
        text = q.text.lower()
        for k, rows in self.table.items():
            if k in text:
                return [Result(url=r["url"].replace("{origin}", self.base), title=r.get("title", ""), published=r.get("published", ""),
                               snippet=r.get("snippet", "")) for r in rows][: q.count]
        return []


class NoSearch(Provider):
    """The baseline without search: the agent is given no tools."""
    name = "no-search"


def _strip_tags(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s)


def make(name: str) -> Provider:
    if name == "no-search":
        return NoSearch()
    if name.startswith("exa"):
        return Exa(name.split("-", 1)[1] if "-" in name else "auto")
    if name == "brave":
        return Brave()
    if name == "perplexity":
        return Perplexity()
    raise ProviderError(f"unknown provider {name} (exa-auto, exa-lite, brave, perplexity, no-search)")
