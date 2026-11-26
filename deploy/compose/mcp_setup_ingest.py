"""Provision the SYSTEM gateway of the knowledge-base conversion (idp-mcp ADR-0001 D6: one gateway per tenant; idp-cmi D61, D67, D70; platform PATH P2).
DEV ONLY, driven by deploy/compose/e2e.sh and tests/kind/full.sh with the registry tokens of the SYSTEM tenant (not the members' tenant): register the
ingest sandbox as the one target `ingest` (identity=forward, so the sandbox receives the verified caller: the CMI's workload identity), have its catalog
discovered, read and approved by a second person, grant the five tools of one conversion to the role `cmi-ingest` and to no other role, compile, review and
deploy a release, and wait until the gateway attests it.

Usage: python3 -I deploy/compose/mcp_setup_ingest.py REGISTRY_URL GATEWAY_ID ADMIN_TOKEN OPERATOR_TOKEN REVIEWER_TOKEN ROLE INGEST_ENDPOINT

The catalog is pinned at discovery. idp-sandbox D25 changed the description of `sandbox_run` (the office profile now names `ooxml2md` and
`pdftext --markdown`), so a gateway that was approved before that catalog has to discover it again, have it reviewed again and publish a release again
(the registry sends a server whose catalog differs back to REVIEWED, and the drift check refuses the calls until then). A new system gateway has no earlier
approval: this is its first. The script checks that what the reviewer approves is the catalog that names the converters.

Only the five tools of a conversion are granted (create, import, run, export, delete): the sandbox's other tools (write, read, list files) are in the catalog,
approved with it, and granted to nobody. `ingest__*` is NOT a host-only tool of any gateway: the CMI is not the HOST, and the gateway of the system
tenant has exactly one client, which is the CMI (deploy/compose/e2e.sh and tests/kind/full.sh write its clients file).
"""
import json
import sys
import time
import urllib.error
import urllib.request

reg, gateway_id, admin, operator, reviewer, role, endpoint = sys.argv[1:8]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOOLS = ['sandbox_create', 'sandbox_import_file', 'sandbox_run', 'sandbox_export_file', 'sandbox_delete']


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


s = must(operator, 'POST', '/api/v1/servers', {'displayName': 'ingest', 'targetName': 'ingest', 'endpoint': endpoint, 'identity': 'forward'})
sid = s['serverId']
found = set(must(operator, 'POST', f'/api/v1/servers/{sid}/discover').get('toolNames') or [])
missing = set(TOOLS) - found
if missing:
    print(f'the ingest sandbox did not offer {sorted(missing)} (it needs IDP_SANDBOX_FILES_URL and the office image)', file=sys.stderr)
    sys.exit(2)
catalog = json.dumps(must(reviewer, 'GET', f'/api/v1/servers/{sid}/catalog'))
if 'ooxml2md' not in catalog or '--markdown' not in catalog:
    print('the catalog the reviewer would approve does not name ooxml2md and the --markdown switch of pdftext: this is not the sandbox of idp-sandbox D25', file=sys.stderr)
    sys.exit(2)
must(reviewer, 'POST', f'/api/v1/servers/{sid}/approve')
g = must(operator, 'POST', '/api/v1/grants', {'serverId': sid, 'roles': [role], 'tools': TOOLS})
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
print('OK: release', rel['releaseId'], 'GOOD, tools', ','.join(TOOLS))
