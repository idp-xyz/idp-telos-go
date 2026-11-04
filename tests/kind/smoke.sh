#!/usr/bin/env bash
# The platform on a kind cluster (docs/adr/ADR-0001-platform-integration.md P3): the component
# manifests as their repositories ship them, composed by deploy/kubernetes/kind, applied to a real
# cluster; then PATH A through port-forwards: a member asks through idp-workspace, idp-host runs the
# agent with a ContextPackage from idp-cmi, the answer comes back into the member's inbox.
# Needs: a kind cluster (KIND_CLUSTER, default idp-telos), kubectl, docker, go, node (the components'
# image builds), and the component checkouts next to this repository (CI clones them at the BOM).
set -euo pipefail
cd "$(dirname "$0")/../.."
CLUSTER="${KIND_CLUSTER:-idp-telos}"
TENANT=7d1c6a52-0000-4000-8000-00000000c1d1   # the tenant deploy/kubernetes/kind/setup-jobs.yaml seeds
OUT=.local/kind
mkdir -p "$OUT"
log() { echo "== $*"; }

if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  log "images"
  bash idp-cmi/deploy/build.sh idp-cmi:dev
  bash idp-host/deploy/build.sh idp-host:dev
  bash idp-host/deploy/fixtures/build-fakemodel.sh >/dev/null
  bash idp-workspace/deploy/build.sh idp-workspace:dev
fi
for img in idp-cmi:dev idp-host:dev idp-host-fakemodel:dev idp-workspace:dev; do kind load docker-image "$img" --name "$CLUSTER" >/dev/null; done

log "render"
go run sigs.k8s.io/kustomize/kustomize/v5@v5.7.1 build deploy/kubernetes/kind 2>/dev/null > "$OUT/platform.yaml"
python3 - "$OUT/platform.yaml" "$OUT" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
ns = [d for d in docs if d["kind"] == "Namespace"]
rest = [d for d in docs if d["kind"] != "Namespace"]
yaml.safe_dump_all(ns, open(sys.argv[2] + "/namespaces.yaml", "w"))
yaml.safe_dump_all(rest, open(sys.argv[2] + "/workloads.yaml", "w"))
PY
kubectl apply -f "$OUT/namespaces.yaml" >/dev/null

log "secrets"
PG="$(openssl rand -hex 16)"; DEV="$(openssl rand -hex 32)"
kubectl -n databases create secret generic postgres-owner --from-literal=password="$PG" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
devtoken() { docker run --rm -e IDP_DEV_MODE=true -e IDP_DEV_SECRET="$DEV" --entrypoint "/usr/local/bin/$1" "$1:dev" dev-token "$TENANT" "${@:2}"; }
for c in idp-cmi idp-host idp-workspace; do kubectl apply -f "$c/deploy/kubernetes/secrets.example.yaml" >/dev/null; done
for spec in idp-cmi:idp_cmi:idp_cmi_svc idp-host:idp_host:idp_host_svc idp-workspace:idp_workspace:idp_ws_svc; do
  IFS=: read -r c db login <<<"$spec"
  APP="$(openssl rand -hex 16)"
  kubectl -n "$c" create secret generic "$c-db" \
    --from-literal=owner-url="postgres://postgres:$PG@postgresql.databases.svc.cluster.local:5432/$db?sslmode=disable" \
    --from-literal=service-url="postgres://$login:$APP@postgresql.databases.svc.cluster.local:5432/$db?sslmode=disable" \
    --from-literal=app-password="$APP" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  # the development signing secret, in the TLS Secret the kind overlay points IDP_DEV_SECRET_FILE into
  kubectl -n "$c" delete secret "$c-tls" --ignore-not-found >/dev/null   # the example is kubernetes.io/tls; a type cannot change
  kubectl -n "$c" create secret generic "$c-tls" --from-literal=dev-secret="$DEV" >/dev/null
done
kubectl -n idp-host create secret generic idp-host-credential-key --from-literal=key="$(openssl rand -hex 32)" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n idp-host create secret generic idp-host-provider-keys --from-literal=default=unused \
  --from-literal=cmi="$(devtoken idp-cmi idp-host cmi-reader)" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$OUT/signing.pem" 2>/dev/null
kubectl -n idp-workspace create secret generic idp-workspace-signing-key --from-file=signing.pem="$OUT/signing.pem" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n idp-workspace create secret generic idp-workspace-exchange --from-literal=client-secret="$(devtoken idp-host idp-workspace host-user)" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

log "PostgreSQL"
kubectl apply -f "$OUT/workloads.yaml" >/dev/null
kubectl -n databases rollout status deploy/postgresql --timeout=300s
for db in idp_cmi idp_host idp_workspace; do
  kubectl -n databases exec deploy/postgresql -- psql -U postgres -q -c "CREATE DATABASE $db" >/dev/null
done

log "migrations, logins, tenant"
for c in idp-cmi idp-host idp-workspace; do
  kubectl -n "$c" wait --for=condition=complete job --all --timeout=600s || { kubectl -n "$c" get pods; kubectl -n "$c" logs -l app.kubernetes.io/component=migrate --tail=20 --all-containers; exit 1; }
done
log "rollouts"
for c in idp-cmi idp-host idp-workspace; do
  kubectl -n "$c" rollout restart deploy >/dev/null   # pick up the logins created after the first start
  for d in $(kubectl -n "$c" get deploy -o name); do kubectl -n "$c" rollout status "$d" --timeout=300s; done
done

