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
# PATH D (P4): bob asks an agent to run code; the HOST calls the idp-mcp gateway with a token it exchanged for bob
# PATH I: a tool call waits for bob's confirmation; he decides it in the Workspace, the code then runs (or does not)
# (RFC 8693, issued to the client idp-host), the gateway forwards the verified caller to idp-sandbox, which runs the code in an isolated
# container in a workspace that belongs to bob. The gateway's invocation record names bob and the HOST.
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
for v in IDP_MCP_DB_PASSWORD IDP_MCP_ADMIN_SECRET; do grep -q "^$v=" "$ENVF" || echo "$v=$(openssl rand -hex 16)" >> "$ENVF"; done
set -a; source "$ENVF"; set +a

for repo in idp-cmi idp-host idp-workspace idp-eer idp-mcp idp-sandbox; do
  [[ -d "$repo/.git" ]] || { echo "BLOCKED: $repo is not checked out next to this repository" >&2; exit 2; }
done
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  bash idp-cmi/deploy/build.sh idp-cmi:dev
  bash idp-host/deploy/build.sh idp-host:dev
  bash idp-host/deploy/fixtures/build-fakemodel.sh >/dev/null
  bash idp-workspace/deploy/build.sh idp-workspace:dev
  bash idp-eer/tools/development/runtime_image_offline.sh eer-runtime:dev
  bash idp-mcp/deploy/build.sh idp-mcp:dev
  bash idp-sandbox/deploy/build.sh idp-sandbox:dev
fi
# the sandbox's runtime images are pulled by the operator (here), never by the service
for img in public.ecr.aws/docker/library/python:3.12-slim public.ecr.aws/docker/library/alpine:3.22; do
  docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null
done
for img in idp-cmi:dev idp-host:dev idp-host-fakemodel:dev idp-workspace:dev eer-runtime:dev idp-mcp:dev idp-sandbox:dev; do
  docker image inspect "$img" >/dev/null 2>&1 || { echo "BLOCKED: image $img missing" >&2; exit 2; }
done

