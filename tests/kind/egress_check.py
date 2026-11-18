#!/usr/bin/env python3
"""PATH L (idp-sandbox ADR-0001 D21, D22): a member's code reaches the host on its tenant's allow-list, through the
sandbox's egress proxy and through nothing else, on the platform as deployed on the cluster.

bob signs in; his token is exchanged for the idp-mcp gateway exactly as idp-host does it for a run (the same client, an
actor token); the sandbox's tools are then called at the gateway, which verifies the token and forwards bob and his
tenant to the sandbox. The model is left out on purpose: which network a run gets is an argument of sandbox_run, and
the path under test begins at the gateway.

  a run with network=egress   the listed origin answers; a host that is not listed is refused by the proxy (403); a
                              connection that does not go through the proxy gets nowhere
  a run without it            has no proxy in its environment and cannot reach the proxy either

Exit 0 and one line of evidence, or exit 1 with the step that failed.

    python3 -I tests/kind/egress_check.py ISSUER CA_FILE CLIENTS_JSON GATEWAY_AUDIENCE GATEWAY_URL ORIGIN_HOST ORIGIN_IP PROXY_IP UNLISTED_HOST

GATEWAY_AUDIENCE is the gateway's canonical MCP URI (the token's audience, and the Host it answers to); GATEWAY_URL is
where this script reaches it (a port-forward). ORIGIN_IP and PROXY_IP are cluster IPs: a run has no DNS.
"""
import json, ssl, sys, urllib.error, urllib.parse, urllib.request

issuer, ca, clients_file, audience, gateway, origin, origin_ip, proxy_ip, unlisted = sys.argv[1:10]
c = json.load(open(clients_file))
kc = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
gw = urllib.request.build_opener(urllib.request.ProxyHandler({}))
X, AT = 'urn:ietf:params:oauth:grant-type:token-exchange', 'urn:ietf:params:oauth:token-type:access_token'
BODY = 'hello from the egress origin'
META = {'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientInfo': {'name': 'idp-telos-kind-full', 'version': '1'},
        'io.modelcontextprotocol/clientCapabilities': {}}


def must(step, ok, detail):
    if not ok:
        print(f'FAIL: PATH L {step}: {detail}', file=sys.stderr)
        sys.exit(1)


def post(form):
    try:
        with kc.open(urllib.request.Request(issuer + '/protocol/openid-connect/token', urllib.parse.urlencode(form).encode()), timeout=20) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def tool(token, name, args):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args, '_meta': META}}).encode()
    req = urllib.request.Request(gateway, body, {
        'Host': urllib.parse.urlparse(audience).netloc,   # the gateway answers to its own name only
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'tools/call', 'Mcp-Name': name})
    try:
        with gw.open(req, timeout=120) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        must(name, False, f'HTTP {e.code} {e.read()[:300]!r}')
    must(name, 'result' in out and not out['result'].get('isError'), out)
    return out['result'].get('structuredContent') or {}


st, bob = post({'grant_type': 'password', 'client_id': 'e2e-cli', 'client_secret': c['e2e-cli'], 'username': 'bob', 'password': c['users']['bob']})
must('sign-in', st == 200, bob)
st, actor = post({'grant_type': 'client_credentials', 'client_id': 'idp-host', 'client_secret': c['idp-host']})
must('idp-host actor token', st == 200, actor)
st, tok = post({'grant_type': X, 'client_id': 'idp-host', 'client_secret': c['idp-host'], 'subject_token': bob['access_token'], 'subject_token_type': AT,
                'audience': audience, 'actor_token': actor['access_token'], 'actor_token_type': AT})
must('exchange for the gateway', st == 200, tok.get('error_description', tok))
token = tok['access_token']

ws = tool(token, 'sandbox__sandbox_create', {}).get('workspaceId')
must('sandbox_create', bool(ws), ws)

# the code of a run: the standard client (it reads HTTP_PROXY, the run's credential included), and a plain socket
CODE = f'''
import os, socket, urllib.error, urllib.request
def get(url):
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return "%d %s" % (r.status, r.read().decode())
    except urllib.error.HTTPError as e:
        return "HTTP %d" % e.code
    except Exception as e:
        return "ERR %s" % type(e).__name__
def tcp(host, port):
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect((host, port)); return "CONNECTED"
    except OSError:
        return "blocked"
print("proxy-set:", bool(os.environ.get("HTTP_PROXY")))
if os.environ.get("HTTP_PROXY"):   # by name only through the proxy: a run has no DNS, and its resolver's timeouts outlast a run
    print("listed:", get("http://{origin}/"))
    print("unlisted:", get("http://{unlisted}/"))
print("around-the-proxy:", tcp("{origin_ip}", 80))
print("to-the-proxy:", tcp("{proxy_ip}", 3128))
'''


def lines(result):
    return dict(l.split(': ', 1) for l in str(result.get('stdout', '')).splitlines() if ': ' in l)


out = tool(token, 'sandbox__sandbox_run', {'workspaceId': ws, 'language': 'python', 'code': CODE, 'network': 'egress'})
got = lines(out)
must('a run with network=egress', out.get('exitCode') == 0 and got.get('proxy-set') == 'True', out)
must('the listed host through the proxy', got.get('listed') == '200 ' + BODY, got)
must('a host that is not listed', got.get('unlisted') == 'HTTP 403', got)
must('around the proxy', got.get('around-the-proxy') == 'blocked', got)

out = tool(token, 'sandbox__sandbox_run', {'workspaceId': ws, 'language': 'python', 'code': CODE})
plain = lines(out)
must('a run without network=egress', out.get('exitCode') == 0 and plain.get('proxy-set') == 'False'
     and plain.get('to-the-proxy') == 'blocked' and plain.get('around-the-proxy') == 'blocked', out)

tool(token, 'sandbox__sandbox_delete', {'workspaceId': ws})
print(f"network=egress: {origin} -> {got['listed']!r}, {unlisted} -> {got['unlisted']}, the origin's address directly -> {got['around-the-proxy']}; "
      f"without it: no proxy in the environment, the proxy's address -> {plain['to-the-proxy']}")