# on failure: can a pod in the Workspace's namespace (policies apply) and one in default (none) reach
# the HOST? kind's CNI enforces NetworkPolicies in recent versions.
diagnose() {
  echo "== diagnostics"
  kubectl get networkpolicy -A
  for ns in idp-workspace default; do
    kubectl -n "$ns" run "probe-$RANDOM" --rm -i --restart=Never --quiet --image=public.ecr.aws/docker/library/busybox:1.37 \
      --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":65534,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"p","image":"public.ecr.aws/docker/library/busybox:1.37","command":["sh","-c","nslookup idp-host.idp-host.svc.cluster.local; wget -qO- -T 3 http://idp-host.idp-host.svc.cluster.local:8450/healthz || echo WGET-FAILED"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}' \
      2>&1 | sed "s/^/[$ns] /" || true
  done
  kubectl -n idp-workspace logs deploy/idp-workspace --tail=20 || true
  kubectl -n idp-host logs deploy/idp-host --tail=20 || true
}
trap 'status=$?; [[ $status -ne 0 ]] && diagnose; exit $status' EXIT

log "PATH A through port-forwards"
kubectl -n idp-workspace port-forward svc/idp-workspace 18560:8560 >/dev/null 2>&1 & PF1=$!
kubectl -n idp-host port-forward svc/idp-host 18450:8450 >/dev/null 2>&1 & PF2=$!
kubectl -n idp-cmi port-forward svc/idp-cmi 18550:8550 >/dev/null 2>&1 & PF3=$!
trap 'status=$?; kill $PF1 $PF2 $PF3 2>/dev/null || true; [[ $status -ne 0 ]] && diagnose; exit $status' EXIT
WS=http://127.0.0.1:18560 HOST=http://127.0.0.1:18450 CMI=http://127.0.0.1:18550
for url in "$WS/readyz" "$HOST/readyz" "$CMI/readyz"; do
  for _ in $(seq 1 60); do curl -sf -o /dev/null "$url" && break; sleep 1; done
done
call() {
  local want="$1" method="$2" url="$3" token="$4" body="${5:-}" out status
  out="$(mktemp)"
  status="$(curl -s -o "$out" -w '%{http_code}' -X "$method" "$url" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' ${body:+--data "$body"})"
  [[ "$status" == "$want" ]] || { echo "FAIL: $method $url -> $status (want $want): $(cat "$out")" >&2; exit 1; }
  cat "$out"; rm -f "$out"
}
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
CADMIN="$(devtoken idp-cmi alice cmi-admin)"; HADMIN="$(devtoken idp-host hostadmin host-admin)"; HAPPROVER="$(devtoken idp-host hostapprover host-approver)"
WADMIN="$(devtoken idp-workspace alice ws-admin)"; BOB="$(devtoken idp-workspace bob ws-member)"
call 201 POST "$CMI/api/v1/sources" "$CADMIN" '{"name":"handbook","displayName":"Handbook","kind":"document-store","trust":"authoritative"}' >/dev/null
DOC="$(call 201 POST "$CMI/api/v1/knowledge/documents" "$CADMIN" '{"source":"handbook","uri":"handbook://pickup","title":"Pickup policy","sensitivity":"internal","acl":{},"text":"Pickup policy.\n\nParcels above 10000 USD need a manager to approve the pickup slot."}' | field "['documentId']")"
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"order-assistant","displayName":"Order assistant","definition":{"instructions":"Answer from the context.","model":{"provider":"local","model":"fake-1"},"tools":{},"confirmation":{"default":"auto"},"context":{"cmi":true,"purpose":"assist","maxItems":5}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/order-assistant/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null
call 201 POST "$WS/api/v1/workspaces" "$WADMIN" '{"name":"us-ops","displayName":"US operations"}' >/dev/null
call 200 PUT "$WS/api/v1/workspaces/us-ops/members/bob" "$WADMIN" '{"roles":[]}' >/dev/null
call 201 POST "$WS/api/v1/apps" "$WADMIN" '{"schemaVersion":"idp.workspace.app/v1","name":"orders","version":"1.0.0","title":"Orders","publisher":"IDP","capabilities":[],"actions":[{"name":"order.review","title":"Ask the order assistant","target":{"kind":"host.run","agent":"order-assistant"},"input":{"type":"object","required":["message"],"properties":{"message":{"type":"string"}},"additionalProperties":false}}]}' >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/orders" "$WADMIN" '{"version":"1.0.0"}' >/dev/null
RUN="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" '{"app":"orders","action":"order.review","input":{"message":"What is the pickup policy?"}}' | field "['result']['id']")"
RESULT=""
for _ in $(seq 1 120); do
  RESULT="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$RUN' and not it['title'].endswith('running'): print(it['title'])")"
  [[ -n "$RESULT" ]] && break; sleep 1
done
[[ "$RESULT" == *"— finished" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; kubectl -n idp-workspace logs deploy/idp-workspace --tail=30; echo "FAIL: no finished run in the inbox ('$RESULT')" >&2; exit 1; }
PKG="$(call 200 GET "$HOST/v1/runs/$RUN" "$HADMIN" | field "['contextPackageId']")"
call 200 GET "$CMI/v1/context/packages/$PKG" "$CADMIN" | python3 -c "
import json,sys
p=json.load(sys.stdin)['package']
assert any(i['id']=='$DOC' for i in p['items']), p['items']" || { echo "FAIL: the run's CMI package lacks the pickup policy" >&2; exit 1; }
echo "PASS: kind — idp-cmi, idp-host, idp-workspace from their own manifests; bob -> Workspace -> HOST run $RUN -> CMI package $PKG -> inbox: $RESULT"
