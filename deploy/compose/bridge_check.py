#!/usr/bin/env python3
"""PATH O (the plan idp-mcp-bridge-oauth-plan.md section 4; idp-mcp-bridge ADR-0001; idp-host D61; idp-workspace D30): what the acceptance reads
of the places where a member's authorization, a third party's tool and the bridge leave a record. The records are the third party's own
(deploy/compose/thirdparty_double.py: every request it received, headers and bodies, the tokens it issued), the run's events as the
Workspace shows them, and the bridge's database. Each command reads one JSON document on stdin, checks it, and prints one line of
evidence and exits 0, or prints `FAIL: PATH O <step>: ...` to stderr and exits 1. The same checks serve the compose acceptance
(deploy/compose/e2e.sh) and the kind one (tests/kind/full.sh).

    python3 -I bridge_check.py consent URL USER      play the member's browser at the third party: prints the redirect's query as JSON
                                                     (THIRDPARTY_CONNECT=host:port connects there instead, for an address only the cluster knows)
    <{"url", "client_id", "resource", "scope", "state"}> | bridge_check.py auth-url
    <{"rows", "tokens", "user", "reviewer"}>             | bridge_check.py o1-sealed
    <{"requests", "tokens", "user", "reviewer", "forbidden"}> | bridge_check.py o2-call
    <{"events", "requests", "tokens", "connection"}>     | bridge_check.py o3-alice
    <{"requests", "tokens", "user", "old"}>              | bridge_check.py o4-refresh   (the first call is refused, one refresh, the retry succeeds)
    <{"requests", "tokens", "user"}>                     | bridge_check.py o4-proactive (one refresh, and no refused call)
    <{"events", "requests", "connections", "rows"}>      | bridge_check.py o4-scope     (insufficient_scope: authorization required, the grant is kept)
    <{"status", "body", "requests", "rows"}>             | bridge_check.py o5-iss       (a wrong iss: refused, nothing stored, no code exchanged)
    <{"requests", "tokens", "user", "rows"}>             | bridge_check.py o6-revoke
    <{"rows"}>                                           | bridge_check.py gh-secrets KEYS_FILE SECRET_FILE SOURCES_DIR   (R1: the tokens the bridge holds are
                                                     unsealed in memory and never printed; THIRDPARTY_TOKENS_URL: a stand-in whose records prove the unsealing)
    <{"secrets", "sources", "required"}>                  | bridge_check.py o7-secrets   (sources: {name: text}; required: prefixes of those that must not be empty)

Standard library only; run it with -I.
"""
import base64
import hashlib
import http.client
import json
import os
import sys
import urllib.parse

PATH = 'O'
# every header the bridge may send to a third party (idp-mcp-bridge README, "What goes to the third party, exactly"), and the ones Go's
# HTTP client adds by itself; nothing else
ALLOWED_HEADERS = {'host', 'content-type', 'accept', 'mcp-protocol-version', 'mcp-method', 'mcp-name', 'user-agent', 'authorization',
                   'content-length', 'accept-encoding', 'connection'}
ALLOWED_META = {'io.modelcontextprotocol/protocolVersion', 'io.modelcontextprotocol/clientCapabilities', 'progressToken'}


def fail(step, why):
    print(f'FAIL: PATH {PATH} {step}: {why}', file=sys.stderr)
    sys.exit(1)


def bearer(r):
    a = r['headers'].get('Authorization', '')
    return a[7:] if a.startswith('Bearer ') else None


def owner(tokens, value):
    for t in tokens:
        if t['value'] == value:
            return t['user']
    return None


def body_of(r):
    try:
        return json.loads(r['body'])
    except ValueError:
        return {}


def calls(reqs):
    return [r for r in reqs if r['method'] == 'POST' and body_of(r).get('method') == 'tools/call']


def form_of(r):
    return {k: v[0] for k, v in urllib.parse.parse_qs(r['body']).items()}


def grants_of(reqs, kind):
    return [r for r in reqs if r['path'] == '/token' and form_of(r).get('grant_type') == kind]


