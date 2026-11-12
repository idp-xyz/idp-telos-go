#!/usr/bin/env bash
# The WHOLE platform on a kind cluster (docs/adr/ADR-0001-platform-integration.md): idp-cmi, idp-host, idp-workspace,
# idp-eer, idp-mcp and idp-sandbox from their own Kubernetes manifests, composed by deploy/kubernetes/full with an
# in-cluster PostgreSQL, Keycloak (https issuer, a per-run CA), Temporal and the HOST's model double; every namespace
# default-deny (the cluster must enforce NetworkPolicies, e.g. Calico). Then the four paths of deploy/compose/e2e.sh,
# with the same assertions, through kubectl port-forwards on high local ports:
#   PATH A  bob via the Workspace -> HOST -> CMI package -> inbox
#   PATH B  operator T2 intent -> EER -> bob's inbox -> approval in the Workspace -> EER Completed
#   PATH D  bob asks for code: HOST -> idp-mcp gateway -> idp-sandbox (kubernetes backend) -> 'answer 42' in the inbox
#   PATH E  memory: bob's words are remembered by the HOST in the CMI, found again in a new conversation, kept in the same one
#   PATH F  my memory: bob lists and erases what was remembered about him, through the Workspace; alice cannot
#   PATH I  a tool call waits for bob's confirmation; he decides it in the Workspace, the code then runs (or does not)
#   PATH C  idp-eer's Intelligence test run from this host (uv) against the CMI port-forward
# and installs the assistant app (deploy/apps/assistant): a chat page in the shell for both agents, on its own origin.
# Local ports: 17443 Keycloak (https; it is also the issuer https://127.0.0.1:17443/realms/idp), 17560 Workspace,
# 17450 HOST, 17550 CMI, 17180 EER api, 17250 idp-mcp registry, 17320 idp-mcp gateway, 17561 the assistant's page.
#
#   bash tests/kind/full.sh             build the images (as deploy/compose/e2e.sh), load them, deploy, run the paths
#   PG_HA=1 ...                         PostgreSQL as a CloudNativePG cluster (a primary and two synchronous standbys, deploy/kubernetes/ha-postgres)
#                                       instead of the single Deployment; tests/kind/failover-drill.sh then kills the primary under load
#   SKIP_BUILD=1 ...                    use the images already built (idp-cmi:dev idp-host:dev idp-host-fakemodel:dev idp-workspace:dev
#                                       eer-runtime:dev idp-mcp:dev idp-sandbox:dev)
#   KIND_CLUSTER=kind ...               the cluster (default kind; kubectl context kind-$KIND_CLUSTER)
#   TEARDOWN=1 ...                      delete the platform's namespaces at the end (default: leave it running)
#   MODEL=live ...                      the HOST's agents answer with a real OpenAI-compatible model on this host
#                                       (IDP_LIVE_MODEL_URL, default http://127.0.0.1:8080/v1; IDP_LIVE_MODEL, default the
#                                       first model it lists) instead of the deterministic double; the pods reach it through
#                                       a forwarder on the kind network's gateway (container idp-kind-model-forward, which
#                                       outlives the run so that the platform keeps answering)
# A run starts by deleting the platform's namespaces (a fresh platform); nothing else in the cluster is touched.
# Nodes that cannot pull images get every image from the Docker daemon (or, for the infrastructure images, crane).
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
CLUSTER="${KIND_CLUSTER:-kind}"
kubectl() { command kubectl --context "kind-$CLUSTER" "$@"; }
L=.local/kind-full
ISSUER=https://127.0.0.1:17443/realms/idp
WS=http://127.0.0.1:17560 HOST=http://127.0.0.1:17450 CMI=http://127.0.0.1:17550 EER=http://127.0.0.1:17180 REG=http://127.0.0.1:17250
KCS=keycloak.keycloak.svc.cluster.local
GATEWAY_URL=http://idp-mcp-gateway-edge-1.idp-mcp.svc.cluster.local:3220/mcp
SANDBOX_URL=http://idp-sandbox.idp-sandbox.svc.cluster.local:8570/mcp
NAMESPACES=(idp-cmi idp-host idp-workspace idp-eer idp-mcp idp-sandbox idp-sandbox-runs databases keycloak temporal)
log() { echo "== $*"; }
mkdir -p "$L"
PFPIDS=()
# a port-forward that survives its pod being replaced (a rolling restart): re-established until the script ends
# (`|| true`: under set -e one failed kubectl would end the loop for good)
pf() { # pf NAMESPACE SERVICE LOCAL:REMOTE
  ( while true; do kubectl -n "$1" port-forward "svc/$2" "$3" >/dev/null 2>&1 || true; sleep 1; done ) & PFPIDS+=($!)
}
for repo in idp-cmi idp-host idp-workspace idp-eer idp-mcp idp-sandbox; do
  [[ -d "$repo/.git" ]] || { echo "BLOCKED: $repo is not checked out next to this repository" >&2; exit 2; }
done
for tool in kubectl kind docker openssl python3 curl; do command -v "$tool" >/dev/null || { echo "BLOCKED: $tool not found" >&2; exit 2; }; done
kubectl get nodes >/dev/null || { echo "BLOCKED: no cluster (kubectl context kind-$CLUSTER)" >&2; exit 2; }
MODEL="${MODEL:-fake}"
if [[ "$MODEL" == live ]]; then
  LIVE_URL="${IDP_LIVE_MODEL_URL:-http://127.0.0.1:8080/v1}"
  LIVE_MODEL="${IDP_LIVE_MODEL:-$(curl -sf --noproxy '*' -m 10 "$LIVE_URL/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || true)}"
  [[ -n "$LIVE_MODEL" ]] || { echo "BLOCKED: no model at $LIVE_URL" >&2; exit 2; }
fi

diagnose() {
  echo "== diagnostics" >&2
  kubectl get pods -A 2>&1 | grep -E 'idp-|databases|keycloak|temporal' | grep -vE 'Running +[0-9]+ .*|Completed' >&2 || true
  for ns in idp-cmi idp-host idp-workspace idp-eer idp-mcp idp-sandbox keycloak temporal; do
    for p in $(kubectl -n "$ns" get pods --no-headers 2>/dev/null | awk '$3!="Running" && $3!="Completed" {print $1}'); do
      echo "-- $ns/$p" >&2; kubectl -n "$ns" describe pod "$p" 2>&1 | tail -8 >&2; kubectl -n "$ns" logs "$p" --all-containers --tail=15 2>&1 >&2 || true
    done
  done
}
cleanup() {
  local status=$?
  for p in "${PFPIDS[@]:-}"; do [[ -n "$p" ]] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; } || true; done
  [[ $status -ne 0 ]] && diagnose
  if [[ "${TEARDOWN:-0}" == 1 ]]; then kubectl delete ns "${NAMESPACES[@]}" --wait=false >/dev/null 2>&1 || true; fi
  return $status
}
trap cleanup EXIT

# ---------------------------------------------------------------- images
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  log "images"
  bash idp-cmi/deploy/build.sh idp-cmi:dev
  bash idp-host/deploy/build.sh idp-host:dev
  bash idp-host/deploy/fixtures/build-fakemodel.sh >/dev/null
  bash idp-workspace/deploy/build.sh idp-workspace:dev
  bash idp-eer/tools/development/runtime_image_offline.sh eer-runtime:dev
  bash idp-mcp/deploy/build.sh idp-mcp:dev
  bash idp-sandbox/deploy/build.sh idp-sandbox:dev
  bash deploy/images/pg-backup/build.sh idp-pg-backup:dev