"${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
cleanup() {
  [[ -n "${FORWARDER:-}" && "${KEEP:-0}" != 1 ]] && kill "$FORWARDER" 2>/dev/null
  if [[ "${KEEP:-0}" != 1 ]]; then
    "${C[@]}" down -v --remove-orphans >/dev/null 2>&1
    docker ps -aq --filter label=idp.sandbox=platform | xargs -r docker rm -fv >/dev/null 2>&1
    docker volume ls -q --filter label=idp.sandbox=platform | xargs -r docker volume rm -f >/dev/null 2>&1
  fi
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
# the HOST's own client secret for the CMI: it mints and refreshes its own tokens (idp-host D36)
python3 -c "import json; print(json.load(open('$L/clients.json'))['idp-host-svc'])" > "$L/cmi-client.secret"
MODEL_BASE=http://fakemodel:18080/v1 AGENT_MODEL=fake-1
[[ "$MODEL" == live ]] && MODEL_BASE="http://host.docker.internal:18088$LIVE_PATH" AGENT_MODEL="$LIVE_MODEL"
# two providers: the agents' model (the double, or the live one) and the double on its own for the fact
# extractor, which needs an answer in JSON (a live model does not reliably give one)
printf 'providers:\n  - name: local\n    kind: openai\n    baseUrl: %s\n    insecure: true\n  - name: double\n    kind: openai\n    baseUrl: http://fakemodel:18080/v1\n    insecure: true\n' "$MODEL_BASE" > "$L/host.yaml"
# the CMI: the HOST reads AS THE MEMBER (RFC 8693 exchange for the CMI audience, so a member's own memories are the ones
# retrieved) and writes conversation memory with its own identity (the client idp-host-svc: cmi-writer), for which it
# mints and refreshes its own token (D36) - a token handed over here would stop working when it expired
cat >> "$L/host.yaml" <<'YAML'
cmi:
  endpoint: http://cmi:8550
  auth:
    mode: delegation
    tokenEndpoint: http://keycloak:7080/realms/idp/protocol/openid-connect/token
    clientId: idp-host
    clientSecretFile: /etc/idp/host.key
    audience: idp-cmi
  remember:
    enabled: true
    source: idp-host
    ttlDays: 30
    auth: {mode: client_credentials, tokenEndpoint: "http://keycloak:7080/realms/idp/protocol/openid-connect/token", clientId: idp-host-svc, clientSecretFile: /etc/idp/cmi-client.secret, audience: idp-cmi}
YAML
# the conversation is remembered two ways (idp-host D34): the turn, and the facts the user stated, extracted by the model
printf '    mode: both\n    facts: {provider: double, model: fake-1}\n' >> "$L/host.yaml"
# the idp-mcp gateway: the HOST acts for the member (RFC 8693 delegation, its own client credentials as actor_token)
cat >> "$L/host.yaml" <<'YAML'
gateways:
  - name: edge
    endpoint: http://mcp-gateway:3320/mcp
    auth:
      mode: delegation
      tokenEndpoint: http://keycloak:7080/realms/idp/protocol/openid-connect/token
      clientId: idp-host
      clientSecretFile: /etc/idp/host.key
      audience: http://mcp-gateway:3320/mcp
      actorToken: true
YAML
python3 -c 'import json; print(json.load(open(".local/platform/clients.json"))["idp-host"])' > "$L/host.key"
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
  cmi:
    endpoint: "http://cmi:8550"
    auth: exchange
    exchange: {tokenUrl: "http://keycloak:7080/realms/idp/protocol/openid-connect/token", clientId: idp-workspace, clientSecretFile: /etc/idp/exchange.secret, audience: idp-cmi}
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
chmod 644 "$L"/keycloak/idp-realm.json "$L/host.key" "$L/host.yaml" "$L/cmi-client.secret" "$L/exchange.secret" "$L/workspace.yaml" "$L/svc-eer.secret" "$L/eer.env"
umask 077
secret_of() { python3 -c "import json,sys; d=json.load(open('$L/clients.json')); print(d['users'].get(sys.argv[1]) or d[sys.argv[1]])" "$1"; }
token_json() { python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
KC=(curl -sf --noproxy '*' --cacert "$L/tls/ca.crt")
user_token() { "${KC[@]}" "$ISSUER/protocol/openid-connect/token" -d grant_type=password -d client_id=e2e-cli -d "client_secret=$(secret_of e2e-cli)" -d "username=$1" -d "password=$(secret_of "$1")" | token_json; }

"${C[@]}" up -d --wait postgres
for db in idp_cmi idp_host idp_workspace idp_mcp eer; do "${C[@]}" exec -T postgres psql -U postgres -q -c "CREATE DATABASE $db" >/dev/null; done
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
call() { # call EXPECTED METHOD URL TOKEN [BODY]
  local want="$1" method="$2" url="$3" token="$4" body="${5:-}" out status
  out="$(mktemp)"
  status="$(curl -s --noproxy '*' -o "$out" -w '%{http_code}' -X "$method" "$url" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' ${body:+--data "$body"})"
  if [[ "$status" != "$want" ]]; then echo "FAIL: $method $url -> $status (want $want): $(cat "$out")" >&2; rm -f "$out"; exit 1; fi
  cat "$out"; rm -f "$out"
}
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
# idp-mcp: registry, a gateway that verifies Keycloak tokens, and the sandbox as its upstream
"${C[@]}" run --rm -T mcp-admin migrate >/dev/null
"${C[@]}" run --rm -T mcp-admin ensure-login idp_mcp_svc >/dev/null
got="$("${C[@]}" run --rm -T mcp-admin seed-tenant acme "$TENANT" | sed -n 's/^TENANT_ID=//p')"
[[ "$got" == "$TENANT" ]] || { echo "BLOCKED: mcp tenant $got != $TENANT" >&2; exit 2; }
"${C[@]}" up -d mcp-registry sandbox
for _ in $(seq 1 60); do curl -sf --noproxy '*' -o /dev/null http://127.0.0.1:8250/api/v1/health && break; sleep 0.5; done
mcp_token() { "${C[@]}" run --rm -T mcp-admin dev-token "$TENANT" "$1" "$2" | tail -1; }
MADMIN="$(mcp_token carol admin)"; MOPERATOR="$(mcp_token alice operator)"; MREVIEWER="$(mcp_token bob reviewer)"
GW="$(call 201 POST http://127.0.0.1:8250/api/v1/gateways "$MADMIN" '{"name":"edge-1","authMode":"jwt","jwt":{"issuer":"'"$ISSUER"'","resource":"http://mcp-gateway:3320/mcp","jwksUrl":"http://keycloak:7080/realms/idp/protocol/openid-connect/certs","tenantClaim":"tenant_id","rolesClaim":"roles"}}')"
GW_ID="$(echo "$GW" | field "['gateway']['gatewayId']")"; GW_TOKEN="$(echo "$GW" | field "['token']")"
printf 'IDP_GATEWAY_ID=%s\nIDP_GATEWAY_TOKEN=%s\n' "$GW_ID" "$GW_TOKEN" >> "$ENVF"
set -a; source "$ENVF"; set +a
"${C[@]}" up -d mcp-gateway
for _ in $(seq 1 60); do curl -sf --noproxy '*' -o /dev/null http://127.0.0.1:3320/healthz && break; sleep 0.5; done
python3 -I deploy/compose/mcp_setup.py http://127.0.0.1:8250 "$GW_ID" "$MADMIN" "$MOPERATOR" "$MREVIEWER" host-user >/dev/null \
  || { "${C[@]}" logs --tail 30 mcp-registry mcp-gateway sandbox; echo "BLOCKED: the idp-mcp setup failed" >&2; exit 2; }

"${C[@]}" up -d cmi host host-worker workspace eer-api eer-worker eer-projector
for url in "$CMI/healthz" "$HOST/healthz" "$WS/healthz" "$EER/health/ready" http://127.0.0.1:18181/health/ready http://127.0.0.1:18182/health/ready; do
  for _ in $(seq 1 120); do curl -sf --noproxy '*' -o /dev/null "$url" && break; sleep 0.5; done
  curl -sf --noproxy '*' -o /dev/null "$url" || { "${C[@]}" logs --tail 20; echo "BLOCKED: $url" >&2; exit 2; }
done

ALICE="$(user_token alice)"; BOB="$(user_token bob)"; HADMIN="$(user_token hostadmin)"; HAPPROVER="$(user_token hostapprover)"

# ---- CMI: the enterprise knowledge the answer must draw on ----
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"idp-host","displayName":"Conversations (idp-host)","kind":"agent","trust":"informational"}' >/dev/null
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

# ---- PATH D: bob asks an agent to run code; it runs in the sandbox, through the gateway, as bob ----
if [[ "$MODEL" == live ]]; then
  echo "NOT_RUN: PATH D uses the model double's scripted sandbox flow (MODEL=fake)"
else
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"sandbox-assistant","displayName":"Sandbox assistant","definition":{"instructions":"Run the code the user gives you in the sandbox and report the result.","model":{"provider":"local","model":"'"$AGENT_MODEL"'"},"tools":{"gateway":"edge","allow":["sandbox__sandbox_create","sandbox__sandbox_run"]},"confirmation":{"default":"auto"}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/sandbox-assistant/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null
call 201 POST "$WS/api/v1/apps" "$ALICE" '{"schemaVersion":"idp.workspace.app/v1","name":"coder","version":"1.0.0","title":"Coder","publisher":"IDP","capabilities":[],"actions":[{"name":"code.run","title":"Run code","target":{"kind":"host.run","agent":"sandbox-assistant"},"input":{"type":"object","required":["message"],"properties":{"message":{"type":"string"}},"additionalProperties":false}}]}' >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/coder" "$ALICE" '{"version":"1.0.0"}' >/dev/null
INV="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" '{"app":"coder","action":"code.run","input":{"message":"run python: print(\"answer\", 6 * 7)"}}')"
DRUN="$(echo "$INV" | field "['result']['id']")"
DRESULT=""
for _ in $(seq 1 600); do
  DRESULT="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$DRUN' and not it['title'].endswith('running'): print(it['title'])")"
  [[ -n "$DRESULT" ]] && break; sleep 0.5
