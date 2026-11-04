#!/usr/bin/env bash
# Platform acceptance, PATH A of the system architecture (docs/adr/ADR-0001-platform-integration.md T5):
# a member signed in at Keycloak asks through the Workspace; the Workspace exchanges the member's token
# and starts a run on idp-host; the HOST draws a ContextPackage from idp-cmi with its own identity;
# the result comes back into the member's inbox. Then the three systems are checked against each other.
# PATH B (P2): an operator submits a T2 intent to idp-eer; the approval wait appears in the approver's
# Workspace inbox (EER ADR-038); the approver approves there; the Workspace exchanges the token and
# decides on EER; EER dispatches and completes, and closes the item. EER and the Workspace are checked.
# PATH C (EER ADR-037 inside the platform): idp-eer's Intelligence takes a Decision with context drawn
# from the platform's CMI and leaves memory there, authenticated by a Keycloak service account. Nothing
# in EER's API starts a Decision, so this runs EER's own opt-in test against the platform (needs uv).
#
#   bash deploy/compose/e2e.sh          build the component images from the side-by-side checkouts, run, tear down
#   SKIP_BUILD=1 ...                    use the images already built (idp-cmi:dev, idp-host:dev, idp-host-fakemodel:dev, idp-workspace:dev, eer-runtime:dev)
#   KEEP=1 ...                          leave the platform running
#   MODEL=live ...                      the HOST's agent answers with a real OpenAI-compatible model on the Docker
#                                       host (IDP_LIVE_MODEL_URL, default http://127.0.0.1:8080/v1; IDP_LIVE_MODEL,
#                                       default the first model it lists) instead of the deterministic double;
#                                       PATH A then also checks that the answer uses the CMI context
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
L=.local/platform
ENVF=$L/compose.env
C=(docker compose -f deploy/compose/platform.yaml --env-file "$ENVF" --profile tools)
ISSUER=https://127.0.0.1:7443/realms/idp
WS=http://127.0.0.1:8560 HOST=http://127.0.0.1:8450 CMI=http://127.0.0.1:8550 EER=http://127.0.0.1:18180
mkdir -p "$L"
MODEL="${MODEL:-fake}"
if [[ "$MODEL" == live ]]; then
  LIVE_URL="${IDP_LIVE_MODEL_URL:-http://127.0.0.1:8080/v1}"
  LIVE_MODEL="${IDP_LIVE_MODEL:-$(curl -sf --noproxy '*' -m 10 "$LIVE_URL/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || true)}"
  [[ -n "$LIVE_MODEL" ]] || { echo "BLOCKED: no model at $LIVE_URL" >&2; exit 2; }
  BRIDGE="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
  [[ -n "$BRIDGE" ]] || { echo "BLOCKED: no docker0 address for the model forwarder" >&2; exit 2; }
  TARGET="$(python3 -c "import urllib.parse,sys; u=urllib.parse.urlparse(sys.argv[1]); print(f'{u.hostname}:{u.port or 80}')" "$LIVE_URL")"
  python3 tools/model_forward.py "$BRIDGE:18088" "$TARGET" >/dev/null 2>&1 & FORWARDER=$!
  LIVE_PATH="$(python3 -c "import urllib.parse,sys; print(urllib.parse.urlparse(sys.argv[1]).path.rstrip('/'))" "$LIVE_URL")"
fi
umask 077
if [[ ! -f "$ENVF" ]] || ! grep -q EER_RUNTIME_APP_PASSWORD "$ENVF"; then
  { for v in IDP_PG_OWNER_PASSWORD IDP_CMI_DB_PASSWORD IDP_HOST_DB_PASSWORD IDP_WORKSPACE_DB_PASSWORD KC_BOOTSTRAP_ADMIN_PASSWORD \
             EER_RUNTIME_OWNER_PASSWORD EER_RUNTIME_APP_PASSWORD EER_RUNTIME_READONLY_PASSWORD EER_INTELLIGENCE_OWNER_PASSWORD EER_INTELLIGENCE_APP_PASSWORD; do echo "$v=$(openssl rand -hex 16)"; done
    echo "IDP_HOST_CREDENTIAL_KEY=$(openssl rand -hex 32)"; } > "$ENVF"