# ---- the scripted browser ----
def consent(url, user):
    """What the member's browser does at the third party's page: it is shown the consent page, the member (a test user: the third party
    consents by itself) agrees, and the browser is redirected to the redirect address with the code. The redirect is not followed: its
    query is what the Workspace's callback page takes from the address bar."""
    u = urllib.parse.urlsplit(url)
    host, port = (os.environ.get('THIRDPARTY_CONNECT') or f'{u.hostname}:{u.port or 80}').rsplit(':', 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    conn.request('GET', u.path + ('?' + u.query if u.query else ''), headers={'Host': u.netloc, 'X-Test-User': user})
    resp = conn.getresponse()
    resp.read()
    loc = resp.getheader('Location')
    if resp.status != 302 or not loc:
        fail('browser', f'the third party answered HTTP {resp.status} to the authorization request, not a redirect')
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query, keep_blank_values=True).items()}
    q['_redirect_base'] = loc.split('?', 1)[0]
    print(json.dumps(q))


def auth_url():
    d = json.load(sys.stdin)
    u = urllib.parse.urlsplit(d['url'])
    q = {k: v for k, v in urllib.parse.parse_qs(u.query).items()}
    one = lambda k: (q.get(k) or [''])[0]
    problems = []
    if one('response_type') != 'code':
        problems.append('response_type')
    if one('code_challenge_method') != 'S256' or len(one('code_challenge')) != 43:
        problems.append('PKCE S256 challenge')
    if one('client_id') != d['client_id']:
        problems.append(f'client_id {one("client_id")}')
    if one('resource') != d['resource']:
        problems.append(f'resource {one("resource")} (RFC 8707)')
    if not set(d['scope'].split()) <= set(one('scope').split()):
        problems.append(f'scope {one("scope")!r} lacks {d["scope"]!r}')
    if len(q.get('state', [])) != 1 or len(one('state')) < 16:
        problems.append('state')
    if one('redirect_uri') != d['redirect_uri']:
        problems.append(f'redirect_uri {one("redirect_uri")}')
    if problems:
        fail('authorization address', '; '.join(problems) + f' in {d["url"].split("?")[0]}')
    print(f'authorization address at {u.netloc}{u.path}: S256, state, client_id {one("client_id")[-40:]}, resource {one("resource")}, scope "{one("scope")}"')


# ---- the checks ----
def o1_sealed():
    """The grant is in the bridge's database, and what is in its token columns is not the token the third party issued."""
    d = json.load(sys.stdin)
    rows = [r for r in d['rows'] if r['connection'] == d['connection']]
    mine = [r for r in rows if r['member'] != d['reviewer_member']]
    if len(mine) != 1:
        fail('1 sealed', f'want one grant row of the member on {d["connection"]}, got {len(mine)}: {[r["member"] for r in rows]}')
    row = mine[0]
    mine_tokens = [t for t in d['tokens'] if t['user'] == d['user'] and t['resource'] == d['resource']]
    access = [t['value'] for t in mine_tokens if t['kind'] == 'access' and not t['revoked']]
    refresh = [t['value'] for t in mine_tokens if t['kind'] == 'refresh' and not t['revoked']]
    if not access or not refresh:
        fail('1 sealed', f'the third party issued no live access and refresh token to {d["user"]}')
    for col, plain in (('sealed_access', access[-1]), ('sealed_refresh', refresh[-1])):
        raw = bytes.fromhex(row[col])
        if plain.encode() in raw or raw == plain.encode() or plain in row[col] or len(raw) <= len(plain):
            fail('1 sealed', f'{col} is not sealed: it holds or equals the plaintext token ({len(raw)} bytes)')
    print(f'grants row of the member on {d["connection"]}: sealed_access {len(bytes.fromhex(row["sealed_access"]))} bytes and sealed_refresh '
          f'{len(bytes.fromhex(row["sealed_refresh"]))} bytes, neither the issued token ({len(access[-1])} characters) nor containing it; scope "{row["scope"]}"')