done
[[ "$DRESULT" == *"— finished" ]] || { "${C[@]}" logs --tail 30 host host-worker mcp-gateway sandbox; echo "FAIL: no finished sandbox run in bob's inbox ('$DRESULT')" >&2; exit 1; }
DANSWER="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$DRUN': print(it.get('summary',''))")"
[[ "$DANSWER" == *"exit code 0"* && "$DANSWER" == *"answer 42"* ]] || { echo "FAIL: the sandbox result is not in bob's inbox: $DANSWER" >&2; exit 1; }
# the gateway's record: the calls were made for bob (the subject) with a token issued to the HOST (the client), and delivered
BOB_ID="$(python3 -c "import json; print(json.load(open('.local/platform/clients.json'))['ids']['bob'])")"
for _ in $(seq 1 40); do   # the gateway pushes its call records to the registry in batches
  INVS="$(call 200 GET http://127.0.0.1:8250/api/v1/invocations "$MREVIEWER")"
  [[ "$INVS" == *sandbox__sandbox_run* ]] && break; sleep 0.5
done
echo "$INVS" | python3 -c "
import json,sys
items=json.load(sys.stdin)['items']
mine=[i for i in items if i.get('tool','').startswith('sandbox__sandbox_')]
tools={i['tool'] for i in mine}
assert {'sandbox__sandbox_create','sandbox__sandbox_run'} <= tools, ('the gateway recorded', tools)
for i in mine:
    assert i['subject']=='$BOB_ID', ('the call was not made for bob', i)
    assert i['client']=='idp-host', ('the exchanged token was not issued to the HOST', i)
    assert i['decision'].lower()=='allow' and i['outcome'].lower()=='ok', i
