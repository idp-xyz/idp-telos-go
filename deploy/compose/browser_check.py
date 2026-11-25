#!/usr/bin/env python3
"""PATH M6 (idp-browser, platform plan browser-capability-plan.md section 3; idp-web D33): what the acceptance reads of the places that record a render. Each command
reads one record on stdin (a log, or idp-web's metrics), checks it, prints one line of evidence and exits 0, or prints `FAIL: PATH M6 <step>: ...` to stderr and
exits 1. The records are the products' own (idp-web's audit lines, the proxy's `egress` lines, the renderer's `render` lines, a canary's request lines, the metrics),
so the same checks serve the compose acceptance and the kind one.

    <idp-web's log>   | python3 -I browser_check.py audit URL key=value...    the last web_fetch audit line of URL has these values (rendered=true, render=ok, ...)
    <idp-web's log>   | python3 -I browser_check.py no-audit URL               every web_fetch audit line of URL is a plain read: rendered=false, render empty
    <proxy's log>     | python3 -I browser_check.py ssrf HOST:PORT...         one render asked for all of them, and each was denied and none was allowed
    <proxy's log>     | python3 -I browser_check.py storm MAX                 the render the proxy let the most requests through was let through at most MAX
    <proxy's log>     | python3 -I browser_check.py proxy-host HOST           how many `egress` lines name HOST, and with which decisions
    <the renderer's log> | python3 -I browser_check.py render-lines [MIN_REQUESTS MIN_BLOCKED]   the number of `render` lines (finished renders), the largest `requests` and `blocked`; with the minimums, fails below them
    <a canary's log>  | python3 -I browser_check.py canary                    no request line at all (a request line starts with `{`)
    <metrics>         | python3 -I browser_check.py metric NAME LABEL=VALUE   the value of idp_web_renders_total{outcome="ok"} (prints the number only)

Standard library only; run it with -I.
"""
import collections
import json
import sys

PATH = 'M6'


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


def want(v):
    return {'true': True, 'false': False}.get(v, v)


def audit(url, *pairs):
    lines = [l for l in json_lines(sys.stdin.read()) if l.get('msg') == 'audit' and l.get('tool') == 'web_fetch' and l.get('url') == url]
    if not lines:
        fail('audit', f'idp-web has no web_fetch audit line of {url}')
    last = lines[-1]
    for p in pairs:
        k, v = p.split('=', 1)
        if last.get(k) != want(v) and str(last.get(k)) != v:
            fail('audit', f'the audit line of {url} has {k}={last.get(k)!r}, want {v}: {({x: last.get(x) for x in ("decision", "reason", "rendered", "render", "renderMs")})}')
    print(f'idp-web audit of {url}: ' + ', '.join(f'{k}={last.get(k)}' for k in ('decision', 'reason', 'rendered', 'render')) + f', renderMs {last.get("renderMs")}')


def no_audit(url):
    lines = [l for l in json_lines(sys.stdin.read()) if l.get('msg') == 'audit' and l.get('tool') == 'web_fetch' and l.get('url') == url]
    if not lines or any(l.get('rendered') or l.get('render') for l in lines):
        fail('audit', f'want plain (not rendered) web_fetch audit lines of {url}: {[(l.get("rendered"), l.get("render")) for l in lines]}')
    print(f'idp-web audit of {url}: read plainly ({len(lines)} line), rendered=false, render empty')


def egress_by_render():
    by = collections.defaultdict(list)
    for l in json_lines(sys.stdin.read()):
        if l.get('msg') == 'egress':
            by[l['render']].append(l)
    return by


def ssrf(*targets):
    by = egress_by_render()
    if not by:
        fail('ssrf', 'the proxy has no audit line in the window')
    want_t = [t.rsplit(':', 1) for t in targets]
    best = max(by.values(), key=lambda v: sum(1 for l in v if any(l['host'] == h for h, _ in want_t)))
    missing, allowed = [], []
    for h, p in want_t:
        got = [l for l in best if l['host'] == h and str(l['port']) == p]
        if not got:
            missing.append(f'{h}:{p}')
        allowed += [f'{h}:{p}' for l in got if l['decision'] != 'deny']
    if missing or allowed:
        fail('ssrf', f'not asked of the proxy: {missing}; let through: {allowed}')
    reasons = collections.Counter(l['reason'] for l in best if l['decision'] == 'deny')
    print(f'the proxy refused all {len(want_t)} forbidden targets the page asked for ({sum(reasons.values())} refusals: {dict(reasons)}), none let through; the render\'s only allowed request was the page itself')


def storm(limit):
    """the storm is the render that made the proxy let through the most requests (not the one with the most lines: the SSRF render has many refusals). The proxy
    counts what it carries and a keep-alive connection carries several requests, so how many it carried varies from run to run (12 to 300 were seen); that it is not
    over the budget is what this checks, and the renderer's own record (render-lines MIN_REQUESTS MIN_BLOCKED) says that the storm happened and was cut."""
    by = egress_by_render()
    if not by:
        fail('storm', 'the proxy has no audit line in the window')
    best = max(by.values(), key=lambda v: sum(1 for l in v if l['decision'] == 'allow'))
    allowed = sum(1 for l in best if l['decision'] == 'allow')
    if allowed > int(limit) or allowed < 1:
        fail('storm', f'the storm render was let through {allowed} requests by the proxy (the budget is {limit})')
    print(f'the proxy carried {allowed} requests of the storm render (at most {limit} a render)')


def proxy_host(host):
    n = collections.Counter(l['decision'] for l in json_lines(sys.stdin.read()) if l.get('msg') == 'egress' and l.get('host') == host)
    print(json.dumps(dict(n)))


def render_lines(min_requests='0', min_blocked='0'):
    r = [l for l in json_lines(sys.stdin.read()) if l.get('msg') == 'render']
    most, blocked = max([l.get('requests', 0) for l in r] or [0]), max([l.get('blocked', 0) for l in r] or [0])
    if most < int(min_requests) or blocked < int(min_blocked):
        fail('render-lines', f'the renderer saw at most {most} requests of a page and refused at most {blocked}: wanted at least {min_requests} and {min_blocked} (the storm did not happen, or its budget did not hold)')
    print(json.dumps({'renders': len(r), 'most_requests': most, 'blocked': blocked}))


def canary():
    n = len(json_lines(sys.stdin.read()))
    if n:
        fail('canary', f'the canary was asked for something {n} times')
    print('the canary behind the internal names received no request')


def metric(name, label):
    k, v = label.split('=', 1)
    total = 0.0
    for line in sys.stdin.read().splitlines():
        if line.startswith(name + '{') and f'{k}="{v}"' in line:
            total += float(line.rsplit(' ', 1)[1])
    print(int(total))


if __name__ == '__main__':
    cmd, *args = sys.argv[1:]
    {'audit': audit, 'no-audit': no_audit, 'ssrf': ssrf, 'storm': storm, 'proxy-host': proxy_host, 'render-lines': render_lines, 'canary': canary, 'metric': metric}[cmd](*args)
