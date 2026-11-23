"""Register a connection of idp-mcp-bridge at idp-mcp for the platform acceptance (DEV ONLY, driven by deploy/compose/e2e.sh and
tests/kind/full.sh, PATH O step 0): the connection is a server of class external at the bridge's endpoint, discovered (with the
reviewer account's token: the third party wants one even to list its tools, so the operator's authorization of that account comes
first), reviewed (an external server is approved with the digest of the catalog the reviewer read), granted to ROLE (the role of
the HOST's delegated tokens: the third party's tools are the HOST's, as the web tools are) and put into a new release, which is
deployed and attested by the gateway.

Usage: python3 -I deploy/compose/bridge_setup.py REGISTRY_URL GATEWAY_ID OPERATOR_TOKEN REVIEWER_TOKEN ADMIN_TOKEN ROLE SERVER_ID TOOL [TOOL...]
(the server is registered by the caller, at the connection's endpoint at the bridge: IDP_BRIDGE_BASE_URL + /mcp/<id>, character for
character, because it is the audience of the gateway's caller assertion; PATH O registers it first and shows that discovery is refused
until the reviewer account is authorized.) Prints the digest of the catalog that was approved.
"""
import json
import sys
import time
import urllib.error
import urllib.request

reg, gateway_id, operator, reviewer, admin, role, sid, *tools = sys.argv[1:]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(reg + path, data=data, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with opener.open(req, timeout=120) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


def must(token, method, path, body=None, want=(200, 201)):
    status, out = api(token, method, path, body)
    if status not in want:
        print(f'{method} {path}: HTTP {status} {out}', file=sys.stderr)
        sys.exit(2)
    return out


found = set(must(operator, 'POST', f'/api/v1/servers/{sid}/discover').get('toolNames') or [])
missing = set(tools) - found
if missing:
    print(f'the bridge did not offer {sorted(missing)} (it offered {sorted(found)})', file=sys.stderr)
    sys.exit(2)
digest = must(reviewer, 'GET', f'/api/v1/servers/{sid}/catalog')['latest']['digest']
must(reviewer, 'POST', f'/api/v1/servers/{sid}/approve', {'digest': digest})
g = must(operator, 'POST', '/api/v1/grants', {'serverId': sid, 'roles': [role], 'tools': tools})
must(reviewer, 'POST', f"/api/v1/grants/{g['grantId']}/approve")
rel = must(operator, 'POST', '/api/v1/releases', {'gatewayId': gateway_id})
must(reviewer, 'POST', f"/api/v1/releases/{rel['releaseId']}/approve")
must(admin, 'POST', f"/api/v1/releases/{rel['releaseId']}/deploy")
for _ in range(60):
    if must(reviewer, 'GET', f"/api/v1/releases/{rel['releaseId']}")['status'] == 'GOOD':
        break
    time.sleep(1)
else:
    print('release never became GOOD', file=sys.stderr)
    sys.exit(2)
print(f"OK: server {sid} (class external) approved at catalog {digest[:19]}, release {rel['releaseId']} GOOD")
