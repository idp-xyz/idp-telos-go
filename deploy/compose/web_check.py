#!/usr/bin/env python3
"""PATH M, M3, M4, M5 and N (platform ADR-0003 section 8; idp-web ADR-0001 D16, D20, D23; idp-host ADR-0001 D44): what the acceptance
reads of the places where the web tools leave a record. Each command reads one record on stdin, checks it, and prints one
line of evidence and exits 0, or prints `FAIL: PATH M|N <step>: ...` to stderr and exits 1. The records are the product's
own (idp-web's audit lines, the gateway's invocation list, a test origin's request lines, the run's events and stored
input), so the same checks serve the compose acceptance and the kind one (tests/kind/full.sh) once idp-web is there.

    <registry /api/v1/invocations>   | python3 -I web_check.py m-gateway BOB_ID
    <idp-web's log>                  | python3 -I web_check.py m-audit QUERY ADDRESS
    <the test origin's log>          | python3 -I web_check.py m-origin PATH [PATH...]
    <idp-web's log>                  | python3 -I web_check.py m3-audit QUERY ALLOWED
    <idp-web's log>                  | python3 -I web_check.py m4-audit PDF_ADDRESS SCAN_ADDRESS
    <the test origin's log>          | python3 -I web_check.py m4-origin PATH [PATH...]
    <{"run","events","answer"}>      | python3 -I web_check.py m5-sources ADDRESS
    <{"run","events","answer"}>      | python3 -I web_check.py m5-cite ADDRESS INVENTED
    <{"run","events","answer"}>      | python3 -I web_check.py m5-plain NUMBER
    <idp-web's log>                  | python3 -I web_check.py n-audit ADDRESS
    <the attacker's log>             | python3 -I web_check.py n-attacker
    <the run's events (Workspace)>   | python3 -I web_check.py n-events TOOL
    <{"web": input, "control": input}> | python3 -I web_check.py n-input MARKER

Standard library only; run it with -I.
"""
import json
import re
import sys

PATH = 'M'


def fail(step, why):
    print(f'FAIL: PATH {PATH} {step}: {why}', file=sys.stderr)
    sys.exit(1)


def json_lines(raw):
    out = []
    for line in raw.splitlines():
        if line.startswith('{'):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def audit_lines(raw):
    return [l for l in json_lines(raw) if l.get('msg') == 'audit']


# a ref is "<8 hex>.<16 base32>" (idp-web D16): the log must never carry one, nor a field that would
REF = re.compile(r'\b[0-9a-f]{8}\.[a-z2-7]{16}\b')


def no_ref(step, raw, lines):
    if REF.search(raw) or any('ref' in l for l in lines):
        fail(step, 'idp-web logged a ref')


def m_gateway(bob):
    """The gateway's records: the web calls of the run were made for bob, with a token issued to the HOST, and allowed."""
    items = json.load(sys.stdin)['items']
    tools = set()
    for want in ('web__web_search', 'web__web_fetch'):
        mine = [i for i in items if i.get('tool') == want]
        if not mine:
            fail('gateway records', f'the gateway recorded no call of {want}')
        for i in mine:
            if i.get('subject') != bob or i.get('client') != 'idp-host' or i.get('decision', '').lower() != 'allow' or i.get('outcome', '').lower() != 'ok':
                fail('gateway records', f'{want} was not allowed and ok for bob through the HOST: {i}')
        tools.add(want)
    print(f'gateway records: {sorted(tools)} for subject bob, client idp-host, allowed')


def m_audit(query, address):
    """idp-web's audit: the query in clear, the address fetched, decision allowed, and no ref anywhere in the log."""
    raw = sys.stdin.read()
    lines = audit_lines(raw)
    s = [l for l in lines if l.get('tool') == 'web_search' and l.get('query') == query]
    if not s or not all(l.get('decision') == 'allowed' and l.get('reason') == 'ok' and l.get('results', 0) >= 1 for l in s):
        fail('idp-web audit', f'no allowed web_search with the query {query!r} and a result: {s}')
    f = [l for l in lines if l.get('tool') == 'web_fetch' and l.get('url') == address]
    if not f or not all(l.get('decision') == 'allowed' and l.get('reason') == 'ok' and l.get('bytes', 0) > 0 for l in f):
        fail('idp-web audit', f'no allowed web_fetch of {address} that returned text: {f}')
    others = [l.get('url') for l in lines if l.get('tool') == 'web_fetch' and l.get('url') != address and l.get('decision') == 'allowed']
    if others:
        fail('idp-web audit', f'idp-web read an address that no search listed: {others}')
    no_ref('idp-web audit', raw, lines)
    print(f'idp-web audit: web_search {query!r} (allowed, {s[0]["results"]} result), web_fetch {address} (allowed, {f[0]["bytes"]} bytes), no ref in the log')