print('gateway records:', sorted(tools), 'subject bob, client', mine[0]['client'])
" || { echo "FAIL: the gateway's invocation records: $INVS" >&2; exit 1; }
# and the sandbox knew whose workspace it made: its audit log names the caller (a hash), not the code
"${C[@]}" logs sandbox 2>/dev/null | grep -q '"tool":"sandbox_run".*caller=' || { "${C[@]}" logs --tail 20 sandbox; echo "FAIL: the sandbox did not see the caller" >&2; exit 1; }
echo "PASS: PATH D — bob -> Workspace -> HOST run $DRUN -> idp-mcp gateway (bob's token exchanged by the HOST) -> idp-sandbox (caller forwarded) -> code ran in an isolated container -> 'answer 42' in bob's inbox"

# ---- PATH I: a tool call waits for the member, who decides it in the Workspace (idp-host D7, Workspace D22) ----
# The same two sandbox tools, but running code needs the run owner's confirmation. The shell's conversation page
# reaches the run, its pending call and the decision through the Workspace; this path drives the same endpoints.
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"careful-coder","displayName":"Careful coder","definition":{"instructions":"Run the code the user gives you in the sandbox and report the result.","model":{"provider":"local","model":"fake-1"},"tools":{"gateway":"edge","allow":["sandbox__sandbox_create","sandbox__sandbox_run"]},"confirmation":{"default":"auto","tools":{"sandbox__sandbox_run":"confirm"}},"limits":{"confirmationTimeoutSeconds":900}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/careful-coder/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null
call 201 POST "$WS/api/v1/apps" "$ALICE" '{"schemaVersion":"idp.workspace.app/v1","name":"careful","version":"1.0.0","title":"Careful coder","publisher":"IDP","capabilities":[],"actions":[{"name":"run","title":"Run code (asks before it runs)","target":{"kind":"host.run","agent":"careful-coder"},"input":{"type":"object","required":["message"],"properties":{"message":{"type":"string"}},"additionalProperties":false}}]}' >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/careful" "$ALICE" '{"version":"1.0.0"}' >/dev/null
INV="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" '{"app":"careful","action":"run","input":{"message":"run python: print(\"answer\", 6 * 7)"}}')"
IRUN="$(echo "$INV" | field "['result']['id']")"; ITHREAD="$(echo "$INV" | field "['result']['threadId']")"
# the run stops, and the Workspace shows bob the call that waits with the arguments it would run
IPENDING=""
for _ in $(seq 1 240); do
  IPENDING="$(call 200 GET "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls" "$BOB" | python3 -c "
import json,sys
for c in json.load(sys.stdin)['toolCalls']:
    if c['state'] == 'PENDING_CONFIRMATION': print(c['toolCallId'], c['tool'], c.get('arguments','')[:200])")"
  [[ -n "$IPENDING" ]] && break; sleep 0.5
done
[[ -n "$IPENDING" ]] || { "${C[@]}" logs --tail 30 host host-worker; echo "FAIL: no tool call waits for bob in run $IRUN" >&2; exit 1; }
ICALL="${IPENDING%% *}"
[[ "$(echo "$IPENDING" | awk '{print $2}')" == sandbox__sandbox_run ]] || { echo "FAIL: the waiting call is $IPENDING" >&2; exit 1; }
[[ "$IPENDING" == *print* ]] || { echo "FAIL: the waiting call does not carry the code it would run: $IPENDING" >&2; exit 1; }
[[ "$(call 200 GET "$WS/v1/workspaces/us-ops/runs/$IRUN" "$BOB" | field "['status']")" == AWAITING_INPUT ]] || { echo "FAIL: run $IRUN is not AWAITING_INPUT" >&2; exit 1; }
# the run's events, relayed: the confirmation was asked for on the stream the shell follows
call 200 GET "$WS/v1/workspaces/us-ops/runs/$IRUN/events" "$BOB" | python3 -c "
import json,sys
names={e['event'].get('name') for e in json.load(sys.stdin)['events'] if e['type']=='CUSTOM'}
assert 'idp.host/confirmation.required' in names, names" || { echo "FAIL: the relayed events do not ask for a confirmation" >&2; exit 1; }
# only bob: the run is not alice's to see or to decide, even though she is a member and an administrator
call 404 GET "$WS/v1/workspaces/us-ops/runs/$IRUN" "$ALICE" >/dev/null
call 404 POST "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls/$ICALL/decide" "$ALICE" '{"decision":"confirm"}' >/dev/null
call 422 POST "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls/$ICALL/decide" "$BOB" '{"decision":"maybe"}' >/dev/null
# bob allows it: the decision reaches idp-host, the run goes on and its result arrives in his inbox
call 200 POST "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls/$ICALL/decide" "$BOB" '{"decision":"confirm"}' >/dev/null
IRESULT=""
for _ in $(seq 1 600); do
  IRESULT="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$IRUN' and it['title'].endswith('— finished'): print(it.get('summary','').replace(chr(10),' '))")"
  [[ -n "$IRESULT" ]] && break; sleep 0.5
