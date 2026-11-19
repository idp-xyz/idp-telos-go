#!/usr/bin/env python3
"""PATH M, M3, M4 and N (platform ADR-0003 section 8; idp-web ADR-0001 D16, D20, D23; idp-host ADR-0001 D44): what the acceptance
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


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    {'m-gateway': m_gateway, 'm-audit': m_audit, 'm-origin': m_origin, 'm3-audit': m3_audit, 'm4-audit': m4_audit, 'm4-origin': m4_origin, 'n-audit': n_audit, 'n-attacker': n_attacker,
     'n-events': n_events, 'n-input': n_input}[cmd](*args)