fi
set -a; source "$ENVF"; set +a

for repo in idp-cmi idp-host idp-workspace idp-eer; do
  [[ -d "$repo/.git" ]] || { echo "BLOCKED: $repo is not checked out next to this repository" >&2; exit 2; }
done
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  bash idp-cmi/deploy/build.sh idp-cmi:dev
  bash idp-host/deploy/build.sh idp-host:dev
  bash idp-host/deploy/fixtures/build-fakemodel.sh >/dev/null
  bash idp-workspace/deploy/build.sh idp-workspace:dev
  bash idp-eer/tools/development/runtime_image_offline.sh eer-runtime:dev
fi
for img in idp-cmi:dev idp-host:dev idp-host-fakemodel:dev idp-workspace:dev eer-runtime:dev; do
  docker image inspect "$img" >/dev/null 2>&1 || { echo "BLOCKED: image $img missing" >&2; exit 2; }
done

"${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
cleanup() {
  [[ -n "${FORWARDER:-}" && "${KEEP:-0}" != 1 ]] && kill "$FORWARDER" 2>/dev/null
  [[ "${KEEP:-0}" == 1 ]] || "${C[@]}" down -v --remove-orphans >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

# ---- identity: one realm, one tenant for every component ----
TENANT="$(python3 -c 'import uuid; print(uuid.uuid4())')"
umask 022
# an https issuer (idp-eer accepts no other): a CA and a server certificate for 127.0.0.1 and keycloak
rm -rf "$L/tls"; mkdir -p "$L/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=IDP TELOS platform test CA" -keyout "$L/tls/ca.key" -out "$L/tls/ca.crt" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=keycloak" -keyout "$L/tls/server.key" -out "$L/tls/server.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1,DNS:keycloak,DNS:localhost\n' > "$L/tls/san.ext"
openssl x509 -req -in "$L/tls/server.csr" -CA "$L/tls/ca.crt" -CAkey "$L/tls/ca.key" -CAcreateserial -days 2 -extfile "$L/tls/san.ext" -out "$L/tls/server.crt" 2>/dev/null
chmod 644 "$L/tls/server.key" "$L/tls/server.crt" "$L/tls/ca.crt"   # read by the Keycloak container user; a throw-away dev CA
rm -rf "$L/keycloak"; mkdir -p "$L/keycloak"
python3 deploy/keycloak/realm.py "$L/keycloak" "$TENANT" "$L/clients.json" >/dev/null
printf 'pending\n' > "$L/cmi.key"
MODEL_BASE=http://fakemodel:18080/v1 AGENT_MODEL=fake-1
[[ "$MODEL" == live ]] && MODEL_BASE="http://host.docker.internal:18088$LIVE_PATH" AGENT_MODEL="$LIVE_MODEL"
printf 'providers:\n  - name: local\n    kind: openai\n    baseUrl: %s\n    insecure: true\ncmi:\n  endpoint: http://cmi:8550\n  auth: {mode: static, apiKeyFile: /etc/idp/cmi.key}\n' "$MODEL_BASE" > "$L/host.yaml"
python3 -c 'import json; print(json.load(open(".local/platform/clients.json"))["idp-workspace"])' > "$L/exchange.secret"
cat > "$L/workspace.yaml" <<'YAML'
targets:
  host:
    endpoint: "http://host:8450"
    auth: exchange
    exchange: {tokenUrl: "http://keycloak:7080/realms/idp/protocol/openid-connect/token", clientId: idp-workspace, clientSecretFile: /etc/idp/exchange.secret, audience: idp-host}
  eer:
    endpoint: "http://eer-api:8080"
    auth: exchange
    exchange: {tokenUrl: "http://keycloak:7080/realms/idp/protocol/openid-connect/token", clientId: idp-workspace, clientSecretFile: /etc/idp/exchange.secret, audience: eer-runtime}
    tenantId: pilot
YAML
python3 -c 'import json; print(json.load(open(".local/platform/clients.json"))["svc-eer"])' > "$L/svc-eer.secret"
# idp-eer: Keycloak as its issuer; operator submits, bob approves as actor bob; its own workloads
python3 - "$L/clients.json" "$ISSUER" > "$L/eer.env" <<'PY'
import json, sys
ids = json.load(open(sys.argv[1]))["ids"]; iss = sys.argv[2]
wl = "urn:eer:workload"
mappings = [{"issuer": iss, "subject": ids["operator"], "tenant_id": "pilot", "actor_id": "operator"},
            {"issuer": iss, "subject": ids["bob"], "tenant_id": "pilot", "actor_id": "bob"}] + \
           [{"issuer": wl, "subject": s, "tenant_id": "pilot"} for s in ("eer-worker", "eer-projector", "eer-configure")]
policy = {"policy_id": "platform-acceptance", "policy_version": 1,
          "bindings": [{"issuer": iss, "subject": ids["operator"], "roles": ["runtime-operator"]},
                       {"issuer": iss, "subject": ids["bob"], "roles": ["runtime-operator"]},
                       {"issuer": wl, "subject": "eer-worker", "roles": ["runtime-workload"]},
                       {"issuer": wl, "subject": "eer-projector", "roles": ["runtime-workload"]},
                       {"issuer": wl, "subject": "eer-configure", "roles": ["runtime-configurator"]}],
          "grants": [
              {"grant_id": "operator-general", "role": "runtime-operator", "principal_kind": "human", "actor_scope": "any",
               "actions": ["event.publish", "execution.submit", "execution.read", "decision.read", "actor.read", "plan.read", "world.read", "human_task.read"]},
              {"grant_id": "operator-mapped-actor", "role": "runtime-operator", "principal_kind": "human", "actor_scope": "mapped",
               "actions": ["execution.approve", "execution.cancel"]},
              {"grant_id": "runtime-workload", "role": "runtime-workload", "principal_kind": "workload", "actor_scope": "any",
               "actions": ["event.publish", "event.replay", "execution.submit", "execution.dispatch", "world.snapshot.create", "human_task.create", "human_task.expire"]},
              {"grant_id": "runtime-configuration", "role": "runtime-configurator", "principal_kind": "workload", "actor_scope": "any", "actions": ["configuration.apply"]}]}
# EER fetches keys over https only (http is for .invalid test issuers); it trusts the run's CA (SSL_CERT_FILE)
issuers = [{"issuer": iss, "audience": "eer-runtime", "jwks_url": "https://keycloak:7443/realms/idp/protocol/openid-connect/certs"}]
for k, v in (("EER_PRINCIPAL_MAPPINGS", mappings), ("EER_AUTHORIZATION_POLICY", policy), ("EER_OIDC_ISSUERS", issuers)):
    print(k + "=" + json.dumps(v, separators=(",", ":")))
print("SSL_CERT_FILE=/etc/eer/ca.crt")
PY
chmod 644 "$L"/keycloak/idp-realm.json "$L/host.yaml" "$L/cmi.key" "$L/exchange.secret" "$L/workspace.yaml" "$L/svc-eer.secret" "$L/eer.env"
umask 077
secret_of() { python3 -c "import json,sys; d=json.load(open('$L/clients.json')); print(d['users'].get(sys.argv[1]) or d[sys.argv[1]])" "$1"; }
token_json() { python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
KC=(curl -sf --noproxy '*' --cacert "$L/tls/ca.crt")
user_token() { "${KC[@]}" "$ISSUER/protocol/openid-connect/token" -d grant_type=password -d client_id=e2e-cli -d "client_secret=$(secret_of e2e-cli)" -d "username=$1" -d "password=$(secret_of "$1")" | token_json; }

"${C[@]}" up -d --wait postgres
for db in idp_cmi idp_host idp_workspace eer; do "${C[@]}" exec -T postgres psql -U postgres -q -c "CREATE DATABASE $db" >/dev/null; done
"${C[@]}" up -d keycloak eer-temporal
for _ in $(seq 1 120); do "${KC[@]}" -o /dev/null "$ISSUER/.well-known/openid-configuration" && break; sleep 1; done
"${KC[@]}" -o /dev/null "$ISSUER/.well-known/openid-configuration" || { "${C[@]}" logs --tail 30 keycloak; echo "BLOCKED: Keycloak did not come up" >&2; exit 2; }
for c in cmi host workspace; do
  "${C[@]}" run --rm -T $c-admin migrate >/dev/null
done
"${C[@]}" run --rm -T cmi-admin ensure-login idp_cmi_svc
"${C[@]}" run --rm -T host-admin ensure-login idp_host_svc
"${C[@]}" run --rm -T workspace-admin ensure-login idp_ws_svc
# idp-eer: its roles (deploy/postgres/bootstrap-runtime-roles.sql), schema and pilot Actors and Plans
"${C[@]}" exec -T postgres psql -U postgres --no-psqlrc -q --set ON_ERROR_STOP=1 \
  --set "runtime_owner_password=$EER_RUNTIME_OWNER_PASSWORD" --set "runtime_app_password=$EER_RUNTIME_APP_PASSWORD" \
  --set "runtime_readonly_password=$EER_RUNTIME_READONLY_PASSWORD" --set "intelligence_owner_password=$EER_INTELLIGENCE_OWNER_PASSWORD" \
  --set "intelligence_app_password=$EER_INTELLIGENCE_APP_PASSWORD" --dbname eer < idp-eer/deploy/postgres/bootstrap-runtime-roles.sql >/dev/null
"${C[@]}" run --rm -T eer-migrate >/dev/null
"${C[@]}" run --rm -T eer-configure >/dev/null
for c in cmi host workspace; do
  got="$("${C[@]}" run --rm -T $c-admin seed-tenant acme "$TENANT" | sed -n 's/^TENANT_ID=//p')"
  [[ "$got" == "$TENANT" ]] || { echo "BLOCKED: $c tenant $got != $TENANT" >&2; exit 2; }
done
# the HOST's own identity at the CMI (client credentials), as its static key
umask 022
"${KC[@]}" "$ISSUER/protocol/openid-connect/token" -d grant_type=client_credentials -d client_id=idp-host-svc -d "client_secret=$(secret_of idp-host-svc)" | token_json > "$L/cmi.key"
umask 077
"${C[@]}" up -d cmi host host-worker workspace eer-api eer-worker eer-projector
for url in "$CMI/healthz" "$HOST/healthz" "$WS/healthz" "$EER/health/ready" http://127.0.0.1:18181/health/ready http://127.0.0.1:18182/health/ready; do
  for _ in $(seq 1 120); do curl -sf --noproxy '*' -o /dev/null "$url" && break; sleep 0.5; done
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
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"order-assistant","displayName":"Order assistant","definition":{"instructions":"Answer from the context you are given. Quote the rule that applies.","model":{"provider":"local","model":"'"$AGENT_MODEL"'"},"tools":{},"confirmation":{"default":"auto"},"context":{"cmi":true,"purpose":"assist","maxItems":5}}}' >/dev/null
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
for _ in $(seq 1 600); do
  RESULT="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$RUN' and not it['title'].endswith('running'): print(it['title'])")"
  [[ -n "$RESULT" ]] && break; sleep 0.5