done
[[ "$IRESULT" == *"answer 42"* ]] || { "${C[@]}" logs --tail 30 host host-worker; echo "FAIL: the confirmed run did not report what the code printed: '$IRESULT'" >&2; exit 1; }
# and a rejected one: the call never runs
INV="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" "{\"app\":\"careful\",\"action\":\"run\",\"input\":{\"message\":\"run python: print(\\\"rejected\\\")\"},\"threadId\":\"$ITHREAD\"}")"
JRUN="$(echo "$INV" | field "['result']['id']")"
JCALL=""
for _ in $(seq 1 240); do
  JCALL="$(call 200 GET "$WS/v1/workspaces/us-ops/runs/$JRUN/tool-calls" "$BOB" | python3 -c "
import json,sys
for c in json.load(sys.stdin)['toolCalls']:
    if c['state'] == 'PENDING_CONFIRMATION' and c['tool'] == 'sandbox__sandbox_run': print(c['toolCallId'])")"
  [[ -n "$JCALL" ]] && break; sleep 0.5
done
[[ -n "$JCALL" ]] || { echo "FAIL: the second run never waited for bob" >&2; exit 1; }
[[ "$(call 200 POST "$WS/v1/workspaces/us-ops/runs/$JRUN/tool-calls/$JCALL/decide" "$BOB" '{"decision":"reject"}' | field "['state']")" == REJECTED ]] || { echo "FAIL: the rejection was not recorded" >&2; exit 1; }
echo "PASS: PATH I — bob asks careful-coder to run code -> idp-host stops the run for his confirmation -> the Workspace shows him the waiting call and the code it would run (alice, an administrator, gets 404 for both) -> bob confirms (run $IRUN) -> the code ran and its output reached his inbox; a second call rejected (run $JRUN) never ran"
fi

# ---- PATH E: memory. bob tells the assistant something; the HOST remembers it in the CMI; in a NEW conversation the CMI brings it back ----
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"memory-assistant","displayName":"Assistant with memory","definition":{"instructions":"You are a helpful assistant with a memory. The context you are given may contain things the user told you earlier: use it. Answer briefly.","model":{"provider":"local","model":"'"$AGENT_MODEL"'"},"tools":{},"confirmation":{"default":"auto"},"context":{"cmi":true,"purpose":"assist","maxItems":5}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/memory-assistant/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null
call 201 POST "$WS/api/v1/apps" "$ALICE" '{"schemaVersion":"idp.workspace.app/v1","name":"assistant","version":"1.0.0","title":"Assistant","publisher":"IDP","capabilities":[],"actions":[{"name":"chat","title":"Chat with the memory assistant","target":{"kind":"host.run","agent":"memory-assistant"},"input":{"type":"object","required":["message"],"properties":{"message":{"type":"string"}},"additionalProperties":false}}]}' >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/assistant" "$ALICE" '{"version":"1.0.0"}' >/dev/null
ask() { # ask MESSAGE [THREAD]: bob asks; prints "RUN THREAD ANSWER" once the run finished
  local body inv run thr res=""
  body="$(python3 -c "import json,sys; d={'app':'assistant','action':'chat','input':{'message':sys.argv[1]}}; (d.update(threadId=sys.argv[2]) if sys.argv[2] else None); print(json.dumps(d))" "$1" "${2:-}")"
  inv="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" "$body")"
  run="$(echo "$inv" | field "['result']['id']")"; thr="$(echo "$inv" | field "['result']['threadId']")"
  for _ in $(seq 1 600); do
    res="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$run' and not it['title'].endswith('running'): print(it.get('summary','').replace(chr(10),' '))")"
    [[ -n "$res" ]] && break; sleep 0.5
  done
  [[ -n "$res" ]] || { "${C[@]}" logs --tail 30 host host-worker cmi; echo "FAIL: no answer for '$1'" >&2; exit 1; }
  echo "$run $thr $res"
}
read -r R1 T1 A1 <<<"$(ask "My favourite colour is teal.")"
# the HOST writes the exchange into the CMI after the run completes
MEM=""
for _ in $(seq 1 60); do
  MEM="$(call 200 GET "$CMI/v1/memory/records" "$BOB" | python3 -c "
import json,sys
for r in json.load(sys.stdin)['records']:
    if r['provenance']['ref']=='run:$R1': print(json.dumps(r))")"
  [[ -n "$MEM" ]] && break; sleep 0.5