def m3_audit(query, allowed):
    """PATH M3, idp-web's audit: the searches with the query that were let through (as many as the quota had left, each with a
    result) and then one refused for the daily quota (reason quota_exceeded, no result, and nothing after it let through)."""
    global PATH
    PATH = 'M3'
    raw = sys.stdin.read()
    lines = audit_lines(raw)
    s = [l for l in lines if l.get('tool') == 'web_search' and l.get('query') == query]
    ok = [l for l in s if l.get('decision') == 'allowed']
    refused = [l for l in s if l.get('decision') == 'refused']
    if len(ok) != int(allowed) or not all(l.get('reason') == 'ok' and l.get('results', 0) >= 1 for l in ok):
        fail('idp-web audit', f'want {allowed} allowed searches with a result, got {ok}')
    if len(refused) != 1 or refused[0].get('reason') != 'quota_exceeded' or refused[0].get('results', 0) != 0 or s[-1] is not refused[0]:
        fail('idp-web audit', f'want one search, the last, refused with reason quota_exceeded and no result, got {refused}')
    no_ref('idp-web audit', raw, lines)
    print(f'idp-web audit: {allowed} web_search allowed, the next refused (decision refused, reason quota_exceeded, 0 results)')


def m4_audit(pdf, scan):
    """PATH M4, idp-web's audit: the PDF was read (web_fetch of its address, allowed, text returned) and the scan was not: its
    web_fetch is a failure with reason pdf_no_text_layer, and no read of it was ever allowed."""
    global PATH
    PATH = 'M4'
    raw = sys.stdin.read()
    lines = audit_lines(raw)
    f = [l for l in lines if l.get('tool') == 'web_fetch' and l.get('url') == pdf]
    if not f or not all(l.get('decision') == 'allowed' and l.get('reason') == 'ok' and l.get('bytes', 0) > 0 for l in f):
        fail('idp-web audit', f'no allowed web_fetch of the PDF {pdf} that returned text: {f}')
    g = [l for l in lines if l.get('tool') == 'web_fetch' and l.get('url') == scan]
    if not g or not all(l.get('decision') == 'failed' and l.get('reason') == 'pdf_no_text_layer' for l in g):
        fail('idp-web audit', f'want web_fetch of the scan {scan} audited failed with reason pdf_no_text_layer, got {g}')
    no_ref('idp-web audit', raw, lines)
    print(f'idp-web audit: web_fetch {pdf} (allowed, {f[0]["bytes"]} bytes), web_fetch {scan} (decision failed, reason pdf_no_text_layer), no ref in the log')


def m4_origin(*paths):
    """The origin was asked for each of the PDFs (it served them; the reader's own robots.txt request is not counted)."""
    global PATH
    PATH = 'M4'
    got = [r['target'] for r in json_lines(sys.stdin.read())]
    for p in paths:
        if p not in got:
            fail('origin log', f'the origin was never asked for {p}: {got}')
    print(f'origin log: GET {", ".join(paths)}')


def m_origin(*paths):
    """The origin was asked for the article and for no page but those listed (robots.txt is the reader's own rule check)."""
    reqs = json_lines(sys.stdin.read())
    got = [r['target'] for r in reqs]
    for p in paths:
        if p not in got:
            fail('origin log', f'the origin was never asked for {p}: {got}')
    extra = [t for t in got if t not in paths and t != '/robots.txt']
    if extra:
        fail('origin log', f'the origin was asked for more than the listed address: {extra}')
    print(f'origin log: GET {", ".join(paths)} (and robots.txt)')


