#!/usr/bin/env bash
# Platform acceptance, PATH A of the system architecture (docs/adr/ADR-0001-platform-integration.md T5):
# a member signed in at Keycloak asks through the Workspace; the Workspace exchanges the member's token
# and starts a run on idp-host; the HOST draws a ContextPackage from idp-cmi with its own identity;
# the result comes back into the member's inbox. Then the three systems are checked against each other.
#
#   bash deploy/compose/e2e.sh          build the component images from the side-by-side checkouts, run, tear down
#   SKIP_BUILD=1 ...                    use the images already built (idp-cmi:dev, idp-host:dev, idp-host-fakemodel:dev, idp-workspace:dev)
#   KEEP=1 ...                          leave the platform running
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
L=.local/platform
ENVF=$L/compose.env
C=(docker compose -f deploy/compose/platform.yaml --env-file "$ENVF" --profile tools)
ISSUER=http://127.0.0.1:7080/realms/idp
WS=http://127.0.0.1:8560 HOST=http://127.0.0.1:8450 CMI=http://127.0.0.1:8550
mkdir -p "$L"
umask 077
if [[ ! -f "$ENVF" ]]; then
  { for v in IDP_PG_OWNER_PASSWORD IDP_CMI_DB_PASSWORD IDP_HOST_DB_PASSWORD IDP_WORKSPACE_DB_PASSWORD KC_BOOTSTRAP_ADMIN_PASSWORD; do echo "$v=$(openssl rand -hex 16)"; done
    echo "IDP_HOST_CREDENTIAL_KEY=$(openssl rand -hex 32)"; } > "$ENVF"
fi
set -a; source "$ENVF"; set +a

for repo in idp-cmi idp-host idp-workspace; do
  [[ -d "$repo/.git" ]] || { echo "BLOCKED: $repo is not checked out next to this repository" >&2; exit 2; }
done
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  bash idp-cmi/deploy/build.sh idp-cmi:dev
  bash idp-host/deploy/build.sh idp-host:dev
  [[ -x idp-host/deploy/fixtures/build-fakemodel.sh ]] && bash idp-host/deploy/fixtures/build-fakemodel.sh >/dev/null
  bash idp-workspace/deploy/build.sh idp-workspace:dev
fi
for img in idp-cmi:dev idp-host:dev idp-host-fakemodel:dev idp-workspace:dev; do
  docker image inspect "$img" >/dev/null 2>&1 || { echo "BLOCKED: image $img missing" >&2; exit 2; }
done

"${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
cleanup() { [[ "${KEEP:-0}" == 1 ]] || "${C[@]}" down -v --remove-orphans >/dev/null 2>&1; }
trap cleanup EXIT

# ---- identity: one realm, one tenant for every component ----
TENANT="$(python3 -c 'import uuid; print(uuid.uuid4())')"
umask 022
rm -rf "$L/keycloak"; mkdir -p "$L/keycloak"
python3 deploy/keycloak/realm.py "$L/keycloak" "$TENANT" "$L/clients.json" >/dev/null
printf 'pending\n' > "$L/cmi.key"
printf 'providers:\n  - name: local\n    kind: openai\n    baseUrl: http://fakemodel:18080/v1\n    insecure: true\ncmi:\n  endpoint: http://cmi:8550\n  auth: {mode: static, apiKeyFile: /etc/idp/cmi.key}\n' > "$L/host.yaml"
python3 -c 'import json; print(json.load(open(".local/platform/clients.json"))["idp-workspace"])' > "$L/exchange.secret"
printf 'targets:\n  host:\n    endpoint: "http://host:8450"\n    auth: exchange\n    exchange: {tokenUrl: "http://keycloak:7080/realms/idp/protocol/openid-connect/token", clientId: idp-workspace, clientSecretFile: /etc/idp/exchange.secret, audience: idp-host}\n' > "$L/workspace.yaml"
chmod 644 "$L"/keycloak/idp-realm.json "$L/host.yaml" "$L/cmi.key" "$L/exchange.secret" "$L/workspace.yaml"
umask 077
secret_of() { python3 -c "import json,sys; d=json.load(open('$L/clients.json')); print(d['users'].get(sys.argv[1]) or d[sys.argv[1]])" "$1"; }
token_json() { python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
user_token() { curl -sf --noproxy '*' "$ISSUER/protocol/openid-connect/token" -d grant_type=password -d client_id=e2e-cli -d "client_secret=$(secret_of e2e-cli)" -d "username=$1" -d "password=$(secret_of "$1")" | token_json; }

"${C[@]}" up -d --wait postgres
for db in idp_cmi idp_host idp_workspace; do "${C[@]}" exec -T postgres psql -U postgres -q -c "CREATE DATABASE $db" >/dev/null; done
"${C[@]}" up -d keycloak
for _ in $(seq 1 120); do curl -sf --noproxy '*' -o /dev/null "$ISSUER/.well-known/openid-configuration" && break; sleep 1; done
curl -sf --noproxy '*' -o /dev/null "$ISSUER/.well-known/openid-configuration" || { "${C[@]}" logs --tail 30 keycloak; echo "BLOCKED: Keycloak did not come up" >&2; exit 2; }
for c in cmi host workspace; do
  "${C[@]}" run --rm -T $c-admin migrate >/dev/null
done
"${C[@]}" run --rm -T cmi-admin ensure-login idp_cmi_svc
"${C[@]}" run --rm -T host-admin ensure-login idp_host_svc
"${C[@]}" run --rm -T workspace-admin ensure-login idp_ws_svc
for c in cmi host workspace; do
  got="$("${C[@]}" run --rm -T $c-admin seed-tenant acme "$TENANT" | sed -n 's/^TENANT_ID=//p')"
  [[ "$got" == "$TENANT" ]] || { echo "BLOCKED: $c tenant $got != $TENANT" >&2; exit 2; }
done
# the HOST's own identity at the CMI (client credentials), as its static key
umask 022
curl -sf --noproxy '*' "$ISSUER/protocol/openid-connect/token" -d grant_type=client_credentials -d client_id=idp-host-svc -d "client_secret=$(secret_of idp-host-svc)" | token_json > "$L/cmi.key"
umask 077
"${C[@]}" up -d cmi host host-worker workspace
for url in "$CMI/healthz" "$HOST/healthz" "$WS/healthz"; do
  for _ in $(seq 1 60); do curl -sf --noproxy '*' -o /dev/null "$url" && break; sleep 0.5; done
  curl -sf --noproxy '*' -o /dev/null "$url" || { "${C[@]}" logs --tail 20; echo "BLOCKED: $url" >&2; exit 2; }
done

call() { # call EXPECTED METHOD URL TOKEN [BODY]
  local want="$1" method="$2" url="$3" token="$4" body="${5:-}" out status
  out="$(mktemp)"
  status="$(curl -s --noproxy '*' -o "$out" -w '%{http_code}' -X "$method" "$url" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' ${body:+--data "$body"})"
  if [[ "$status" != "$want" ]]; then echo "FAIL: $method $url -> $status (want $want): $(cat "$out")" >&2; rm -f "$out"; exit 1; fi
  cat "$out"; rm -f "$out"
}
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
ALICE="$(user_token alice)"; BOB="$(user_token bob)"; HADMIN="$(user_token hostadmin)"; HAPPROVER="$(user_token hostapprover)"

# ---- CMI: the enterprise knowledge the answer must draw on ----
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"handbook","displayName":"Handbook","kind":"document-store","trust":"authoritative"}' >/dev/null
DOC="$(call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"handbook","uri":"handbook://pickup","title":"Pickup policy","sensitivity":"internal","acl":{},"text":"Pickup policy.\n\nParcels above 10000 USD need a manager to approve the pickup slot."}' | field "['documentId']")"

