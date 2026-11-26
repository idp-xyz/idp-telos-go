#!/usr/bin/env python3
"""PATH P2, the tokens (idp-cmi D67, D70; idp-mcp D6, D46 to D48): the system gateway serves one client, the CMI's ingest identity, and nobody else gets anything from it.

The positive side: the CMI's own token (client credentials of idp-cmi-ingest) carries the system tenant, the role cmi-ingest, the audience of the system gateway and the
sub that is the CMI's workloadSubject (the gateway's subject), and the system gateway lists to it the five tools of a conversion (create, import, run, export, delete) with the
catalog of idp-sandbox D25 (the run tool's description names ooxml2md and pdftext --markdown: the catalog that was reviewed), and no other tool. `ingest__*` is therefore not host-only.

The negative side, each against the system gateway: HOST's delegated token (bob's token exchanged by idp-host with its actor token, as for a run), a member's own token (bob,
e2e-cli), and idp-probe's exchanged token (a platform client that is not the HOST) are all refused (HTTP 401: their audience is not the system gateway's) and none of them lists or
calls an ingest tool; asking Keycloak for a token of the system gateway's audience as idp-host or idp-probe is refused by Keycloak. And the other direction: on the member-side
gateway (the one the HOST uses) the HOST's token lists no ingest__ tool, and the CMI's token is refused.

Exit 0 and one line of evidence, or exit 1 with the step that failed.

  python3 -I deploy/compose/ingest_check.py ISSUER CA_FILE CLIENTS_JSON SYSTEM_AUDIENCE SYSTEM_URL EDGE_AUDIENCE EDGE_URL SYSTEM_TENANT WORKLOAD_SUBJECT

*_AUDIENCE is a gateway's canonical MCP URI (the token's audience and the Host it answers to); *_URL is where this script reaches it (a published port or a port-forward).
"""
import base64
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

issuer, ca, clients_file, sys_aud, sys_url, edge_aud, edge_url, system_tenant, subject = sys.argv[1:10]
c = json.load(open(clients_file))
kc = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
gw = urllib.request.build_opener(urllib.request.ProxyHandler({}))
X, AT = 'urn:ietf:params:oauth:grant-type:token-exchange', 'urn:ietf:params:oauth:token-type:access_token'
META = {'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientInfo': {'name': 'idp-telos-acceptance', 'version': '1'},
        'io.modelcontextprotocol/clientCapabilities': {}}
TOOLS = ['ingest__sandbox_create', 'ingest__sandbox_delete', 'ingest__sandbox_export_file', 'ingest__sandbox_import_file', 'ingest__sandbox_run']


def must(step, ok, detail):
    if not ok:
        print(f'FAIL: PATH P2 tokens: {step}: {detail}', file=sys.stderr)
        sys.exit(1)


def post(form):
    try:
        with kc.open(urllib.request.Request(issuer + '/protocol/openid-connect/token', urllib.parse.urlencode(form).encode()), timeout=20) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def rpc(url, audience, token, method, params):
    """(HTTP status, JSON body or None)"""
    name = params.get('name', '')
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {**params, '_meta': META}}).encode()
    headers = {'Host': urllib.parse.urlparse(audience).netloc, 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
               'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': method}
    if name:
        headers['Mcp-Name'] = name
    try:
        with gw.open(urllib.request.Request(url, body, headers), timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, None


def claims(token):
    p = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))


def listed(url, audience, token):
    st, out = rpc(url, audience, token, 'tools/list', {})
    names = sorted(t['name'] for t in ((out or {}).get('result') or {}).get('tools', [])) if st == 200 else []
    return st, names, out


# ---- the CMI's own identity
st, cmi = post({'grant_type': 'client_credentials', 'client_id': 'idp-cmi-ingest', 'client_secret': c['idp-cmi-ingest']})
must('the CMI client-credentials token', st == 200, cmi)
cl = claims(cmi['access_token'])
aud = cl.get('aud') if isinstance(cl.get('aud'), list) else [cl.get('aud')]
must('the CMI token names the system tenant', cl.get('tenant_id') == system_tenant, cl.get('tenant_id'))
must('the CMI token has the role cmi-ingest and no other', cl.get('roles') == ['cmi-ingest'], cl.get('roles'))
must('the CMI token is for the system gateway', sys_aud in aud and edge_aud not in aud, aud)
must("the CMI token's sub is the workloadSubject (the subject the gateway asserts: idp-mcp takes it from `sub`)", cl.get('sub') == subject, cl.get('sub'))
must("the CMI token's preferred_username is the service account's", cl.get('preferred_username') == 'service-account-idp-cmi-ingest', cl.get('preferred_username'))
must('the CMI token is the client idp-cmi-ingest (azp)', cl.get('azp') == 'idp-cmi-ingest', cl.get('azp'))
st, names, out = listed(sys_url, sys_aud, cmi['access_token'])
must('the system gateway lists the five tools of a conversion to the CMI', names == TOOLS, (st, names, out))
run = next(t for t in out['result']['tools'] if t['name'] == 'ingest__sandbox_run')
must("the reviewed run tool is idp-sandbox D25's (it names the converters)", 'ooxml2md' in json.dumps(run) and 'FILE.pdf --markdown' in json.dumps(run), run.get('description', '')[:200])
st, err = rpc(sys_url, sys_aud, cmi['access_token'], 'tools/call', {'name': 'ingest__sandbox_write_file', 'arguments': {}})
must('a tool that is in the catalog but granted to nobody is not callable by the CMI either', 'result' not in (err or {}) and st in (200, 403), (st, err))