fi
# load an image into the cluster; a Docker with the containerd image store saves all platforms and kind's import fails
# then, so save the one platform the nodes run
load_image() {
  local tar; tar="$(mktemp --suffix=.tar)"
  docker save --platform linux/amd64 "$1" -o "$tar" 2>/dev/null || docker save "$1" -o "$tar"
  kind load image-archive "$tar" --name "$CLUSTER" >/dev/null; rm -f "$tar"
}
PG_HA="${PG_HA:-0}"
NODE="$CLUSTER-control-plane"
in_node() { docker exec "$NODE" crictl inspecti "$1" >/dev/null 2>&1; }
# an infrastructure image: from the daemon, else crane (it honours a shell https_proxy; the daemon and the nodes may have none)
ensure_image() { # ensure_image IMAGE [NODE-NAME]
  local img="$1" want="${2:-$1}"
  in_node "$want" && return 0
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    if command -v crane >/dev/null; then
      local tar; tar="$(mktemp --suffix=.tar)"; crane pull --platform linux/amd64 "$img" "$tar" >/dev/null && kind load image-archive "$tar" --name "$CLUSTER" >/dev/null; rm -f "$tar"
      return 0
    fi
    docker pull -q "$img" >/dev/null
  fi
  [[ "$img" == "$want" ]] || docker tag "$img" "$want"
  load_image "$want"
}
log "loading images into $CLUSTER"
for img in idp-cmi:dev idp-host:dev idp-host-fakemodel:dev idp-workspace:dev eer-runtime:dev idp-mcp:dev idp-sandbox:dev idp-pg-backup:dev; do
  docker image inspect "$img" >/dev/null 2>&1 || { echo "BLOCKED: image $img missing (run without SKIP_BUILD)" >&2; exit 2; }
  load_image "$img"
done
ensure_image docker.io/pgvector/pgvector:0.8.7-pg17
if [[ "$PG_HA" == 1 ]]; then
  ensure_image ghcr.io/cloudnative-pg/postgresql:17.11-standard-trixie   # the operators' images: tests/kind/ha-operators.sh
  # built like the components (cached, so quick when the Dockerfile has not changed) and always loaded: the node may hold an older
  # build under the same tag
  if [[ "${SKIP_BUILD:-0}" != 1 ]] || ! docker image inspect idp-postgres-ha:17-dev >/dev/null 2>&1; then bash deploy/images/postgres-ha/build.sh idp-postgres-ha:17-dev >/dev/null; fi
  load_image idp-postgres-ha:17-dev
fi
ensure_image public.ecr.aws/docker/library/postgres:17-alpine   # the restore drill's client tools
ensure_image quay.io/keycloak/keycloak:26.8.2
ensure_image local/temporal:1.10.0
# what the sandbox runs (IDP_SANDBOX_PYTHON_IMAGE / _SHELL_IMAGE in its manifest): short names, loaded by the operator
ensure_image public.ecr.aws/docker/library/python:3.12-slim python:3.12-slim
ensure_image public.ecr.aws/docker/library/alpine:3.22 alpine:3.22
if [[ "$MODEL" == live ]]; then
  # the pods reach the host's model through the kind network's gateway: a forwarder bound to that address only, in a
  # container on the host's network so that it outlives this script and comes back with Docker
  KIND_GW="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1)"
  [[ -n "$KIND_GW" ]] || { echo "BLOCKED: no IPv4 gateway on the kind network" >&2; exit 2; }
  TARGET="$(python3 -c "import urllib.parse,sys; u=urllib.parse.urlparse(sys.argv[1]); print(f'{u.hostname}:{u.port or 80}')" "$LIVE_URL")"
  LIVE_PATH="$(python3 -c "import urllib.parse,sys; print(urllib.parse.urlparse(sys.argv[1]).path.rstrip('/'))" "$LIVE_URL")"
  PYIMG=python:3.12-slim; docker image inspect "$PYIMG" >/dev/null 2>&1 || PYIMG=public.ecr.aws/docker/library/python:3.12-slim
  docker rm -f idp-kind-model-forward >/dev/null 2>&1 || true
  docker run -d --name idp-kind-model-forward --restart unless-stopped --network host -v "$PWD/tools/model_forward.py:/model_forward.py:ro" \
    "$PYIMG" python /model_forward.py "$KIND_GW:18088" "$TARGET" >/dev/null
  MODEL_BASE="http://$KIND_GW:18088$LIVE_PATH"
  for _ in $(seq 1 20); do curl -sf --noproxy '*' -m 5 -o /dev/null "$MODEL_BASE/models" && break; sleep 0.5; done
  curl -sf --noproxy '*' -m 5 -o /dev/null "$MODEL_BASE/models" || { docker logs idp-kind-model-forward 2>&1 | tail -5; echo "BLOCKED: the model forwarder at $MODEL_BASE does not answer" >&2; exit 2; }
  log "live model $LIVE_MODEL at $LIVE_URL, for the pods at $MODEL_BASE"
fi

# ---------------------------------------------------------------- a fresh platform
log "deleting the platform's namespaces"
kubectl delete ns "${NAMESPACES[@]}" --ignore-not-found --wait=true --timeout=300s >/dev/null