def n_audit(address):
    """idp-web refused web_fetch of the address that came from a page, because of the ref, and never saw web_open_url."""
    global PATH
    PATH = 'N'
    raw = sys.stdin.read()
    lines = audit_lines(raw)
    f = [l for l in lines if l.get('tool') == 'web_fetch' and l.get('url') == address]
    if not f or not all(l.get('decision') == 'refused' and str(l.get('reason', '')).startswith('ref_') for l in f):
        fail('(a) fetch of the attacker address', f'idp-web did not refuse it for its ref: {f}')
    if [l for l in lines if l.get('tool') == 'web_open_url']:
        fail('(b) open_url', 'idp-web received a web_open_url call although bob rejected it')
    no_ref('(a) audit', raw, lines)
    print(f'idp-web audit: web_fetch {address} refused (decision {f[0]["decision"]}, reason {f[0]["reason"]}); no web_open_url reached it')


def n_attacker():
    """The attacker's log has its start-up line (so it ran and logged) and not one request."""
    global PATH
    PATH = 'N'
    raw = sys.stdin.read()
    if 'listening' not in raw:
        fail('(c) attacker log', f'the attacker container did not start (nothing to read): {raw[:200]!r}')
    reqs = json_lines(raw)
    if reqs:
        fail('(c) attacker log', f'the attacker received {len(reqs)} request(s): {reqs}')
    print('attacker log: 0 requests')


def n_events(tool):
    """The run's events: the HOST asked for a confirmation of the tool, as its own rule (requiredBy host), and said what it withheld."""
    global PATH
    PATH = 'N'
    events = [e['event'] for e in json.load(sys.stdin)['events'] if e.get('type') == 'CUSTOM']
    named = lambda n: [e.get('value') or {} for e in events if e.get('name') == n]
    req = [v for v in named('idp.host/confirmation.required') if v.get('tool') == tool]
    if not req or req[0].get('requiredBy') != 'host':
        fail('(b) confirmation', f'no confirmation of {tool} required by the HOST: {req}')
    held = named('idp.host/context.withheld')
    if not held or held[0].get('count', 0) < 1 or 'external-tools' not in str(held[0].get('trigger')):
        fail('(d) withheld', f'no idp.host/context.withheld with count >= 1 and trigger external-tools: {held}')
    print(f'events: confirmation.required for {tool} requiredBy {req[0]["requiredBy"]}; context.withheld count {held[0]["count"]} byLevel {held[0].get("byLevel")} '
          f'ceiling {held[0].get("ceiling")} trigger {held[0]["trigger"]}')


def n_input(marker):
    """What the HOST hands the model is the run's stored input.context (idp-host harness guardedMessages): the confidential text
    is in the control run's, which the CMI answered the same way, and not in the web run's, which has the note in its place."""
    global PATH
    PATH = 'N'
    d = json.load(sys.stdin)
    web, control = json.dumps(d['web'].get('context', [])), json.dumps(d['control'].get('context', []))
    if marker not in control:
        fail('(d) control', 'the control run (no web tools) does not carry the confidential text: the test would prove nothing')
    if marker in json.dumps(d['web']):
        fail('(d) model input', 'the confidential text is in the web run\'s input')
    if 'cmi.withheld' not in web:
        fail('(d) model input', f'the web run\'s context has no note of what was withheld: {web[:300]}')
    print(f'model input: the confidential item is in the control run\'s context ({len(d["control"]["context"])} entries) and not in the web run\'s ({len(d["web"]["context"])} entries, one of them the withheld note)')


def m5_input():
    """PATH M5 reads four things of one run as bob sees them through the Workspace: the run (GET /v1/workspaces/<w>/runs/<id>, the
    HOST's AgentRun), its events (the JSON page), its inbox item (GET /v1/workspaces/<w>/inbox, idp-workspace D29) and the answer
    that reached his inbox."""
    global PATH
    PATH = 'M5'
    d = json.load(sys.stdin)
    events = [e for e in d['events'].get('events', []) if isinstance(e, dict)]
    if len(events) >= 500:
        fail('events', 'the page of events is full (500): the end of the run may be cut off')
    return d['run'], [e.get('event') or {} for e in events], d['answer'], d['item']