def o2_call():
    d = json.load(sys.stdin)
    reqs, tokens, user, reviewer = d['requests'], d['tokens'], d['user'], d['reviewer']
    cs = calls(reqs)
    if len(cs) != 1:
        fail('2 call', f'the third party received {len(cs)} tools/call requests, want 1')
    c = cs[0]
    if c['status'] != 200:
        fail('2 call', f'the third party answered the call with HTTP {c["status"]}')
    if owner(tokens, bearer(c)) != user:
        fail('2 call', f'the call carried {owner(tokens, bearer(c))!r}\'s token, not {user}\'s')
    # a token for tools/call is the member's; the reviewer account's is for tools/list and only that
    for r in reqs:
        who = owner(tokens, bearer(r)) if bearer(r) else None
        m = body_of(r).get('method')
        if m == 'tools/call' and who != user:
            fail('2 call', f'a tools/call carried {who!r}\'s token')
        if m == 'tools/list' and who != reviewer:
            fail('2 call', f'a tools/list carried {who!r}\'s token, not the reviewer account\'s')
    names = sorted(k.lower() for k in c['headers'])
    extra = [k for k in names if k not in ALLOWED_HEADERS and not k.startswith('mcp-param-')]
    if extra:
        fail('2 call', f'headers that are not on the bridge\'s allow-list: {extra}')
    body = body_of(c)
    meta = (body.get('params') or {}).get('_meta') or {}
    if set(meta) - ALLOWED_META or set(body) - {'jsonrpc', 'id', 'method', 'params'} or set(body['params']) - {'name', 'arguments', '_meta'}:
        fail('2 call', f'the body carries more than the allow-list: top {sorted(body)}, params {sorted(body["params"])}, _meta {sorted(meta)}')
    raw = json.dumps(c['headers']) + c['body']
    for marker in ['io.idp.telos', 'caller-assertion', 'traceparent', 'idp-host', 'clientInfo'] + d['forbidden']:
        if marker.lower() in raw.lower():
            fail('2 call', f'what the third party received contains {marker!r}')
    print(f'the third party received one tools/call, HTTP 200, with {user}\'s own token (not the reviewer account\'s); headers {names}, _meta keys {sorted(meta)}, '
          f'none of: a caller assertion, io.idp.telos/ keys, trace headers, tenant or member ids; its tools/list requests carried only the reviewer account\'s token')


def event_values(events, name):
    out = []
    for e in events:
        ev = e.get('event', e)
        if ev.get('type') == 'CUSTOM' and ev.get('name') == name:
            out.append(ev.get('value') or {})
    return out


def authorization_required(events, connection, step):
    got = event_values(events, 'idp.host/authorization.required')
    if len(got) != 1:
        fail(step, f'want one idp.host/authorization.required event, got {len(got)}')
    v = got[0]
    if v.get('connection') != connection or not v.get('toolCallId') or not str(v.get('tool', '')).endswith('echo'):
        fail(step, f'the event is {v}')
    if set(v) - {'toolCallId', 'tool', 'connection'} or 'http' in json.dumps(v):
        fail(step, f'the event carries more than the connection id (keys {sorted(v)}): an address in it would be the third party\'s to choose')
    return v


def o3_alice():
    d = json.load(sys.stdin)
    v = authorization_required(d['events'], d['connection'], '3 alice')
    if calls(d['requests']):
        fail('3 alice', 'the third party received a tools/call although the member has no authorization')
    users = {owner(d['tokens'], bearer(r)) for r in d['requests'] if bearer(r)}
    if users - {d['reviewer']}:
        fail('3 alice', f'a request carried a token of {sorted(users - {d["reviewer"]})}')
    print(f'idp.host/authorization.required {{tool {v["tool"]}, connection {v["connection"]}}}, no address; the third party received {len(d["requests"])} requests in the window, '
          f'no tools/call, and none with a token that is not the reviewer account\'s')


def member_refreshes(d):
    """The refresh_token grants in the window, split by whose refresh token they present: the member's own, and the rest. The rest are the
    reviewer account's renewals, which nothing in the acceptance controls: /_test/expire expires its access token too, and the gateway's drift
    probe or the bridge's catalog refresh lists the tools with it at any moment. They are reported, not counted."""
    mine, others = [], []
    for r in grants_of(d['requests'], 'refresh_token'):
        (mine if owner(d['tokens'], form_of(r).get('refresh_token')) == d['user'] else others).append(r)
    return mine, others