# ---------------------------------------------------------------- identity: one realm, one tenant, a CA
TENANT="$(python3 -c 'import uuid; print(uuid.uuid4())')"
rm -rf "$L/tls" "$L/keycloak"; mkdir -p "$L/tls" "$L/keycloak"
umask 077
# the test CA is kept across runs (.local/kind-ca): a browser that trusts it once keeps working after every full.sh (the
# shell's sign-in fetches Keycloak's discovery document itself, and an untrusted certificate stops it); only the server
# certificate is new each time
mkdir -p .local/kind-ca
[[ -f .local/kind-ca/ca.crt && -f .local/kind-ca/ca.key ]] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=IDP TELOS platform test CA" -keyout .local/kind-ca/ca.key -out .local/kind-ca/ca.crt 2>/dev/null
cp .local/kind-ca/ca.key "$L/tls/ca.key"; cp .local/kind-ca/ca.crt "$L/tls/ca.crt"
openssl req -newkey rsa:2048 -nodes -subj "/CN=keycloak" -keyout "$L/tls/server.key" -out "$L/tls/server.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1,DNS:localhost,DNS:keycloak,DNS:keycloak.keycloak,DNS:keycloak.keycloak.svc,DNS:%s\n' "$KCS" > "$L/tls/san.ext"
openssl x509 -req -in "$L/tls/server.csr" -CA "$L/tls/ca.crt" -CAkey "$L/tls/ca.key" -CAcreateserial -days 30 -extfile "$L/tls/san.ext" -out "$L/tls/server.crt" 2>/dev/null
# PostgreSQL's server certificate, from the same CA: the components connect with sslmode=verify-full and PGSSLROOTCERT
openssl req -newkey rsa:2048 -nodes -subj "/CN=postgresql.databases.svc.cluster.local" -keyout "$L/tls/pg.key" -out "$L/tls/pg.csr" 2>/dev/null
PGSAN=""; for n in postgresql postgresql-rw postgresql-ro postgresql-r; do PGSAN+="DNS:$n,DNS:$n.databases,DNS:$n.databases.svc,DNS:$n.databases.svc.cluster.local,"; done
printf 'subjectAltName=%sDNS:localhost\nextendedKeyUsage=serverAuth\n' "$PGSAN" > "$L/tls/pg-san.ext"
openssl x509 -req -in "$L/tls/pg.csr" -CA "$L/tls/ca.crt" -CAkey "$L/tls/ca.key" -CAcreateserial -days 30 -extfile "$L/tls/pg-san.ext" -out "$L/tls/pg.crt" 2>/dev/null
IDP_REALM_GATEWAY="$GATEWAY_URL" IDP_REALM_SHELL="$WS" python3 deploy/keycloak/realm.py "$L/keycloak" "$TENANT" "$L/clients.json" >/dev/null
secret_of() { python3 -c "import json,sys; d=json.load(open('$L/clients.json')); print(d['users'].get(sys.argv[1]) or d[sys.argv[1]])" "$1"; }
token_json() { python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
KC=(curl -sf --noproxy '*' --cacert "$L/tls/ca.crt")
user_token() { "${KC[@]}" "$ISSUER/protocol/openid-connect/token" -d grant_type=password -d client_id=e2e-cli -d "client_secret=$(secret_of e2e-cli)" -d "username=$1" -d "password=$(secret_of "$1")" | token_json; }
rnd() { openssl rand -hex 16; }
PG=$(rnd) CMI_DB=$(rnd) HOST_DB=$(rnd) WS_DB=$(rnd) MCP_DB=$(rnd) EER_OWNER=$(rnd) EER_APP=$(rnd) EER_RO=$(rnd) EER_IOWN=$(rnd) EER_IAPP=$(rnd) MCP_ADMIN="$(openssl rand -hex 32)"
PGH=postgresql.databases.svc.cluster.local:5432

# ---------------------------------------------------------------- render
log "render"
APIIP="$(kubectl get endpointslices -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].endpoints[0].addresses[0]}')"
BACKUP_GW="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1)"
[[ -n "$BACKUP_GW" ]] || { echo "BLOCKED: no IPv4 gateway on the kind network (the backup bucket is reached through it)" >&2; exit 2; }
mkdir -p "$L/overlay"
cat > "$L/overlay/kustomization.yaml" <<EOF
# generated by tests/kind/full.sh: the full overlay plus this cluster's API server address for the sandbox's egress rule
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../deploy/kubernetes/full
patches:
  - target: {kind: NetworkPolicy, name: sandbox, namespace: idp-sandbox}
    patch: |-
      - {op: replace, path: /spec/egress/0/to/0/ipBlock/cidr, value: "$APIIP/32"}
  - target: {kind: NetworkPolicy, name: pg-backup, namespace: databases}
    patch: |-
      - {op: replace, path: /spec/egress/2/to/0/ipBlock/cidr, value: "$BACKUP_GW/32"}
EOF
if [[ -n "${SANDBOX_RUNTIME_CLASS:-}" ]]; then   # e.g. gvisor (idp-sandbox/tests/kind/gvisor.sh installs it): the sandbox's run Pods use it
  cat >> "$L/overlay/kustomization.yaml" <<YAML
  - target: {kind: Deployment, name: idp-sandbox, namespace: idp-sandbox}
    patch: |-
      - {op: add, path: /spec/template/spec/containers/0/env/-, value: {name: IDP_SANDBOX_RUNTIME_CLASS, value: "$SANDBOX_RUNTIME_CLASS"}}
YAML
fi
if go run sigs.k8s.io/kustomize/kustomize/v5@v5.7.1 build "$L/overlay" > "$L/platform.yaml" 2>"$L/kustomize.err"; then :; else
  kubectl kustomize "$L/overlay" > "$L/platform.yaml" || { cat "$L/kustomize.err" >&2; echo "BLOCKED: kustomize" >&2; exit 2; }
fi
if [[ "$MODEL" == live ]]; then
  # the HOST's provider is the host's model through the forwarder, and the HOST's namespace may reach that address
  python3 - "$L/platform.yaml" "$MODEL_BASE" "$KIND_GW" "$LIVE_MODEL" <<'PY'
import sys, yaml
path, base, gw, live_model = sys.argv[1:5]
docs = [d for d in yaml.safe_load_all(open(path)) if d]
for d in docs:
    if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "idp-host-providers":
        host = yaml.safe_load(d["data"]["host.yaml"])
        host["providers"] = [dict(p, baseUrl=base) if p["name"] == "local" else p for p in host["providers"]]
        host["cmi"]["remember"]["facts"]["model"] = live_model   # the fact extraction uses the same live model
        d["data"]["host.yaml"] = yaml.safe_dump(host, sort_keys=False)
docs.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "kind-live-model", "namespace": "idp-host"},
             "spec": {"podSelector": {}, "policyTypes": ["Egress"],
                      "egress": [{"to": [{"ipBlock": {"cidr": gw + "/32"}}], "ports": [{"protocol": "TCP", "port": 18088}]}]}})
yaml.safe_dump_all(docs, open(path, "w"), sort_keys=False)
PY
fi
# split into phases: namespaces; databases (+ keycloak, temporal); the components; their setup Jobs (after the migrations)
PG_HA="$PG_HA" python3 - "$L/platform.yaml" "$L" <<'PY'
import os, sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
out = sys.argv[2]
def ns(d): return d["metadata"].get("namespace")
# every workload that may open a database connection (components, migrations, setup Jobs) verifies PostgreSQL's certificate: the CA
# comes from the ConfigMap pg-ca of its namespace and PGSSLROOTCERT names it (libpq, pgx and asyncpg all read that variable)
def pod_spec(d):
    if d["kind"] in ("Deployment", "StatefulSet", "Job"): return d["spec"]["template"]["spec"]
    if d["kind"] == "CronJob": return d["spec"]["jobTemplate"]["spec"]["template"]["spec"]
for d in docs:
    spec = pod_spec(d) if ns(d) in ("idp-cmi", "idp-host", "idp-workspace", "idp-mcp", "idp-eer") else None
    if not spec: continue
    spec.setdefault("volumes", []).append({"name": "pg-ca", "configMap": {"name": "pg-ca"}})
    for c in spec.get("containers", []) + spec.get("initContainers", []):
        c.setdefault("volumeMounts", []).append({"name": "pg-ca", "mountPath": "/etc/idp/pg-ca", "readOnly": True})
        c.setdefault("env", []).append({"name": "PGSSLROOTCERT", "value": "/etc/idp/pg-ca/ca.crt"})
def setup(d): return d["kind"] == "Job" and (d["metadata"].get("labels", {}).get("idp-telos/phase") == "setup" or d["metadata"]["name"] == "eer-configure")
if os.environ.get("PG_HA") == "1":   # the single PostgreSQL gives way to the CloudNativePG cluster (deploy/kubernetes/ha-postgres)
    single = {("Deployment", "postgresql"), ("Service", "postgresql"), ("PersistentVolumeClaim", "postgres-data"), ("ConfigMap", "postgres-hba")}
    docs = [d for d in docs if not (ns(d) == "databases" and (d["kind"], d["metadata"]["name"]) in single)]