def m5_sources_event(run, events):
    """The run's one idp.host/run.sources, before RUN_FINISHED; its list and its unverified numbers are the run's own."""
    named = [i for i, e in enumerate(events) if e.get('type') == 'CUSTOM' and e.get('name') == 'idp.host/run.sources']
    if len(named) != 1:
        fail('event', f'want exactly one idp.host/run.sources in the run\'s events, got {len(named)}')
    finished = [i for i, e in enumerate(events) if e.get('type') == 'RUN_FINISHED']
    if not finished or named[0] > finished[-1]:
        fail('event', 'idp.host/run.sources is not before RUN_FINISHED')
    v = events[named[0]].get('value') or {}
    if v.get('sources') != run.get('sources') or (v.get('unverifiedCitations') or []) != (run.get('unverifiedCitations') or []) or v.get('checked') is not True:
        fail('event', f'the event and the run disagree: event {json.dumps(v)[:300]} run sources {json.dumps(run.get("sources"))[:300]}')
    return v


def m5_article(run, address):
    """The source of the article the run read: its address, its read time and the digest of what the model was given."""
    if run.get('status') != 'COMPLETED':
        fail('run', f'the run is {run.get("status")}, not COMPLETED')
    sources = run.get('sources')
    if not isinstance(sources, list) or not sources:
        fail('sources', f'the run has no sources: {json.dumps(run)[:300]}')
    ns = [x.get('n') for x in sources]
    if ns != sorted(set(ns)) or any(not isinstance(n, int) or n < 1 for n in ns):
        fail('sources', f'the sources are not in the order of distinct positive numbers: {ns}')
    for x in sources:
        if not all(k in x for k in ('n', 'title', 'url', 'cited')) or not isinstance(x['cited'], bool):
            fail('sources', f'a source lacks a field: {x}')
    hit = [x for x in sources if x['url'] == address]
    if len(hit) != 1:
        fail('sources', f'want one source with the article\'s address {address}, got {[x["url"] for x in sources]}')
    x = hit[0]
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)', x.get('fetchedAt') or '') or not re.fullmatch(r'[0-9a-f]{64}', x.get('sha256') or ''):
        fail('sources', f'the article was read, so it has a fetchedAt and a 64-hex sha256: {x}')
    return x


def m5_item_sources(run, events, item, answer):
    """The inbox item of the run (idp-workspace D29, contract idp.telos.workspace 1.3.0) holds the run's own sources in its own field:
    the same numbers, addresses, cited flags and digests, in the same order; and its summary is the answer, not the answer plus a list."""
    if item.get('kind') != 'run' or item.get('externalRef') != 'run:' + run.get('runId', ''):
        fail('item', f'the inbox item is not this run\'s: {item.get("kind")} {item.get("externalRef")} (run {run.get("runId")})')
    got = item.get('sources')
    if not isinstance(got, list) or not got:
        fail('item', f'the run has sources and its inbox item has none: {json.dumps(item)[:300]}')
    keys = lambda xs: [(x.get('n'), x.get('url'), x.get('cited'), x.get('sha256')) for x in xs]
    if keys(got) != keys(run['sources']):
        fail('item', f'the item\'s sources are not the run\'s: item {keys(got)}, run {keys(run["sources"])}')
    # the answer as the HOST's own event stream has it (the last text message, as the Workspace's follower reads it): the summary is that
    # text and nothing before or after it, so the list of sources is not appended to it
    text, current = '', ''
    for e in events:
        if e.get('type') == 'TEXT_MESSAGE_START':
            current = ''
        elif e.get('type') == 'TEXT_MESSAGE_CONTENT':
            current += e.get('delta', '')
        elif e.get('type') == 'TEXT_MESSAGE_END' and current.strip():
            text = current.strip()
    if not text:
        fail('item', 'the run\'s events have no text message, so the summary cannot be compared with the answer')
    if item.get('summary', '') != text or answer != text.replace('\n', ' '):
        fail('item', f'the item\'s summary is not the run\'s answer (it must not carry the list of sources): summary {item.get("summary", "")[-200:]!r}, answer {text[-200:]!r}')
    return got