def o4_refresh():
    d = json.load(sys.stdin)
    cs = calls(d['requests'])
    rf, others = member_refreshes(d)
    if len(rf) != 1:
        fail('4 refresh', f'want exactly one refresh_token grant of {d["user"]}\'s, got {len(rf)} (and {len(others)} of the reviewer account\'s)')
    if [c['status'] for c in cs] != [401, 200]:
        fail('4 refresh', f'the calls were answered {[c["status"] for c in cs]}, want [401, 200]: the expired token refused, then the new one accepted')
    if bearer(cs[0]) != d['old'] or bearer(cs[1]) == d['old'] or owner(d['tokens'], bearer(cs[1])) != d['user']:
        fail('4 refresh', 'the retry did not carry the new token of the member')
    if rf[0]['path'] != '/token' or 'refresh_token' not in rf[0]['body']:
        fail('4 refresh', 'not a refresh request')
    print(f'the issued access token expired: the bridge\'s call was refused (401), it refreshed once (grant_type=refresh_token, {d["user"]}\'s own refresh token), and the retry with the new token succeeded (200); '
          f'the reviewer account\'s renewals are not counted ({len(others)} in the window)')


def o4_proactive():
    d = json.load(sys.stdin)
    cs = calls(d['requests'])
    rf, others = member_refreshes(d)
    if len(rf) != 1 or [c['status'] for c in cs] != [200]:
        fail('4 proactive', f'want one refresh of {d["user"]}\'s and one call answered 200, got {len(rf)} (and {len(others)} of the reviewer account\'s) and calls {[c["status"] for c in cs]}')
    if owner(d['tokens'], bearer(cs[0])) != d['user']:
        fail('4 proactive', 'the call did not carry the member\'s token')
    print(f'a token the third party had issued with a 2 second life: the bridge refreshed it before the call (one refresh_token grant, rotated refresh token used) and the one call was accepted (200), none refused; the reviewer account\'s renewals are not counted ({len(others)} in the window)')


def o4_scope():
    d = json.load(sys.stdin)
    v = authorization_required(d['events'], 'thirdparty', '4 scope')
    c = [x for x in d['connections'] if x['id'] == 'thirdparty']
    if not c or c[0]['status'] != 'reauthorization_required' or c[0].get('reason') != 'insufficient_scope':
        fail('4 scope', f'the member\'s list says {c}, want reauthorization_required / insufficient_scope')
    if not [r for r in d['rows'] if r['connection'] == 'thirdparty' and r['member'] != d['reviewer_member']]:
        fail('4 scope', 'the member\'s grant was deleted: insufficient_scope keeps it')
    cs = calls(d['requests'])
    if [x['status'] for x in cs] != [403]:
        fail('4 scope', f'the calls were answered {[x["status"] for x in cs]}, want [403]')
    print(f'insufficient_scope: the third party answered 403, the run got idp.host/authorization.required for {v["connection"]}, the list says reauthorization_required (insufficient_scope), the grant is kept')


def o5_iss():
    d = json.load(sys.stdin)
    if d['status'] != 400:
        fail('5 iss', f'the callback with a wrong iss was answered HTTP {d["status"]}: {d["body"][:200]}')
    if 'authorization_failed' not in d['body'].lower() and 'iss' not in d['body'].lower() and 'authorization' not in d['body'].lower():
        fail('5 iss', f'the refusal does not say that the authorization failed: {d["body"][:200]}')
    if grants_of(d['requests'], 'authorization_code'):
        fail('5 iss', 'the bridge exchanged the code at the token endpoint: an unchecked iss must stop it before')
    if d['rows']:
        fail('5 iss', f'the bridge stored a grant: {d["rows"]}')
    print(f'a callback with iss "{d["iss"]}": refused (HTTP 400 {d["body"][:80].strip()}), the code was never exchanged, the member has no grant row')