phases = {"0-namespaces": [d for d in docs if d["kind"] == "Namespace"],
          "1-infra": [d for d in docs if d["kind"] != "Namespace" and ns(d) in ("databases", "keycloak", "temporal")],
          "3-setup": [d for d in docs if setup(d)]}
rest = [d for d in docs if not any(d is x for v in phases.values() for x in v)]
phases["2-components"] = rest
for k, v in phases.items():
    yaml.safe_dump_all(v, open(f"{out}/phase-{k}.yaml", "w"))
PY
kubectl apply -f "$L/phase-0-namespaces.yaml" >/dev/null

# ---------------------------------------------------------------- PostgreSQL, Keycloak, Temporal
log "PostgreSQL, Keycloak, Temporal"
kubectl -n databases create secret generic postgres-owner --from-literal=password="$PG" >/dev/null
if [[ "$PG_HA" == 1 ]]; then   # HA: the operators, and the cluster's three secrets (the superuser is the same owner role, same password)
  # the policies of the operators' namespaces (and the instances') first, so that everything the operators do runs under them; then
  # CloudNativePG, cert-manager and the Barman Cloud plugin as production runs them (two replicas each of the operator and the plugin)
  for ns_ in cnpg-system cert-manager; do kubectl create namespace "$ns_" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; done
  # the API server calls a webhook on another node from the control plane's address on the Pod network (Calico's tunnel address), not
  # from its node address: on one node the two are never needed apart, on several the certificates were never issued without it
  APITUN="$(kubectl get nodes -o json | python3 -c '
import json, sys
for n in json.load(sys.stdin)["items"]:
    if any(a["address"] == sys.argv[1] for a in n["status"]["addresses"]):
        ann = n["metadata"].get("annotations", {})
        print(ann.get("projectcalico.org/IPv4IPIPTunnelAddr") or ann.get("projectcalico.org/IPv4VXLANTunnelAddr") or sys.argv[1])' "$APIIP")"
  sed -e "s#203.0.113.10/32#$APIIP/32#" -e "s#203.0.113.11/32#${APITUN:-$APIIP}/32#" -e "s#203.0.113.20/32#$BACKUP_GW/32#" deploy/kubernetes/ha-postgres/platform.yaml | kubectl apply -f - >/dev/null
  KIND_CLUSTER="$CLUSTER" bash tests/kind/ha-operators.sh databases >/dev/null
  kubectl -n databases create secret generic postgres-superuser --type=kubernetes.io/basic-auth --from-literal=username=postgres --from-literal=password="$PG" >/dev/null
  kubectl -n databases create secret generic postgres-server-ca --from-file=ca.crt="$L/tls/ca.crt" >/dev/null
  kubectl -n databases create secret tls postgres-tls --cert="$L/tls/pg.crt" --key="$L/tls/pg.key" >/dev/null
  for s_ in postgres-superuser postgres-server-ca postgres-tls; do kubectl -n databases label secret "$s_" cnpg.io/reload=true >/dev/null; done
fi
# the pod to run psql in: the single PostgreSQL's Deployment, or the cluster's current primary
pgtarget() { if [[ "$PG_HA" == 1 ]]; then echo "pod/$(kubectl -n databases get cluster postgresql -o jsonpath='{.status.currentPrimary}')"; else echo deploy/postgresql; fi; }
[[ "$PG_HA" == 1 ]] || kubectl -n databases create secret generic postgres-tls --from-file=tls.crt="$L/tls/pg.crt" --from-file=tls.key="$L/tls/pg.key" >/dev/null   # HA: created above, as the kubernetes.io/tls type the cluster wants
for ns_ in databases idp-cmi idp-host idp-workspace idp-mcp idp-eer; do kubectl -n "$ns_" create configmap pg-ca --from-file=ca.crt="$L/tls/ca.crt" >/dev/null; done
# the backup target: an S3-compatible store on the HOST (outside the cluster), reached through the kind network's gateway (RustFS:
# tests/kind/s3-target.sh), and the age key pair whose private half stays in $L and never enters the cluster
BK_AK="backup$(openssl rand -hex 4)"; BK_SK="$(openssl rand -hex 16)"
S3_ACCESS_KEY="$BK_AK" S3_SECRET_KEY="$BK_SK" bash tests/kind/s3-target.sh idp-kind-backup-target "$BACKUP_GW" 19000
docker run --rm --network host idp-pg-backup:dev age-keygen 2>"$L/backup-age.pub" >"$L/backup-identity.txt"
sed -n 's/^Public key: //p' "$L/backup-age.pub" > "$L/backup-recipients.txt"; rm -f "$L/backup-age.pub"
kubectl -n databases create secret generic backup-target --from-literal=S3_ENDPOINT="http://$BACKUP_GW:19000" --from-literal=S3_BUCKET=backups \
  --from-literal=S3_ACCESS_KEY="$BK_AK" --from-literal=S3_SECRET_KEY="$BK_SK" >/dev/null
kubectl -n databases create configmap backup-recipients --from-file=recipients.txt="$L/backup-recipients.txt" >/dev/null
kubectl -n keycloak create secret generic keycloak-admin --from-literal=password="$(rnd)" >/dev/null
kubectl -n keycloak create secret generic keycloak-realm --from-file=idp-realm.json="$L/keycloak/idp-realm.json" >/dev/null
kubectl -n keycloak create secret generic keycloak-tls --from-file=server.crt="$L/tls/server.crt" --from-file=server.key="$L/tls/server.key" >/dev/null
kubectl apply -f "$L/phase-1-infra.yaml" >/dev/null
if [[ "$PG_HA" == 1 ]]; then
  # the cluster with continuous archiving of the WAL and a base backup to the same object store, through the Barman Cloud plugin
  # (deploy/kubernetes/ha-postgres/pitr.yaml): the ObjectStore first, then cluster.yaml with pitr.yaml's patch merged in (a new
  # cluster has the plugin from its first instance; the patch on its own is how a running cluster moves), then the ScheduledBackup.
  # The policies are in place (above).
  kubectl -n databases create secret generic pitr-s3 --from-literal=ACCESS_KEY_ID="$BK_AK" --from-literal=ACCESS_SECRET_KEY="$BK_SK" >/dev/null
  python3 - "$BACKUP_GW" deploy/kubernetes/ha-postgres/pitr.yaml "$L" <<'PY'