done
[[ "$RESULT" == *"— finished" ]] || { "${C[@]}" logs --tail 30 workspace host host-worker cmi; echo "FAIL: no finished run in bob's inbox ('$RESULT')" >&2; exit 1; }
ANSWER="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$RUN': print(it.get('summary',''))")"
if [[ "$MODEL" == live ]]; then
  # the real model saw the CMI package: the answer carries the rule only the handbook document states
  echo "$ANSWER" | grep -qi "manager" || { echo "FAIL: the live answer does not use the CMI context: $ANSWER" >&2; exit 1; }
  echo "live answer ($LIVE_MODEL): $(echo "$ANSWER" | tr '\n' ' ' | cut -c1-300)"
fi

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

# ---- PATH B: a T2 execution waits for a human; the approver decides in the Workspace ----
OPERATOR="$(user_token operator)"; BOB="$(user_token bob)"
call 200 PUT "$WS/api/v1/workspaces/us-ops/members/bob" "$ALICE" '{"roles":["approver"]}' >/dev/null
call 201 POST "$WS/api/v1/apps" "$ALICE" "$(cat idp-workspace/deploy/apps/eer-approvals.json)" >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/eer-approvals" "$ALICE" '{"version":"1.0.0"}' >/dev/null
SUFFIX="$(date +%s%N)"
EVENT="event:platform-$SUFFIX" EXEC="execution:platform-$SUFFIX"
NOW="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
call 201 POST "$EER/v1/events" "$OPERATOR" "{\"tenant_id\":\"pilot\",\"event_id\":\"$EVENT\",\"event_type\":\"parcel.address.rejected\",\"occurred_at\":\"$NOW\",\"source\":\"actor:carrier-connector\",\"subject\":\"parcel:PLATFORM-$SUFFIX\",\"schema_version\":1,\"correlation_id\":\"loop:platform-$SUFFIX\",\"payload\":{\"reason\":\"postal_code_missing\"}}" >/dev/null
call 202 POST "$EER/v1/intents" "$OPERATOR" "{\"intent\":{\"tenant_id\":\"pilot\",\"intent_id\":\"intent:platform-$SUFFIX\",\"schema_version\":1,\"objective\":\"correct rejected parcel address\",\"originating_events\":[\"$EVENT\"],\"correlation_id\":\"loop:platform-$SUFFIX\",\"constraints\":{\"disposition_tier\":\"T2\"},\"policy_id\":\"address-exception-policy\",\"policy_version\":1,\"created_at\":\"$NOW\"},\"execution_id\":\"$EXEC\",\"plan_id\":\"address-error-actions\",\"plan_version\":1,\"compilation_context\":{\"context\":{\"parcel\":{\"parcel_id\":\"PKG-$SUFFIX\"}},\"request\":{\"oms_action_cost\":0,\"reissue_cost\":120,\"fail_mode\":\"none\"}}}" >/dev/null
state_of() { call 200 GET "$EER/v1/executions/$EXEC" "$OPERATOR" | field "['state']"; }
for _ in $(seq 1 120); do [[ "$(state_of)" == Waiting ]] && break; sleep 0.5; done
[[ "$(state_of)" == Waiting ]] || { echo "FAIL: $EXEC is $(state_of), not Waiting for approval" >&2; exit 1; }
ITEM=""
for _ in $(seq 1 120); do
  ITEM="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=approval" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='$EXEC/approval': print(it['itemId'])")"
  [[ -n "$ITEM" ]] && break; sleep 0.5
