#!/usr/bin/env python3
"""PATH M2 (idp-mcp ADR-0001 D46 to D48): the web tools are the HOST's. A member whose token reaches the gateway through another
client sees none of them and cannot call them, whatever her role says; the HOST's own exchanged token sees them.

bob signs in. His token is exchanged for the gateway twice, as two clients with the same audience, user and roles (both carry
host-user, the role the web tools are granted to, deploy/compose/mcp_setup.py):

  idp-host    the HOST (a platform client with host: true), with its actor token as it does for a run: tools/list shows the
              three web tools
  idp-probe   a platform client that is not the host (acceptance only, deploy/keycloak/realm.py): tools/list shows the sandbox's
              tools its role allows and no web__ tool; web__web_search is refused with the answer given for a tool that does not
              exist

Exit 0 and one line of evidence, or exit 1 with the step that failed.

    python3 -I deploy/compose/client_check.py ISSUER CA_FILE CLIENTS_JSON GATEWAY_AUDIENCE GATEWAY_URL

GATEWAY_AUDIENCE is the gateway's canonical MCP URI (the token's audience, and the Host it answers to); GATEWAY_URL is where this
script reaches it (a published port or a port-forward).
"""
import json, ssl, sys, urllib.error, urllib.parse, urllib.request

issuer, ca, clients_file, audience, gateway = sys.argv[1:6]
c = json.load(open(clients_file))
kc = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
gw = urllib.request.build_opener(urllib.request.ProxyHandler({}))
X, AT = 'urn:ietf:params:oauth:grant-type:token-exchange', 'urn:ietf:params:oauth:token-type:access_token'
META = {'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientInfo': {'name': 'idp-telos-acceptance', 'version': '1'},
        'io.modelcontextprotocol/clientCapabilities': {}}


def must(step, ok, detail):
    if not ok:
        print(f'FAIL: PATH M2 {step}: {detail}', file=sys.stderr)
        sys.exit(1)


def post(form):
    try:
        with kc.open(urllib.request.Request(issuer + '/protocol/openid-connect/token', urllib.parse.urlencode(form).encode()), timeout=20) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def rpc(token, method, params):
    name = params.get('name', '')
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {**params, '_meta': META}}).encode()
    headers = {'Host': urllib.parse.urlparse(audience).netloc,   # the gateway answers to its own name only
               'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': method}
    if name:
        headers['Mcp-Name'] = name
    try:
        with gw.open(urllib.request.Request(gateway, body, headers), timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        must(method, False, f'HTTP {e.code} {e.read()[:300]!r}')


st, bob = post({'grant_type': 'password', 'client_id': 'e2e-cli', 'client_secret': c['e2e-cli'], 'username': 'bob', 'password': c['users']['bob']})
must('sign-in', st == 200, bob)
st, actor = post({'grant_type': 'client_credentials', 'client_id': 'idp-host', 'client_secret': c['idp-host']})
must('idp-host actor token', st == 200, actor)
st, host = post({'grant_type': X, 'client_id': 'idp-host', 'client_secret': c['idp-host'], 'subject_token': bob['access_token'], 'subject_token_type': AT,
                 'audience': audience, 'actor_token': actor['access_token'], 'actor_token_type': AT})
must('exchange as idp-host', st == 200, host.get('error_description', host))
st, probe = post({'grant_type': X, 'client_id': 'idp-probe', 'client_secret': c['idp-probe'], 'subject_token': bob['access_token'], 'subject_token_type': AT,
                  'audience': audience})
must('exchange as idp-probe', st == 200, probe.get('error_description', probe))


def tools(token):
    out = rpc(token, 'tools/list', {})
    must('tools/list', 'result' in out, out)
    return sorted(t['name'] for t in out['result']['tools'])


host_tools = tools(host['access_token'])
web = [t for t in host_tools if t.startswith('web__')]
must('the HOST sees the web tools', 'web__web_search' in web and 'web__web_fetch' in web, host_tools)
probe_tools = tools(probe['access_token'])
must('another platform client sees no web tool', not [t for t in probe_tools if t.startswith('web__')], probe_tools)
must('...and still sees what its role allows (it is listed, not shut out)', 'sandbox__sandbox_run' in probe_tools, probe_tools)
refused = rpc(probe['access_token'], 'tools/call', {'name': 'web__web_search', 'arguments': {'query': 'anything'}})
must('another platform client calling web__web_search', 'result' not in refused and 'Unknown tool' in json.dumps(refused.get('error')), refused)
print(f'bob with the same roles: idp-host sees {web}; idp-probe (platform, not the host) sees {len(probe_tools)} tools, none of them web__, and web__web_search is refused ("Unknown tool")')