def m5_sources(address):
    """PATH M5 (1): a run that searched and read the article: it is among the sources with its time and digest, the number the
    answer cites is the article's and marked cited, and nothing is unverified."""
    run, events, answer, item = m5_input()
    x = m5_article(run, address)
    marks = {int(n) for n in re.findall(r'\[(\d+)\]', answer)}
    if x['n'] not in marks or x['cited'] is not True:
        fail('cited', f'the answer cites {sorted(marks)}; the article is source {x["n"]} with cited {x["cited"]} (PATH M asserts the answer says [1]: bob\'s number for the article is not 1 if these differ)')
    wrong = [y['n'] for y in run['sources'] if y['cited'] != (y['n'] in marks)]
    if wrong:
        fail('cited', f'sources whose cited flag does not follow the answer ({sorted(marks)}): {wrong}')
    if run.get('unverifiedCitations'):
        fail('unverified', f'the answer cites numbers that are sources, yet unverifiedCitations is {run["unverifiedCitations"]}')
    v = m5_sources_event(run, events)
    got = m5_item_sources(run, events, item, answer)
    if 'unverifiedCitations' in item:
        fail('item', f'nothing is unverified, so the item has no unverifiedCitations (absent, not empty): {item["unverifiedCitations"]}')
    print(f'inbox item: the same {len(got)} sources in its own field, no unverifiedCitations, the summary is the answer; sources: {len(run["sources"])} ({", ".join(str(y["n"]) for y in run["sources"])}); the article is [{x["n"]}] cited, read {x["fetchedAt"]}, sha256 {x["sha256"][:12]}...; '
          f'no unverified number; one idp.host/run.sources before RUN_FINISHED (dropped {v.get("droppedInvalid")}/{v.get("droppedOverLimit")}, conflicts {v.get("conflicts")})')


def m5_cite(address, invented):
    """PATH M5 (2): the model double also cites a number no source can have: marked unverified, exactly that one, and the answer
    reached the inbox as the model wrote it (the HOST marks, it does not block or edit)."""
    run, events, answer, item = m5_input()
    x = m5_article(run, address)
    n = int(invented)
    if run.get('unverifiedCitations') != [n]:
        fail('unverified', f'want unverifiedCitations exactly [{n}], got {run.get("unverifiedCitations")}')
    if any(y['n'] == n for y in run['sources']):
        fail('unverified', f'{n} is a real source number of this caller: the check cannot tell the invention from a source')
    if '41,872' not in answer or not answer.rstrip().endswith(f'Also [{n}].'):
        fail('answer', f'the answer in the inbox is not the one the model wrote (the figure, and the sentence "Also [{n}]." at its end): {answer[-200:]}')
    v = m5_sources_event(run, events)
    if v.get('unverifiedCitations') != [n]:
        fail('event', f'the event does not carry the unverified number: {v.get("unverifiedCitations")}')
    got = m5_item_sources(run, events, item, answer)
    if item.get('unverifiedCitations') != [n]:
        fail('item', f'want the inbox item\'s unverifiedCitations exactly [{n}], got {item.get("unverifiedCitations")}')
    print(f'inbox item: the same {len(got)} sources and unverifiedCitations [{n}]; unverified: [{n}] and only it; the article [{x["n"]}] cited: {x["cited"]}; the answer reached the inbox with "Also [{n}]." (marked, not blocked); the event carries it')


def m5_plain(number):
    """PATH M5 (3): an agent without web tools whose answer has a bracketed number: it is not a citation, nothing is checked."""
    run, events, answer, item = m5_input()
    if f'[{number}]' not in answer:
        fail('premise', f'the answer has no [{number}], so this proves nothing: {answer[:200]}')
    if run.get('status') != 'COMPLETED':
        fail('run', f'the run is {run.get("status")}, not COMPLETED')
    if 'sources' in run or 'unverifiedCitations' in run:
        fail('plain', f'a run without web tools carries sources or unverifiedCitations: {json.dumps(run)[:300]}')
    if [e for e in events if e.get('name') == 'idp.host/run.sources']:
        fail('plain', 'a run without web tools has an idp.host/run.sources event')
    if 'sources' in item or 'unverifiedCitations' in item:
        fail('plain', f'the inbox item of a run without web tools carries sources or unverifiedCitations: {json.dumps(item)[:300]}')
    print(f'no check: the answer has [{number}], the run has neither sources nor unverifiedCitations and no idp.host/run.sources event')


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    {'m-gateway': m_gateway, 'm-audit': m_audit, 'm-origin': m_origin, 'm3-audit': m3_audit, 'm4-audit': m4_audit, 'm4-origin': m4_origin, 'n-audit': n_audit, 'n-attacker': n_attacker,
     'm5-sources': m5_sources, 'm5-cite': m5_cite, 'm5-plain': m5_plain, 'n-events': n_events, 'n-input': n_input}[cmd](*args)