done
[[ -n "$ITEM" ]] || { "${C[@]}" logs --tail 30 eer-projector; echo "FAIL: the approval wait of $EXEC never reached bob's inbox" >&2; exit 1; }
DECIDED="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" "{\"app\":\"eer-approvals\",\"action\":\"approval.approve\",\"itemId\":\"$ITEM\",\"input\":{}}")"
[[ "$(echo "$DECIDED" | field "['result']['accepted']")" == True ]] || { echo "FAIL: EER did not accept the decision: $DECIDED" >&2; exit 1; }
for _ in $(seq 1 120); do [[ "$(state_of)" == Completed ]] && break; sleep 0.5; done
[[ "$(state_of)" == Completed ]] || { "${C[@]}" logs --tail 30 eer-worker; echo "FAIL: $EXEC is $(state_of) after the approval" >&2; exit 1; }
call 200 GET "$EER/v1/executions/$EXEC" "$OPERATOR" | python3 -c "
import json,sys
d=json.load(sys.stdin)
audit=[(a.get('action'), a.get('actor_id') or a.get('actor')) for a in d.get('audit',[])]
assert ('approval.approved','bob') in audit, audit
print('EER audit:', [a for a in audit if a[0].startswith('approval')])
" || { echo "FAIL: EER did not record bob's approval" >&2; exit 1; }
CLOSED=""
for _ in $(seq 1 120); do
  CLOSED="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox/$ITEM" "$BOB" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status']+'/'+((d.get('resolution') or {}).get('outcome') or ''))")"
  [[ "$CLOSED" == resolved/approved ]] && break; sleep 0.5