# ---- the others, against the system gateway
st, bob = post({'grant_type': 'password', 'client_id': 'e2e-cli', 'client_secret': c['e2e-cli'], 'username': 'bob', 'password': c['users']['bob']})
must('sign-in of bob', st == 200, bob)
st, actor = post({'grant_type': 'client_credentials', 'client_id': 'idp-host', 'client_secret': c['idp-host']})
must('idp-host actor token', st == 200, actor)
st, host = post({'grant_type': X, 'client_id': 'idp-host', 'client_secret': c['idp-host'], 'subject_token': bob['access_token'], 'subject_token_type': AT,
                 'audience': edge_aud, 'actor_token': actor['access_token'], 'actor_token_type': AT})
must("HOST's delegated token (exchange as idp-host with its actor token)", st == 200, host.get('error_description', host))
st, probe = post({'grant_type': X, 'client_id': 'idp-probe', 'client_secret': c['idp-probe'], 'subject_token': bob['access_token'], 'subject_token_type': AT, 'audience': edge_aud})
must("idp-probe's exchanged token", st == 200, probe.get('error_description', probe))
seen = []
for who, token in (("HOST's delegated token", host['access_token']), ("bob's own token", bob['access_token']), ("idp-probe's exchanged token", probe['access_token'])):
    st, names, out = listed(sys_url, sys_aud, token)
    must(f'{who}: the system gateway refuses it', st in (401, 403) and not names, (st, names, out))
    st2, out2 = rpc(sys_url, sys_aud, token, 'tools/call', {'name': 'ingest__sandbox_create', 'arguments': {}})
    must(f'{who}: ingest__sandbox_create is not called', st2 in (401, 403) and 'result' not in (out2 or {}), (st2, out2))
    seen.append(f'{who.split(" ")[0]} {st}/{st2}')
for who, client in (('idp-host', 'idp-host'), ('idp-probe', 'idp-probe')):
    st, out = post({'grant_type': X, 'client_id': client, 'client_secret': c[client], 'subject_token': bob['access_token'], 'subject_token_type': AT, 'audience': sys_aud})
    must(f'Keycloak does not issue {who} a token for the system gateway (exchange of bob)', st != 200 or sys_aud not in ((claims(out['access_token']).get('aud')) or []), (st, out))

# ---- and the member-side gateway
st, host_names, out = listed(edge_url, edge_aud, host['access_token'])
names = host_names
must("HOST's token lists tools on the member-side gateway and none of them is ingest__", st == 200 and names and not [n for n in names if n.startswith('ingest__')], (st, names))
st, out = rpc(edge_url, edge_aud, host['access_token'], 'tools/call', {'name': 'ingest__sandbox_create', 'arguments': {}})
must('ingest__sandbox_create does not exist on the member-side gateway', 'result' not in (out or {}), (st, out))
st, names, out = listed(edge_url, edge_aud, cmi['access_token'])
must("the CMI's token is refused by the member-side gateway", st in (401, 403) and not names, (st, names))
print(f"the CMI's token (tenant {system_tenant[:8]}…, role cmi-ingest, azp idp-cmi-ingest, sub {subject[:8]}…, preferred_username service-account-idp-cmi-ingest) lists the five ingest__ tools on the system gateway, the run tool's catalog names ooxml2md and pdftext --markdown, "
      f"ingest__sandbox_write_file is not callable; refused by the system gateway (HTTP status of tools/list/tools/call): {', '.join(seen)}; Keycloak issues the system audience to neither idp-host nor idp-probe; "
      f"on the member-side gateway the HOST lists {len(host_names)} tools and none is ingest__, and the CMI's token is refused")