def o6_revoke():
    d = json.load(sys.stdin)
    revs = [r for r in d['requests'] if r['path'] == '/revoke']
    sent = {form_of(r).get('token') for r in revs}
    mine = [t for t in d['tokens'] if t['user'] == d['user'] and t['resource'] == d['resource']]
    live_before = [t for t in mine if t['value'] in d['live']]
    if len(d['live']) < 2 or not set(d['live']) <= sent:
        fail('6 revoke', f'the third party received {len(revs)} revocation requests; the member\'s live access and refresh tokens ({len(d["live"])}) were not all among them')
    if any(not t['revoked'] for t in mine if t['value'] in d['live']):
        fail('6 revoke', 'a token that was live is still not revoked at the third party')
    if d['rows']:
        fail('6 revoke', f'the grant row is still in the database: {d["rows"]}')
    kinds = sorted({t['kind'] for t in live_before})
    print(f'{d["user"]} disconnected: the third party received {len(revs)} revocation requests, for the live access and refresh token ({kinds}), and its records say they are revoked; the grant row is deleted')


def forms(s):
    return {'plain': s, 'hex': s.encode().hex(), 'base64': base64.b64encode(s.encode()).decode().rstrip('='),
            'base64url': base64.urlsafe_b64encode(s.encode()).decode().rstrip('='), 'urlencoded': urllib.parse.quote(s, safe='')}


def search(sources, secrets_):
    found = []
    for source, text in sources.items():
        low = text.lower()
        for s in secrets_:
            for name, f in forms(s).items():
                if f in text or (name == 'hex' and f.lower() in low):
                    found.append(f'{source}: {name} form of {s[:6]}...')
    return found


def o7_secrets():
    d = json.load(sys.stdin)
    secrets_ = d['secrets']
    kinds = {p: sum(1 for s in secrets_ if s.startswith(p)) for p in ('tpat_', 'tprt_', 'tpcode_')}
    if min(kinds.values()) < 2 or len(secrets_) < 8:
        fail('7 secrets', f'the third party issued too few secrets to make the search worth anything: {kinds}')
    # the search must be able to find one: plant a secret in each form, in a text of its own
    for name, f in forms(secrets_[0]).items():
        if not search({'planted': 'before ' + f + ' after'}, secrets_[:1]):
            fail('7 secrets', f'the search itself does not find the {name} form of a secret')
    sizes = {k: len(v) for k, v in d['sources'].items()}
    # a container that says nothing has an empty log; these must not be empty, or the search would prove nothing
    must = [r for r in d.get('required', []) if not any(k.startswith(r) and n > 0 for k, n in sizes.items())]
    if must:
        fail('7 secrets', f'nothing was read from {must}: the search would prove nothing')
    found = search(d['sources'], secrets_)
    if found:
        fail('7 secrets', f'{len(found)} places hold what the third party issued: {found[:6]}')
    print(f'searched {len(d["sources"])} sources ({sum(sizes.values()) // 1024} KiB: every container\'s log, the events of every run, the databases of the bridge, the Workspace, the HOST and the registry) '
          f'for {len(secrets_)} secrets the third party issued ({kinds["tpat_"]} access tokens, {kinds["tprt_"]} refresh tokens, {kinds["tpcode_"]} codes, the client secret) in plain, hex, base64 and URL-encoded form: none found')


def listen(port, timeout, state=None):
    """R1: the redirect of the scripted callback. The third party redirects the member's browser to http://127.0.0.1:PORT/oauth/callback?code=..&state=..&iss=..;
    this answers that one request with a plain page and prints code, state, iss and error (those four, as the Workspace's callback route takes them) as JSON.
    With STATE (the one in the authorization address just obtained) only a redirect that carries it is taken; any other redirect to the callback path, such as a
    reloaded tab of an earlier authorization, is answered with a plain page and ignored (a line says so, never the state or the code), and the wait goes on."""
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
            ok = u.path == '/oauth/callback' and ('code' in q or 'error' in q)
            if ok and state and q.get('state') != state:
                print('ignored a redirect with another state', file=sys.stderr, flush=True)
                body, status = b'<!doctype html><meta charset=utf-8><p>This is an old authorization link: use the newest one the script printed.</p>', 200
                ok = False
            else:
                status = 200 if ok else 404
                body = (b'<!doctype html><meta charset=utf-8><p>Done, you can close this tab. Do not reload it: an authorization is used once.</p>' if ok else b'not here')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)
            if ok:
                got.update({k: q[k] for k in ('code', 'state', 'iss', 'error') if k in q})

    server = HTTPServer(('127.0.0.1', int(port)), H)
    server.timeout = 1
    deadline = time.time() + float(timeout)
    while not got and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if not got:
        print(f'FAIL: PATH {PATH}: no redirect came to 127.0.0.1:{port} within {timeout} s', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(got))