import sys, yaml
by = {d.get("kind", "patch"): d for d in yaml.safe_load_all(open(sys.argv[2]).read().replace("203.0.113.20", sys.argv[1])) if d}
open(f"{sys.argv[3]}/pitr-objectstore.yaml", "w").write(yaml.safe_dump(by["ObjectStore"]))
open(f"{sys.argv[3]}/pitr-schedule.yaml", "w").write(yaml.safe_dump(by["ScheduledBackup"]))
PY
  # one node: the instances share it (tools/ha_render.py merges deploy/kubernetes/ha-postgres/single-node.yaml and says so)
  kubectl get nodes -o json | python3 -I tools/ha_render.py --merge deploy/kubernetes/ha-postgres/pitr.yaml --nodes - > "$L/pitr-cluster.yaml"
  kubectl apply -f "$L/pitr-objectstore.yaml" >/dev/null
  kubectl apply -f "$L/pitr-cluster.yaml" >/dev/null
  kubectl apply -f "$L/pitr-schedule.yaml" >/dev/null
  for _ in $(seq 1 240); do [[ "$(kubectl -n databases get cluster postgresql -o jsonpath='{.status.readyInstances}' 2>/dev/null)" == 3 ]] && break; sleep 3; done
  [[ "$(kubectl -n databases get cluster postgresql -o jsonpath='{.status.readyInstances}')" == 3 ]] || { kubectl -n databases get cluster,pods; echo "FAIL: the PostgreSQL cluster did not reach 3 ready instances" >&2; exit 1; }
  # the first base backup completes and the WAL is archived: without them there is no recovery point
  for _ in $(seq 1 100); do [[ "$(kubectl -n databases get backups -o jsonpath='{.items[*].status.phase}' 2>/dev/null)" == *completed* ]] && break; sleep 3; done
  [[ "$(kubectl -n databases get backups -o jsonpath='{.items[*].status.phase}')" == *completed* ]] || { kubectl -n databases get backups,cluster; echo "FAIL: no base backup of the HA cluster completed" >&2; exit 1; }
else
  kubectl -n databases rollout status deploy/postgresql --timeout=300s
fi
for db in idp_cmi idp_host idp_workspace idp_mcp eer; do kubectl -n databases exec -c postgres $(pgtarget) -- psql -U postgres -q -c "CREATE DATABASE $db" >/dev/null; done
# idp-eer: its roles (deploy/postgres/bootstrap-runtime-roles.sql)
kubectl -n databases exec -i -c postgres $(pgtarget) -- psql -U postgres --no-psqlrc -q --set ON_ERROR_STOP=1 \
  --set "runtime_owner_password=$EER_OWNER" --set "runtime_app_password=$EER_APP" --set "runtime_readonly_password=$EER_RO" \
  --set "intelligence_owner_password=$EER_IOWN" --set "intelligence_app_password=$EER_IAPP" --dbname eer < idp-eer/deploy/postgres/bootstrap-runtime-roles.sql >/dev/null
kubectl -n keycloak rollout status deploy/keycloak --timeout=600s
pf keycloak keycloak 17443:7443
for _ in $(seq 1 120); do "${KC[@]}" -o /dev/null "$ISSUER/.well-known/openid-configuration" && break; sleep 1; done
"${KC[@]}" -o /dev/null "$ISSUER/.well-known/openid-configuration" || { kubectl -n keycloak logs deploy/keycloak --tail 30; echo "BLOCKED: Keycloak did not come up" >&2; exit 2; }

# ---------------------------------------------------------------- secrets and per-run configuration
log "secrets"
mksecret() { local ns="$1" name="$2"; shift 2; kubectl -n "$ns" create secret generic "$name" "$@" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; }
for c in idp-cmi idp-host idp-workspace; do kubectl apply -f "$c/deploy/kubernetes/secrets.example.yaml" >/dev/null; kubectl -n "$c" create configmap platform-tenant --from-literal=tenant-id="$TENANT" >/dev/null; done
kubectl -n idp-mcp create configmap platform-tenant --from-literal=tenant-id="$TENANT" >/dev/null
kubectl -n idp-workspace create configmap assistant-app --from-file=deploy/apps/assistant/index.html \
  --from-file=deploy/apps/assistant/app.js --from-file=deploy/apps/assistant/style.css >/dev/null
dbsecret() { # dbsecret NAMESPACE SECRET DATABASE OWNER-LOGIN SERVICE-LOGIN APP-PASSWORD
  mksecret "$1" "$2" --from-literal=owner-url="postgres://postgres:$PG@$PGH/$3?sslmode=verify-full" \
    --from-literal=service-url="postgres://$5:$6@$PGH/$3?sslmode=verify-full" --from-literal=app-password="$6"; }
dbsecret idp-cmi idp-cmi-db idp_cmi x idp_cmi_svc "$CMI_DB"
dbsecret idp-host idp-host-db idp_host x idp_host_svc "$HOST_DB"
dbsecret idp-workspace idp-workspace-db idp_workspace x idp_ws_svc "$WS_DB"
dbsecret idp-mcp idp-mcp-registry-db idp_mcp x idp_mcp_svc "$MCP_DB"
mksecret idp-mcp idp-mcp-admin --from-literal=secret="$MCP_ADMIN"
# idp-host: its key, the model/CMI credentials, the delegation client secret (the HOST's own identity at the CMI is its Keycloak service account)
mksecret idp-host idp-host-credential-key --from-literal=key="$(openssl rand -hex 32)"
# the HOST's own client secret for the CMI: it mints and refreshes its own tokens (idp-host D36), so that
# nothing here expires (a token handed over at deployment time stopped working an hour later)
mksecret idp-host idp-host-provider-keys --from-literal=default=unused --from-literal=cmi-client-secret="$(secret_of idp-host-svc)"
mksecret idp-host idp-host-gateway-client --from-literal=client-secret="$(secret_of idp-host)"
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$L/signing.pem" 2>/dev/null
mksecret idp-workspace idp-workspace-signing-key --from-file=signing.pem="$L/signing.pem"
mksecret idp-workspace idp-workspace-exchange --from-literal=client-secret="$(secret_of idp-workspace)"
mksecret idp-workspace idp-workspace-webhook --from-literal=secret="$(openssl rand -hex 32)"
mksecret idp-eer eer-db --from-literal=owner-dsn="postgresql://eer_runtime_owner:$EER_OWNER@$PGH/eer?sslmode=verify-full" \
  --from-literal=app-dsn="postgresql://eer_runtime_app:$EER_APP@$PGH/eer?sslmode=verify-full"
mksecret idp-eer eer-workspace-inbox --from-literal=client-secret="$(secret_of svc-eer)"
mksecret idp-eer eer-identity-ca --from-file=ca.crt="$L/tls/ca.crt"
python3 -I deploy/kubernetes/full/eer_identity.py "$L/clients.json" "$ISSUER" "https://$KCS:7443/realms/idp/protocol/openid-connect/certs" > "$L/eer-identity.env"
kubectl -n idp-eer create configmap eer-identity --from-env-file="$L/eer-identity.env" >/dev/null

# ---------------------------------------------------------------- components, migrations, logins, tenants
log "components"
kubectl apply -f "$L/phase-2-components.yaml" >/dev/null
waitjobs() { # waitjobs SELECTOR: every Job with the selector in the platform's namespaces completes
  local ns
  for ns in idp-cmi idp-host idp-workspace idp-eer idp-mcp; do
    kubectl -n "$ns" wait --for=condition=complete job -l "$1" --timeout=600s >/dev/null || { kubectl -n "$ns" get pods; kubectl -n "$ns" logs -l "job-name" --all-containers --tail=20 || true; echo "BLOCKED: jobs ($1) in $ns" >&2; exit 2; }
  done
}
log "migrations"
waitjobs app.kubernetes.io/component=migrate
log "logins, tenants, idp-eer configuration"
kubectl apply -f "$L/phase-3-setup.yaml" >/dev/null
for ns in idp-cmi idp-host idp-workspace idp-mcp; do
  kubectl -n "$ns" wait --for=condition=complete job -l idp-telos/phase=setup --timeout=600s >/dev/null || { kubectl -n "$ns" logs -l job-name --all-containers --tail=20; echo "BLOCKED: setup jobs in $ns" >&2; exit 2; }
  got="$(kubectl -n "$ns" logs job/kind-tenant | sed -n 's/^TENANT_ID=//p')"
  [[ "$got" == "$TENANT" ]] || { echo "BLOCKED: $ns tenant '$got' != $TENANT" >&2; exit 2; }