done
[[ -n "$MEM" ]] || { "${C[@]}" logs --tail 30 host host-worker cmi; echo "FAIL: the HOST did not remember run $R1 in the CMI" >&2; exit 1; }
echo "$MEM" | python3 -c "
import json,sys
r=json.load(sys.stdin)
assert r['scope']['kind']=='subject' and r['kind']=='episodic', r
assert 'teal' in r['content'] and r['content'].startswith('The user said: My favourite colour is teal.'), r['content']
assert r['provenance']['source']=='idp-host' and r['provenance']['recordedBy'], r['provenance']
assert 'conversation' in r['tags'] and r['sensitivity']=='internal' and r['validity']['validUntil'], r
" || { echo "FAIL: the remembered record is not as specified: $MEM" >&2; exit 1; }
# the CMI indexes new memory with a small delay (its outbox projection): wait until a resolve as bob finds it
READY=""
for _ in $(seq 1 90); do
  READY="$(call 200 POST "$CMI/v1/context/resolve" "$BOB" '{"query":"What is my favourite colour?","purpose":"assist"}' | python3 -c "
import json,sys
d=json.load(sys.stdin); p=d.get('package') or d.get('document') or d
print('yes' if [i for i in p['items'] if i['kind']=='memory' and 'teal' in (i.get('excerpt') or '')] else '')")"
  [[ -n "$READY" ]] && break; sleep 1
done
[[ -n "$READY" ]] || { "${C[@]}" logs --tail 20 cmi; echo "FAIL: the CMI never retrieved bob's memory for a question about it" >&2; exit 1; }
# a NEW conversation (no thread): the context package of the run carries the memory
read -r R2 T2 A2 <<<"$(ask "What is my favourite colour?")"
[[ "$T2" != "$T1" ]] || { echo "FAIL: a new conversation reused the thread" >&2; exit 1; }
PKG2="$(call 200 GET "$HOST/v1/runs/$R2" "$HADMIN" | field "['contextPackageId']")"
call 200 GET "$CMI/v1/context/packages/$PKG2" "$BOB" | python3 -c "
import json,sys
d=json.load(sys.stdin); p=d.get('package') or d.get('document') or d
mem=[i for i in p['items'] if i['kind']=='memory' and 'teal' in (i.get('excerpt') or '')]
assert mem, ('the package of the new conversation does not carry the memory', [(i['kind'], i['ref']) for i in p['items']])
print('package items:', [(i['kind'], i['ref']) for i in p['items']])
" || { echo "FAIL: the new conversation did not get the memory" >&2; exit 1; }
if [[ "$MODEL" == live ]]; then
  echo "$A2" | grep -qi teal || { echo "FAIL: the live answer does not use the memory: $A2" >&2; exit 1; }
  echo "live answer ($LIVE_MODEL): $(echo "$A2" | cut -c1-200)"
fi
# the same conversation: the thread keeps both exchanges
read -r R3 T3 A3 <<<"$(ask "And my favourite fruit is mango." "$T1")"
[[ "$T3" == "$T1" ]] || { echo "FAIL: the run did not join thread $T1 (got $T3)" >&2; exit 1; }
call 200 GET "$HOST/v1/threads/$T1/messages" "$BOB" | python3 -c "
import json,sys
d=json.load(sys.stdin); msgs=d.get('messages') or d.get('items') or d
text=json.dumps(msgs)
assert 'teal' in text and 'mango' in text, ('the thread lost the first exchange', text[:300])
" || { echo "FAIL: the thread does not hold the whole conversation" >&2; exit 1; }
# memory is private: alice, who is not bob, is not given bob's records
call 200 GET "$CMI/v1/memory/records" "$ALICE" | python3 -c "
import json,sys
assert not [r for r in json.load(sys.stdin)['records'] if r['provenance']['ref'].startswith('run:')], 'alice sees conversation memory that is not hers'
" || { echo "FAIL: alice sees bob's memory" >&2; exit 1; }
# the writer credential is the HOST's client secret, not a token handed over here: a JWT in this file is the
# regression that made memory stop being written an hour after every deployment (idp-host D36)
grep -qE '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.' "$L/cmi-client.secret" && { echo "FAIL: the CMI writer credential is a token, which expires" >&2; exit 1; }
echo "PASS: PATH E — bob: 'My favourite colour is teal.' -> HOST run $R1 -> remembered in the CMI (episodic, scope subject, source idp-host) -> a NEW conversation ($T2) gets it in its context package; the same conversation ($T1) keeps both exchanges; alice does not see it"