# ---- HOST: an agent that answers with CMI context, released through four eyes ----
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"order-assistant","displayName":"Order assistant","definition":{"instructions":"Answer from the context you are given.","model":{"provider":"local","model":"fake-1"},"tools":{},"confirmation":{"default":"auto"},"context":{"cmi":true,"purpose":"assist","maxItems":5}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/order-assistant/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null

# ---- Workspace: a workspace, bob, an app whose action runs the agent ----
call 201 POST "$WS/api/v1/workspaces" "$ALICE" '{"name":"us-ops","displayName":"US operations"}' >/dev/null
call 200 PUT "$WS/api/v1/workspaces/us-ops/members/bob" "$ALICE" '{"roles":[]}' >/dev/null
call 201 POST "$WS/api/v1/apps" "$ALICE" '{"schemaVersion":"idp.workspace.app/v1","name":"orders","version":"1.0.0","title":"Orders","publisher":"IDP","capabilities":[],"actions":[{"name":"order.review","title":"Ask the order assistant","target":{"kind":"host.run","agent":"order-assistant"},"input":{"type":"object","required":["message"],"properties":{"message":{"type":"string"}},"additionalProperties":false}}]}' >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/orders" "$ALICE" '{"version":"1.0.0"}' >/dev/null

# ---- PATH A ----
INV="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" '{"app":"orders","action":"order.review","input":{"message":"What is the pickup policy for expensive parcels?"}}')"
RUN="$(echo "$INV" | field "['result']['id']")"
RESULT=""
for _ in $(seq 1 120); do
  RESULT="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$RUN' and not it['title'].endswith('running'): print(it['title'])")"
  [[ -n "$RESULT" ]] && break; sleep 0.5
done
[[ "$RESULT" == *"— finished" ]] || { "${C[@]}" logs --tail 30 workspace host host-worker cmi; echo "FAIL: no finished run in bob's inbox ('$RESULT')" >&2; exit 1; }

# ---- the three systems agree ----
RUNDOC="$(call 200 GET "$HOST/v1/runs/$RUN" "$HADMIN")"
[[ "$(echo "$RUNDOC" | field "['status']")" == COMPLETED ]] || { echo "FAIL: run $RUN: $RUNDOC" >&2; exit 1; }
PKG="$(echo "$RUNDOC" | field "['contextPackageId']")"
[[ -n "$PKG" ]] || { echo "FAIL: the run has no contextPackageId" >&2; exit 1; }
PACKAGE="$(call 200 GET "$CMI/v1/context/packages/$PKG" "$ALICE")"
echo "$PACKAGE" | python3 -c "
import json,sys
d=json.load(sys.stdin); p=d.get('package') or d.get('document') or d
items=p['items']
assert any(i['id']=='$DOC' and i['source']=='handbook' for i in items), ('the package does not carry the pickup policy', items)
assert p['purpose']=='assist', p['purpose']
print('package items:', [(i['kind'], i['ref']) for i in items])
" || { echo "FAIL: CMI package $PKG: $PACKAGE" >&2; exit 1; }
OWNER="$(echo "$RUNDOC" | field "['owner']")"
INVLIST="$(call 200 GET "$WS/v1/workspaces/us-ops/invocations" "$BOB" | field "['invocations'][0]['result']['id']")"
[[ "$INVLIST" == "$RUN" ]] || { echo "FAIL: the Workspace's invocation record names $INVLIST, not $RUN" >&2; exit 1; }
echo "PASS: PATH A — bob (Keycloak) -> Workspace (token exchange) -> HOST run $RUN (owner $OWNER) -> CMI package $PKG with the pickup policy -> bob's inbox: $RESULT"