done
kubectl -n idp-eer wait --for=condition=complete job/eer-configure --timeout=600s >/dev/null || { kubectl -n idp-eer logs job/eer-configure --tail=30; echo "BLOCKED: eer-configure" >&2; exit 2; }
log "rollouts"
for ns in idp-cmi idp-host idp-workspace idp-eer; do kubectl -n "$ns" rollout restart deploy >/dev/null; done   # pick up the logins and schemas created after the first start
kubectl -n idp-mcp rollout restart deploy/idp-mcp-registry >/dev/null
for ns in idp-cmi idp-host idp-workspace idp-eer; do
  for d in $(kubectl -n "$ns" get deploy -o name); do kubectl -n "$ns" rollout status "$d" --timeout=300s >/dev/null || { echo "BLOCKED: $ns/$d" >&2; exit 2; }; done
done
for d in idp-mcp/idp-mcp-registry idp-sandbox/idp-sandbox idp-host/fakemodel temporal/temporal; do kubectl -n "${d%/*}" rollout status "deploy/${d#*/}" --timeout=300s >/dev/null; done

pf idp-workspace idp-workspace 17560:8560; pf idp-host idp-host 17450:8450; pf idp-cmi idp-cmi 17550:8550
pf idp-eer eer-api 17180:8080; pf idp-mcp idp-mcp-registry 17250:8250
call() { # call EXPECTED METHOD URL TOKEN [BODY]
  local want="$1" method="$2" url="$3" token="$4" body="${5:-}" out status
  out="$(mktemp)"
  status="$(curl -s --noproxy '*' -o "$out" -w '%{http_code}' -X "$method" "$url" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' ${body:+--data "$body"})"
  if [[ "$status" != "$want" ]]; then echo "FAIL: $method $url -> $status (want $want): $(cat "$out")" >&2; rm -f "$out"; exit 1; fi
  cat "$out"; rm -f "$out"
}
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

# ---------------------------------------------------------------- idp-mcp: a gateway that verifies Keycloak tokens, the sandbox as its upstream
log "idp-mcp"
for _ in $(seq 1 60); do curl -sf --noproxy '*' -o /dev/null "$REG/api/v1/health" && break; sleep 1; done
mcp_token() { kubectl -n idp-mcp exec deploy/idp-mcp-registry -- /usr/local/bin/registry-service dev-token "$TENANT" "$1" "$2" | tail -1; }
MADMIN="$(mcp_token carol admin)"; MOPERATOR="$(mcp_token alice operator)"; MREVIEWER="$(mcp_token bob reviewer)"
GW="$(call 201 POST "$REG/api/v1/gateways" "$MADMIN" '{"name":"edge-1","authMode":"jwt","jwt":{"issuer":"'"$ISSUER"'","resource":"'"$GATEWAY_URL"'","jwksUrl":"http://'"$KCS"':7080/realms/idp/protocol/openid-connect/certs","tenantClaim":"tenant_id","rolesClaim":"roles"}}')"
GW_ID="$(echo "$GW" | field "['gateway']['gatewayId']")"; GW_TOKEN="$(echo "$GW" | field "['token']")"
mksecret idp-mcp idp-mcp-gateway-edge-1 --from-literal=gateway-id="$GW_ID" --from-literal=token="$GW_TOKEN"
python3 -I deploy/compose/mcp_setup.py "$REG" "$GW_ID" "$MADMIN" "$MOPERATOR" "$MREVIEWER" host-user "$SANDBOX_URL" >/dev/null \
  || { kubectl -n idp-mcp logs deploy/idp-mcp-registry --tail 20; kubectl -n idp-mcp logs deploy/idp-mcp-gateway-edge-1 --tail 20; echo "BLOCKED: the idp-mcp setup failed" >&2; exit 2; }
# the gateway is Ready only once a release is deployed (mcp_setup.py above)
kubectl -n idp-mcp rollout status deploy/idp-mcp-gateway-edge-1 --timeout=300s >/dev/null
pf idp-mcp idp-mcp-gateway-edge-1 17320:3220
for url in "http://127.0.0.1:17320/healthz" "$CMI/healthz" "$HOST/healthz" "$WS/healthz" "$EER/health/ready"; do
  for _ in $(seq 1 120); do curl -sf --noproxy '*' -o /dev/null "$url" && break; sleep 0.5; done
  curl -sf --noproxy '*' -o /dev/null "$url" || { echo "BLOCKED: $url" >&2; exit 2; }
done

ALICE="$(user_token alice)"; BOB="$(user_token bob)"; HADMIN="$(user_token hostadmin)"; HAPPROVER="$(user_token hostapprover)"
AGENT_MODEL=fake-1
[[ "$MODEL" == live ]] && AGENT_MODEL="$LIVE_MODEL"

# ---- CMI: the enterprise knowledge the answer must draw on ----
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"idp-host","displayName":"Conversations (idp-host)","kind":"agent","trust":"informational"}' >/dev/null
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"handbook","displayName":"Handbook","kind":"document-store","trust":"authoritative"}' >/dev/null
DOC="$(call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"handbook","uri":"handbook://pickup","title":"Pickup policy","sensitivity":"internal","acl":{},"text":"Pickup policy.\n\nParcels above 10000 USD need a manager to approve the pickup slot."}' | field "['documentId']")"
# the same rule in Chinese: a question asked in Chinese (the assistant app's example) does not retrieve the English text
call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"handbook","uri":"handbook://pickup-zh","title":"贵重包裹取件规定","sensitivity":"internal","acl":{},"text":"贵重包裹取件规定。\n\n价值超过 10000 美元的贵重包裹，取件时段需要经理批准。"}' >/dev/null

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
[[ "$RESULT" == *"— finished" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; kubectl -n idp-workspace logs deploy/idp-workspace --tail=30; kubectl -n idp-cmi logs deploy/idp-cmi --tail=30; echo "FAIL: no finished run in bob's inbox ('$RESULT')" >&2; exit 1; }
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
if [[ "$MODEL" == live ]]; then
  # the real model saw the CMI package: the answer carries the rule only the handbook document states
  ANSWER="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$RUN': print(it.get('summary',''))")"
  echo "$ANSWER" | grep -qi "manager" || { echo "FAIL: the live answer does not use the CMI context: $ANSWER" >&2; exit 1; }
  echo "live answer ($LIVE_MODEL): $(echo "$ANSWER" | tr '\n' ' ' | cut -c1-300)"
fi

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
[[ -n "$ITEM" ]] || { kubectl -n idp-eer logs deploy/eer-projector --tail=30; echo "FAIL: the approval wait of $EXEC never reached bob's inbox" >&2; exit 1; }
DECIDED="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" "{\"app\":\"eer-approvals\",\"action\":\"approval.approve\",\"itemId\":\"$ITEM\",\"input\":{}}")"
[[ "$(echo "$DECIDED" | field "['result']['accepted']")" == True ]] || { echo "FAIL: EER did not accept the decision: $DECIDED" >&2; exit 1; }
for _ in $(seq 1 120); do [[ "$(state_of)" == Completed ]] && break; sleep 0.5; done
[[ "$(state_of)" == Completed ]] || { kubectl -n idp-eer logs deploy/eer-worker --tail=30; echo "FAIL: $EXEC is $(state_of) after the approval" >&2; exit 1; }
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