def unseal(sealed, keys, tenant, member, connection, reviewer):
    """What idp-mcp-bridge's sealer (internal/sealer) made of a token: version 1, the length and the id of the key, the nonce, AES-256-GCM with the
    owner (tenant, member, connection, member or reviewer) and the key id as additional data. Only the acceptance's check opens it, in memory."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(sealed) < 2 or sealed[0] != 1:
        raise ValueError('not a sealed value')
    kid = sealed[2:2 + sealed[1]].decode()
    nonce, body = sealed[2 + sealed[1]:14 + sealed[1]], sealed[14 + sealed[1]:]
    aad = b''.join(len(x).to_bytes(2, 'big') + x.encode() for x in ('idp-mcp-bridge/v1', tenant, member, connection, 'reviewer' if reviewer else 'member', kid))
    return AESGCM(keys[kid]).decrypt(nonce, body, aad).decode()


def gh_secrets(keys_file, secret_file, sources_dir):
    """R1 step d: neither the App's client secret nor any token the bridge holds is in any file of sources_dir (the logs, the runs' events, the
    databases). The tokens are read inside this check, from the grants rows on stdin, and are not printed."""
    global PATH
    PATH = 'R1'
    rows = json.load(sys.stdin)['rows']
    keys = {}
    for line in open(keys_file).read().splitlines():
        if line.strip() and not line.startswith('#'):
            kid, enc = line.split(':', 1)
            keys[kid.strip()] = base64.b64decode(enc.strip())
    held = []
    for r in rows:
        for col in ('sealed_access', 'sealed_refresh'):
            if r.get(col):
                held.append((r['connection'], bool(r['reviewer']), col, unseal(bytes.fromhex(r[col]), keys, r['tenant'], r['member'], r['connection'], r['reviewer'])))
    tokens = [t for *_, t in held]
    if len(tokens) < 2 or any(len(t) < 16 for t in tokens):
        fail('d secrets', f'the bridge holds {len(tokens)} tokens that opened: too few, or too short, to search for')
    tp = os.environ.get('THIRDPARTY_TOKENS_URL')
    if tp:   # a stand-in third party: what the bridge holds must be what it issued, which proves the unsealing above
        import urllib.request
        issued = {t['value'] for t in json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(tp, timeout=10))}
        wrong = [c for c, _, col, t in held if t not in issued]
        if wrong:
            fail('d secrets', f'tokens the bridge holds that the stand-in did not issue: {wrong}')
    client_secret = open(secret_file).read().strip()
    secrets_ = tokens + ([client_secret] if client_secret else [])
    sources = {f: open(os.path.join(sources_dir, f), errors='replace').read() for f in sorted(os.listdir(sources_dir))}
    for name, f in forms(secrets_[0]).items():
        if not search({'planted': 'a ' + f + ' b'}, secrets_[:1]):
            fail('d secrets', f'the search itself does not find the {name} form of a secret')
    found = search(sources, secrets_)
    if found:
        fail('d secrets', f'{len(found)} places hold a secret: {[x.split(" form")[0] for x in found[:6]]}')
    print(f'searched {len(sources)} files ({sum(map(len, sources.values())) // 1024} KiB: every container\'s log, the events of the runs, the databases) for the client secret and {len(tokens)} tokens '
          f'the bridge holds ({sum(1 for _, rv, *_ in held if rv) // 1} of them the reviewer account\'s) in plain, hex, base64 and URL-encoded form: none found')


if __name__ == '__main__':
    cmd, *args = sys.argv[1:]
    if cmd == 'consent':
        consent(*args)
    elif cmd == 'listen':
        listen(*args)
    elif cmd == 'gh-secrets':
        gh_secrets(*args)
    else:
        {'auth-url': auth_url, 'o1-sealed': o1_sealed, 'o2-call': o2_call, 'o3-alice': o3_alice, 'o4-refresh': o4_refresh, 'o4-proactive': o4_proactive,
         'o4-scope': o4_scope, 'o5-iss': o5_iss, 'o6-revoke': o6_revoke, 'o7-secrets': o7_secrets}[cmd](*args)