done
[[ "$CLOSED" == resolved/approved ]] || { echo "FAIL: the inbox item is $CLOSED" >&2; exit 1; }
echo "PASS: PATH B — operator -> EER $EXEC (T2) -> Waiting -> bob's inbox (EER projector, svc-eer) -> bob approves in the Workspace (token exchange) -> EER Completed, audit approval.approved by bob -> item $CLOSED by EER"

# ---- PATH C: EER Intelligence draws Decision context from the platform's CMI and leaves memory ----
if ! command -v uv >/dev/null; then
  echo "NOT_RUN: PATH C needs uv (EER's Intelligence runs from its locked environment)"
  exit 0
fi
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"eer","displayName":"EER Intelligence","kind":"agent","trust":"informational"}' >/dev/null
# the knowledge EER's test expects for its Decision fixture (as idp-eer tools/development/cmi_stack.sh seeds it)
call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"handbook","uri":"handbook://addresses","title":"Address normalization","sensitivity":"internal","acl":{},"entities":[{"type":"city","id":"test-city"}],"text":"Address normalization.\n\nPostal codes of the test city start with 0000; the district office is on Example Road."}' >/dev/null
INTEL_TOKEN="$("${KC[@]}" "$ISSUER/protocol/openid-connect/token" -d grant_type=client_credentials -d client_id=eer-intelligence -d "client_secret=$(secret_of eer-intelligence)" | token_json)"
ALICE="$(user_token alice)"
INTEL_SUB="$(python3 -c 'import base64,json,sys; p=sys.argv[1].split(".")[1]; print(json.loads(base64.urlsafe_b64decode(p+"="*(-len(p)%4)))["sub"])' "$INTEL_TOKEN")"
# a clean environment: the Intelligence plane refuses to start beside Runtime credentials (EER ADR-032)
(cd idp-eer && env -i HOME="$HOME" PATH="$PATH" UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}" EER_CMI_INTEGRATION=1 EER_TENANT_ID=pilot EER_CMI_BASE_URL="$CMI" EER_CMI_BEARER_TOKEN="$INTEL_TOKEN" EER_CMI_ADMIN_TOKEN="$ALICE" EER_CMI_EXPECTED_REQUESTER="$INTEL_SUB" \
  EER_INTELLIGENCE_ALLOWED_EGRESS_ORIGINS="[\"$CMI\"]" \
  PYTHONPATH=intelligence/src uv run --project intelligence --frozen --no-python-downloads \
  python -m unittest intelligence.tests.integration.test_cmi_integration -v) > "$L/path-c.log" 2>&1 \
  || { tail -30 "$L/path-c.log"; echo "FAIL: PATH C (EER Intelligence with the platform's CMI)" >&2; exit 1; }
RAN="$(sed -nE 's/^Ran ([0-9]+) tests? in .*/\1/p' "$L/path-c.log")"
grep -qE "^OK$" "$L/path-c.log" && [[ "${RAN:-0}" -ge 1 ]] || { tail -20 "$L/path-c.log"; echo "FAIL: PATH C did not run its tests (ran ${RAN:-0}, or some were skipped)" >&2; exit 1; }
echo "PASS: PATH C — EER Intelligence (Keycloak service account eer-intelligence) -> CMI context for a Decision -> memory left in the CMI ($RAN tests)"