# the assistant app (deploy/apps/assistant): a chat page in the shell for the three agents; PATH E and F use its `chat` action
call 201 POST "$WS/api/v1/apps" "$ALICE" "$(cat deploy/apps/assistant/manifest.json)" >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/assistant" "$ALICE" '{"version":"1.0.0"}' >/dev/null

# ---- PATH E: memory. bob tells the assistant something; the HOST remembers it in the CMI; in a NEW conversation the CMI brings it back ----
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"memory-assistant","displayName":"Assistant with memory","definition":{"instructions":"You are a helpful assistant with a memory. The context you are given may contain things the user told you earlier: use it. Answer briefly.","model":{"provider":"local","model":"'"$AGENT_MODEL"'"},"tools":{},"confirmation":{"default":"auto"},"context":{"cmi":true,"purpose":"assist","maxItems":5}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/memory-assistant/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null
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
  [[ -n "$res" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; kubectl -n idp-cmi logs deploy/idp-cmi --tail=30; echo "FAIL: no answer for '$1'" >&2; exit 1; }
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
[[ -n "$MEM" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; kubectl -n idp-cmi logs deploy/idp-cmi --tail=30; echo "FAIL: the HOST did not remember run $R1 in the CMI" >&2; exit 1; }
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
[[ -n "$READY" ]] || { kubectl -n idp-cmi logs deploy/idp-cmi --tail=30; echo "FAIL: the CMI never retrieved bob's memory for a question about it" >&2; exit 1; }
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
# as in compose: what the HOST holds for the CMI must be its client secret, not a token that expires (D36)
kubectl -n idp-host get secret idp-host-provider-keys -o jsonpath='{.data.cmi-client-secret}' | base64 -d \
  | grep -qE '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.' && { echo "FAIL: the CMI writer credential is a token, which expires" >&2; exit 1; }
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
# pgvector (idp-cmi D38): the CMI's migration installed the extension and the trigger that keeps the vector copy in step
PGV="$(kubectl -n databases exec -c postgres $(pgtarget) -- psql -U postgres -d idp_cmi -Atc "select (select extversion from pg_extension where extname='vector') || ' ' || (select count(*) from pg_trigger where tgname in ('memory_records_embedding_v','knowledge_chunks_embedding_v'))" 2>&1)"
[[ "$PGV" == "0.8.7 2" ]] || { echo "FAIL: pgvector not installed by the CMI migration (got: $PGV)" >&2; exit 1; }
echo "PASS: pgvector $PGV — the CMI's migration installed the extension and the vector-copy triggers"

# ---- PATH D: bob asks an agent to run code; it runs in the sandbox, through the gateway, as bob ----
SANDBOX_INSTRUCTIONS="Run the code the user gives you in the sandbox and report the result."
# a real model is told how the two tools fit together, and may write the code for what the user asks
[[ "$MODEL" == live ]] && SANDBOX_INSTRUCTIONS="You run code for the user in an isolated sandbox: create a workspace with sandbox_create, then run the code in it with sandbox_run (python or shell) - the code the user gives you, or code you write for what they ask - and report what it printed."
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"sandbox-assistant","displayName":"Sandbox assistant","definition":{"instructions":"'"$SANDBOX_INSTRUCTIONS"'","model":{"provider":"local","model":"'"$AGENT_MODEL"'"},"tools":{"gateway":"edge","allow":["sandbox__sandbox_create","sandbox__sandbox_run"]},"confirmation":{"default":"auto"}}}' >/dev/null
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
[[ "$DRESULT" == *"— finished" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; kubectl -n idp-mcp logs deploy/idp-mcp-gateway-edge-1 --tail=30; kubectl -n idp-sandbox logs deploy/idp-sandbox --tail=30; echo "FAIL: no finished sandbox run in bob's inbox ('$DRESULT')" >&2; exit 1; }
DANSWER="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$DRUN': print(it.get('summary',''))")"
if [[ "$MODEL" == live ]]; then   # the model words the result itself; the gateway's records below prove the tools ran
  [[ "$DANSWER" == *"42"* ]] || { echo "FAIL: the live answer does not report what the code printed: $DANSWER" >&2; exit 1; }
  echo "live answer ($LIVE_MODEL): $(echo "$DANSWER" | tr '\n' ' ' | cut -c1-300)"
else
  [[ "$DANSWER" == *"exit code 0"* && "$DANSWER" == *"answer 42"* ]] || { echo "FAIL: the sandbox result is not in bob's inbox: $DANSWER" >&2; exit 1; }
fi
# the gateway's record: the calls were made for bob (the subject) with a token issued to the HOST (the client), and delivered
BOB_ID="$(python3 -c "import json; print(json.load(open('$L/clients.json'))['ids']['bob'])")"
for _ in $(seq 1 40); do   # the gateway pushes its call records to the registry in batches
  INVS="$(call 200 GET "$REG/api/v1/invocations" "$MREVIEWER")"
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
kubectl -n idp-sandbox logs deploy/idp-sandbox 2>/dev/null | grep -q '"tool":"sandbox_run".*caller=' || { kubectl -n idp-sandbox logs deploy/idp-sandbox --tail=20; echo "FAIL: the sandbox did not see the caller" >&2; exit 1; }
echo "PASS: PATH D — bob -> Workspace -> HOST run $DRUN -> idp-mcp gateway (bob's token exchanged by the HOST) -> idp-sandbox (kubernetes backend, caller forwarded) -> code ran in an isolated pod -> 'answer 42' in bob's inbox"

# ---- PATH I: a tool call waits for the member, who decides it in the Workspace (idp-host D7, Workspace D22) ----
# The same two sandbox tools, but running code needs the run owner's confirmation. The shell's conversation page
# reaches the run, its pending call and the decision through the Workspace; this path drives the same endpoints.
call 201 POST "$HOST/api/v1/agents" "$HADMIN" '{"name":"careful-coder","displayName":"Careful coder","definition":{"instructions":"'"$SANDBOX_INSTRUCTIONS"'","model":{"provider":"local","model":"'"$AGENT_MODEL"'"},"tools":{"gateway":"edge","allow":["sandbox__sandbox_create","sandbox__sandbox_run"]},"confirmation":{"default":"auto","tools":{"sandbox__sandbox_run":"confirm"}},"limits":{"confirmationTimeoutSeconds":900}}}' >/dev/null
REL="$(call 201 POST "$HOST/api/v1/agents/careful-coder/releases" "$HADMIN" | field "['releaseId']")"
call 200 POST "$HOST/api/v1/releases/$REL/approve" "$HAPPROVER" >/dev/null
call 201 POST "$WS/api/v1/apps" "$ALICE" '{"schemaVersion":"idp.workspace.app/v1","name":"careful","version":"1.0.0","title":"Careful coder","publisher":"IDP","capabilities":[],"actions":[{"name":"run","title":"Run code (asks before it runs)","target":{"kind":"host.run","agent":"careful-coder"},"input":{"type":"object","required":["message"],"properties":{"message":{"type":"string"}},"additionalProperties":false}}]}' >/dev/null
call 201 PUT "$WS/api/v1/workspaces/us-ops/installations/careful" "$ALICE" '{"version":"1.0.0"}' >/dev/null
INV="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" '{"app":"careful","action":"run","input":{"message":"run python: print(\"answer\", 6 * 7)"}}')"
IRUN="$(echo "$INV" | field "['result']['id']")"
# the run stops, and the Workspace shows bob the call that waits with the arguments it would run
PENDING=""
for _ in $(seq 1 240); do
  PENDING="$(call 200 GET "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls" "$BOB" | python3 -c "
import json,sys
for c in json.load(sys.stdin)['toolCalls']:
    if c['state'] == 'PENDING_CONFIRMATION': print(c['toolCallId'], c['tool'], c.get('arguments','')[:200])")"
  [[ -n "$PENDING" ]] && break; sleep 0.5
done
[[ -n "$PENDING" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; echo "FAIL: no tool call waits for bob in run $IRUN" >&2; exit 1; }
CALLID="${PENDING%% *}"; CALLTOOL="$(echo "$PENDING" | awk '{print $2}')"
[[ "$CALLTOOL" == sandbox__sandbox_run ]] || { echo "FAIL: the waiting call is $CALLTOOL" >&2; exit 1; }
[[ "$(call 200 GET "$WS/v1/workspaces/us-ops/runs/$IRUN" "$BOB" | field "['status']")" == AWAITING_INPUT ]] || { echo "FAIL: run $IRUN is not AWAITING_INPUT" >&2; exit 1; }
# the run's events, relayed as JSON: the confirmation was asked for on the stream the shell follows
call 200 GET "$WS/v1/workspaces/us-ops/runs/$IRUN/events" "$BOB" | python3 -c "
import json,sys
names={e['event'].get('name') for e in json.load(sys.stdin)['events'] if e['type']=='CUSTOM'}
assert 'idp.host/confirmation.required' in names, names
print('the stream asked for the confirmation')" || { echo "FAIL: the relayed events do not ask for a confirmation" >&2; exit 1; }
# only bob: the run is not alice's to see or to decide, even though she is a member and an administrator
call 404 GET "$WS/v1/workspaces/us-ops/runs/$IRUN" "$ALICE" >/dev/null
call 404 POST "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls/$CALLID/decide" "$ALICE" '{"decision":"confirm"}' >/dev/null
call 422 POST "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls/$CALLID/decide" "$BOB" '{"decision":"maybe"}' >/dev/null
# bob allows it: the decision reaches idp-host, the run goes on and its result arrives in his inbox
call 200 POST "$WS/v1/workspaces/us-ops/runs/$IRUN/tool-calls/$CALLID/decide" "$BOB" '{"decision":"confirm"}' >/dev/null
IRESULT=""
for _ in $(seq 1 600); do
  IRESULT="$(call 200 GET "$WS/v1/workspaces/us-ops/inbox?kind=run" "$BOB" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['items']:
    if it['externalRef']=='run:$IRUN' and it['title'].endswith('— finished'): print(it.get('summary','').replace(chr(10),' '))")"
  [[ -n "$IRESULT" ]] && break; sleep 0.5
done
[[ -n "$IRESULT" ]] || { kubectl -n idp-host logs deploy/idp-host --tail=30; echo "FAIL: run $IRUN did not finish after bob's confirmation" >&2; exit 1; }
[[ "$IRESULT" == *42* ]] || { echo "FAIL: the confirmed run's answer does not report what the code printed: $IRESULT" >&2; exit 1; }
# and a rejected one: the agent is told, the tool never ran
INV="$(call 201 POST "$WS/v1/workspaces/us-ops/actions/invoke" "$BOB" '{"app":"careful","action":"run","input":{"message":"run python: print(\"rejected\")"},"threadId":"'"$(echo "$INV" | field "['result']['threadId']")"'"}')"
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
for _ in $(seq 1 240); do
  [[ "$(call 200 GET "$WS/v1/workspaces/us-ops/runs/$JRUN/tool-calls" "$BOB" | python3 -c "
import json,sys
print([c['state'] for c in json.load(sys.stdin)['toolCalls'] if c['tool']=='sandbox__sandbox_run'][0])")" == REJECTED ]] && break; sleep 0.5
done
echo "PASS: PATH I — bob asks careful-coder to run code -> idp-host stops the run for his confirmation -> the Workspace shows him the waiting call and its arguments (alice, an administrator, gets 404 for both) -> bob confirms through the Workspace (run $IRUN) -> the code ran and its output reached his inbox; a second call rejected (run $JRUN) never ran"

# ---- the assistant app: a chat page in the shell for both agents (deploy/apps/assistant), on its own origin ----
pf idp-workspace assistant 17561:8080
for _ in $(seq 1 60); do curl -sf --noproxy '*' -o /dev/null http://127.0.0.1:17561/ && break; sleep 0.5; done
curl -sf --noproxy '*' http://127.0.0.1:17561/app.js | grep -q 'idp.bridge/v1' || { echo "FAIL: the assistant's page is not served" >&2; exit 1; }
echo "PASS: the assistant app is installed in us-ops, its page served on its own origin (port-forward 17561); the shell is http://127.0.0.1:17560 (alice, bob; deploy/kubernetes/RUNBOOK.md)"

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

# ---- PostgreSQL over TLS: every client connection of the platform is encrypted; cleartext TCP is refused ----
SSLSTAT="$(kubectl -n databases exec -c postgres $(pgtarget) -- psql -U postgres -d postgres -tAc "SELECT count(*) FILTER (WHERE s.ssl) || ' ' || count(*) FILTER (WHERE NOT s.ssl) || ' ' || coalesce(string_agg(DISTINCT s.version, ','), '-') FROM pg_stat_ssl s JOIN pg_stat_activity a USING (pid) WHERE a.client_addr IS NOT NULL AND a.client_addr <> '127.0.0.1'")"
read -r NSSL NPLAIN VERS <<<"$SSLSTAT"
[[ "$NSSL" -gt 0 && "$NPLAIN" == 0 && "$VERS" =~ ^TLSv1\.[23](,TLSv1\.[23])?$ ]] || { echo "FAIL: PostgreSQL client connections (encrypted, in the clear, versions): $SSLSTAT" >&2; exit 1; }
if kubectl -n databases exec -c postgres $(pgtarget) -- env PGPASSWORD="$PG" psql "host=$([[ "$PG_HA" == 1 ]] && echo postgresql.databases.svc.cluster.local || echo 127.0.0.1) port=5432 user=postgres dbname=postgres sslmode=disable" -c 'select 1' >/dev/null 2>"$L/pg-plain.err"; then
  echo "FAIL: a cleartext TCP connection to PostgreSQL was accepted" >&2; exit 1
fi
grep -q "no encryption" "$L/pg-plain.err" || { cat "$L/pg-plain.err"; echo "FAIL: the cleartext connection was refused, but not by pg_hba for lack of encryption" >&2; exit 1; }
echo "PASS: PostgreSQL over TLS - $NSSL client connections from the platform's components, all encrypted ($VERS), none in the clear; a cleartext TCP connection is rejected by pg_hba"

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
