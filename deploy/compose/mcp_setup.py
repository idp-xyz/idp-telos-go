"""Provision idp-mcp for the platform acceptance (DEV ONLY, driven by deploy/compose/e2e.sh): register the
sandbox as an upstream that receives the verified caller (identity=forward), have it discovered and reviewed,
grant its tools to the role the HOST's delegated tokens carry, compile, review and deploy a release, and wait
until the gateway attests it.

Usage: python3 -I deploy/compose/mcp_setup.py REGISTRY_URL GATEWAY_ID ADMIN_TOKEN OPERATOR_TOKEN REVIEWER_TOKEN ROLE [SANDBOX_ENDPOINT]
(SANDBOX_ENDPOINT defaults to the compose service; tests/kind/full.sh passes the sandbox's Service URL.)
"""
import json
import sys
import time
import urllib.error
import urllib.request

reg, gateway_id, admin, operator, reviewer, role = sys.argv[1:7]
sandbox = sys.argv[7] if len(sys.argv) > 7 else 'http://sandbox:8570/mcp'
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


s = must(operator, 'POST', '/api/v1/servers', {'displayName': 'sandbox', 'targetName': 'sandbox', 'endpoint': sandbox, 'identity': 'forward'})
sid = s['serverId']
found = set(must(operator, 'POST', f'/api/v1/servers/{sid}/discover').get('toolNames') or [])
must(reviewer, 'POST', f'/api/v1/servers/{sid}/approve')
tools = ['sandbox_create', 'sandbox_run', 'sandbox_write_file', 'sandbox_read_file', 'sandbox_list_files', 'sandbox_delete']
# a member's files (idp-sandbox D15): offered only where the sandbox has the Workspace's file endpoint (compose), not on kind
tools += [t for t in ('sandbox_import_file', 'sandbox_export_file') if t in found]
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
print('OK: release', rel['releaseId'], 'GOOD')