# ---- PATH F: my memory. bob sees what is remembered about him and erases it; nobody else can ----
MY="$(call 200 GET "$WS/v1/me/memory" "$BOB")"
echo "$MY" | python3 -c "
import json,sys
m=json.load(sys.stdin)['memories']
assert any('teal' in x['content'] for x in m), ('bob does not see his memory', m)
assert all(x['id'] and x['createdAt'] for x in m), m
" || { echo "FAIL: bob's memory list: $MY" >&2; exit 1; }
TEAL_ID="$(echo "$MY" | python3 -c "
import json,sys
print([x['id'] for x in json.load(sys.stdin)['memories'] if 'teal' in x['content']][0])")"
# the export is the member's own: bob's carries his memory, alice's carries none of it
call 200 GET "$WS/v1/me/memory/export" "$BOB" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['format']=='idp-cmi.memory-export.v1', d
assert any('teal' in r['content'] for r in d['records']), ('bob export lacks his memory', d)
" || { echo "FAIL: bob's memory export" >&2; exit 1; }
call 200 GET "$WS/v1/me/memory/export" "$ALICE" | grep -q teal && { echo "FAIL: alice's export carries bob's memory" >&2; exit 1; }
# another person cannot erase it, even knowing the id
call 404 DELETE "$WS/v1/me/memory/$TEAL_ID" "$ALICE" >/dev/null
call 200 GET "$WS/v1/me/memory" "$BOB" | grep -q teal || { echo "FAIL: alice's attempt erased bob's memory" >&2; exit 1; }
# bob erases what mentions it (the assistant's answers in later runs repeat it, so several records do): gone from his list and no longer retrieved
for ID in $(call 200 GET "$WS/v1/me/memory" "$BOB" | python3 -c "
import json,sys
print(' '.join(x['id'] for x in json.load(sys.stdin)['memories'] if 'teal' in x['content']))"); do
  call 200 DELETE "$WS/v1/me/memory/$ID" "$BOB" | python3 -c "import json,sys; assert json.load(sys.stdin)['revoked']==1"
done
call 200 GET "$WS/v1/me/memory" "$BOB" | python3 -c "
import json,sys
assert not [x for x in json.load(sys.stdin)['memories'] if x['id']=='$TEAL_ID' or 'teal' in x['content']], 'an erased memory is still listed'" || { echo "FAIL: an erased memory is still listed" >&2; exit 1; }
call 200 POST "$CMI/v1/context/resolve" "$BOB" '{"query":"What is my favourite colour?","purpose":"assist"}' | python3 -c "
import json,sys
p=json.load(sys.stdin)['package']
assert not [i for i in p['items'] if i['kind']=='memory' and 'teal' in (i.get('excerpt') or '')], 'the erased memory is still retrieved'" || { echo "FAIL: the erased memory is still retrieved" >&2; exit 1; }
# erase everything
N="$(call 200 DELETE "$WS/v1/me/memory" "$BOB" | field "['revoked']")"
[[ "$(call 200 GET "$WS/v1/me/memory" "$BOB" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['memories']))")" == 0 ]] || { echo "FAIL: bob still has memories after erasing all" >&2; exit 1; }
echo "PASS: PATH F — bob lists his memory through the Workspace (his own token exchanged for the CMI), alice cannot erase it, bob exports it (alice's export has none of it), bob erases one (gone from the list and from retrieval) and then all ($N more)"

# ---- PATH G: facts. The HOST extracts what bob STATED (never what the assistant answered); a new value closes the old one ----
facts_of_bob() { call 200 GET "$CMI/v1/memory/records?limit=200" "$BOB" | python3 -c "
import json,sys
print(json.dumps([r for r in json.load(sys.stdin)['records'] if (r.get('structured') or {}).get('fact')]))"; }
wait_fact() { # wait_fact PYTHON-CONDITION-ON-fs: poll until the condition holds on bob's current facts
  local fs=""
  for _ in $(seq 1 120); do
    fs="$(facts_of_bob)"
    echo "$fs" | python3 -c "import json,sys; fs=json.load(sys.stdin); sys.exit(0 if ($1) else 1)" && { echo "$fs"; return 0; }
    sleep 1
  done
  echo "FAIL: bob's facts never satisfied: $1 (last: $fs)" >&2; return 1
}
read -r RG1 TG1 AG1 <<<"$(ask "My favourite colour is vermilion.")"
F1="$(wait_fact "any(f['structured']['fact']['value'].lower()=='vermilion' for f in fs)")" || exit 1
echo "$F1" | python3 -c "
import json,sys
fs=[f for f in json.load(sys.stdin) if f['structured']['fact']['value'].lower()=='vermilion']
assert len(fs)==1, fs
f=fs[0]
assert 'colo' in f['structured']['fact']['key'] and f['kind']=='semantic' and f['scope']['kind']=='subject', f   # a real model may write favorite_color
assert f['provenance']['ref'].startswith('run:$RG1/fact/') and f['provenance']['source']=='idp-host', f['provenance']
assert 'vermilion' in f['content'] and 'Echo' not in f['content'] and 'fact' in f['tags'], f
" || { echo "FAIL: the first fact is not as specified: $F1" >&2; exit 1; }
VFROM="$(echo "$F1" | python3 -c "
import json,sys
print([f for f in json.load(sys.stdin) if f['structured']['fact']['value'].lower()=='vermilion'][0]['validity']['validFrom'])")"
read -r RG2 TG2 AG2 <<<"$(ask "Actually my favourite colour is now teal.")"
F2="$(wait_fact "any(f['structured']['fact']['value']=='teal' for f in fs) and not any(f['structured']['fact']['value'].lower()=='vermilion' for f in fs)")" || exit 1
echo "$F2" | python3 -c "
import json,sys
fs=[f for f in json.load(sys.stdin) if 'colo' in f['structured']['fact']['key']]
assert len(fs)==1 and fs[0]['structured']['fact']['value'].lower()=='teal', ('exactly one current value of the colour fact', fs)
" || { echo "FAIL: after the update there is not exactly one current favourite_colour: $F2" >&2; exit 1; }
# the history stays: at a moment between the two statements the old value held (as-of query)
TFROM="$(echo "$F2" | python3 -c "
import json,sys
print([f for f in json.load(sys.stdin) if f['structured']['fact']['value'].lower()=='teal'][0]['validity']['validFrom'])")"
ASOF="$(python3 -c "
import datetime,sys
p=lambda s: datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
a,b=p(sys.argv[1]),p(sys.argv[2])
assert b>a, ('the second statement is not later than the first', sys.argv[1:])
print((a+(b-a)/2).strftime('%Y-%m-%dT%H:%M:%S.%fZ'))" "$VFROM" "$TFROM")"
call 200 GET "$CMI/v1/memory/records?validAt=$ASOF&limit=200" "$BOB" | python3 -c "
import json,sys
fs=[r for r in json.load(sys.stdin)['records'] if (r.get('structured') or {}).get('fact')]
assert any(f['structured']['fact']['value'].lower()=='vermilion' for f in fs) and not any(f['structured']['fact']['value']=='teal' for f in fs), ('as of just after the first statement the value was vermilion', fs)
" || { echo "FAIL: the old value is not in the history" >&2; exit 1; }
# no fact holds the assistant's words
facts_of_bob | python3 -c "
import json,sys
assert not [f for f in json.load(sys.stdin) if 'Echo' in f['content']], 'a fact carries the assistant answer'" || { echo "FAIL: a fact carries the assistant's answer" >&2; exit 1; }
echo "PASS: PATH G — bob: 'My favourite colour is vermilion.' (run $RG1) -> the HOST extracted the fact favourite_colour=vermilion (not the assistant's answer); 'Actually ... now teal.' (run $RG2) closed it: one current value, the old one still in the as-of history"

# ---- PATH H: a member's personal memory is private to them, also against a direct request that names it (idp-cmi D42) ----
ALICE_ID="$(python3 -c "import json; print(json.load(open('$L/clients.json'))['ids']['alice'])")"
BOB_SELF="$(python3 -c "import json; print(json.load(open('$L/clients.json'))['ids']['bob'])")"
call 403 GET "$CMI/v1/memory/records?scopeKind=subject&scopeId=$ALICE_ID" "$BOB" >/dev/null || { echo "FAIL: bob (reader) could list alice's personal memory" >&2; exit 1; }
call 403 POST "$CMI/v1/context/resolve" "$BOB" '{"query":"anything about alice","purpose":"assist","scope":{"kind":"subject","id":"'"$ALICE_ID"'"}}' >/dev/null || { echo "FAIL: bob (reader) could resolve alice's personal memory" >&2; exit 1; }
call 403 POST "$CMI/v1/context/resolve" "$BOB" '{"query":"anything about alice","purpose":"assist","scopes":[{"kind":"subject","id":"'"$ALICE_ID"'"}]}' >/dev/null || { echo "FAIL: bob (reader) could resolve alice's scopes" >&2; exit 1; }
call 200 GET "$CMI/v1/memory/records?scopeKind=subject&scopeId=$BOB_SELF" "$BOB" >/dev/null || { echo "FAIL: bob cannot list his own scope by name" >&2; exit 1; }
echo "PASS: PATH H - bob (cmi-reader) is refused (403) when he names alice's personal memory in a listing or in a resolve request, and can name his own"

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
