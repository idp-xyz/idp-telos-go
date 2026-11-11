#!/usr/bin/env bash
# What runs the HA PostgreSQL, installed on a kind cluster as production runs it (deploy/kubernetes/ha-postgres/README.md): CloudNativePG
# with two replicas (operator-ha.yaml), cert-manager (the plugin's certificates), and the Barman Cloud plugin with two replicas, limited
# to the namespaces whose clusters use it (tools/ha_plugin_manifest.py). Release manifests pinned by checksum; the images loaded into every
# node (from the Docker daemon, else crane through the shell's proxy). The network policies are the caller's: tests/kind/full.sh
# applies deploy/kubernetes/ha-postgres/platform.yaml before this, so that everything here starts under them.
#
#   KIND_CLUSTER=kind bash tests/kind/ha-operators.sh NAMESPACE...     the namespaces must exist (the plugin is bound in each)
#
# Used by tests/kind/full.sh (PG_HA=1: databases) and tests/kind/ha-postgres.sh (ARCHIVE=1: ha-test). Running it again is harmless;
# the plugin is restarted every time (see below).
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
CLUSTER="${KIND_CLUSTER:-kind}"; L=.local/ha-postgres; mkdir -p "$L"
kubectl() { command kubectl --context "kind-$CLUSTER" "$@"; }
[[ $# -ge 1 ]] || { echo "usage: KIND_CLUSTER=NAME $0 NAMESPACE... (the namespaces whose clusters use the plugin)" >&2; exit 2; }
for ns in "$@"; do kubectl get namespace "$ns" >/dev/null 2>&1 || { echo "BLOCKED: namespace $ns does not exist" >&2; exit 2; }; done

pinned() { # pinned URL FILE SHA256: a release manifest, downloaded once and checked against its pinned checksum on every run
  [[ -f "$2" ]] || curl -sSL --retry 5 --retry-all-errors -m 180 -o "$2" "$1"
  [[ "$(sha256sum "$2" | cut -d' ' -f1)" == "$3" ]] || { echo "BLOCKED: $2 does not match its pinned checksum" >&2; rm -f "$2"; exit 2; }
}
load() { # load IMAGE: into every node that does not have it
  local img="$1" tar missing=""
  for n in $(kind get nodes --name "$CLUSTER"); do docker exec "$n" crictl inspecti "$img" >/dev/null 2>&1 || missing=1; done
  [[ -n "$missing" ]] || return 0
  tar="$(mktemp --suffix=.tar)"
  if docker image inspect "$img" >/dev/null 2>&1; then docker save --platform linux/amd64 "$img" -o "$tar" 2>/dev/null || docker save "$img" -o "$tar"
  else crane pull --platform linux/amd64 "$img" "$tar" >/dev/null; fi
  kind load image-archive "$tar" --name "$CLUSTER" >/dev/null; rm -f "$tar"
}
for img in ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1 ghcr.io/cloudnative-pg/plugin-barman-cloud:v0.16.0 ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.16.0 \
           quay.io/jetstack/cert-manager-controller:v1.21.3 quay.io/jetstack/cert-manager-webhook:v1.21.3 quay.io/jetstack/cert-manager-cainjector:v1.21.3; do
  load "$img"
done

# CloudNativePG: the release manifest says imagePullPolicy: Always and one replica
pinned https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.1/cnpg-1.30.1.yaml "$L/cnpg.yaml" \
  37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb
kubectl apply --server-side --force-conflicts -f "$L/cnpg.yaml" >/dev/null
kubectl -n cnpg-system patch deploy cnpg-controller-manager --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]' >/dev/null
kubectl -n cnpg-system patch deploy cnpg-controller-manager --patch-file deploy/kubernetes/ha-postgres/operator-ha.yaml >/dev/null
kubectl -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=300s >/dev/null

# cert-manager: the plugin's gRPC certificates (a self-signed issuer in cnpg-system)
pinned https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml "$L/cert-manager-1.21.3.yaml" \
  c04a51295adda2b1a775146751f973e787190778c7f35180861d9678876e5c74
kubectl apply --server-side --force-conflicts -f "$L/cert-manager-1.21.3.yaml" >/dev/null
for d in cert-manager cert-manager-cainjector cert-manager-webhook; do kubectl -n cert-manager rollout status deploy/$d --timeout=300s >/dev/null; done

# the Barman Cloud plugin (WAL archiving, base backups, recovery; deploy/kubernetes/ha-postgres/pitr.yaml); cert-manager's webhook takes a
# few seconds to serve after its rollout
pinned https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.16.0/manifest.yaml "$L/plugin-barman-cloud-0.16.0.yaml" \
  aee614683892dd4b44ff9f7afaca55254f1c28de17abbaa0552bf2e1228f7155
python3 -I tools/ha_plugin_manifest.py "$@" < "$L/plugin-barman-cloud-0.16.0.yaml" > "$L/plugin-barman-cloud-$CLUSTER.yaml"
for _ in $(seq 1 40); do kubectl apply --server-side --force-conflicts -f "$L/plugin-barman-cloud-$CLUSTER.yaml" >/dev/null 2>"$L/plugin-apply.err" && break; sleep 3; done
kubectl apply --server-side --force-conflicts -f "$L/plugin-barman-cloud-$CLUSTER.yaml" >/dev/null || { cat "$L/plugin-apply.err" >&2; echo "FAIL: the Barman Cloud plugin could not be installed" >&2; exit 1; }
kubectl delete clusterrolebinding plugin-barman-cloud-binding --ignore-not-found >/dev/null   # an unscoped installation's
# restarted: its RoleBindings live in the namespaces, which a test run may have deleted and created again, and a plugin whose watches
# failed in the meantime answers "not found, requeue" until they recover; CloudNativePG 1.30.1 does not come back to a new Cluster
# after that answer (it stayed without a Pod until something touched it; README.md)
kubectl -n cnpg-system rollout restart deploy/barman-cloud >/dev/null
kubectl -n cnpg-system rollout status deploy/barman-cloud --timeout=300s >/dev/null
echo "PASS: CloudNativePG 1.30.1 ($(kubectl -n cnpg-system get deploy cnpg-controller-manager -o jsonpath='{.status.readyReplicas}') replicas), cert-manager 1.21.3," \
     "Barman Cloud plugin 0.16.0 ($(kubectl -n cnpg-system get deploy barman-cloud -o jsonpath='{.status.readyReplicas}') replicas, namespaces: $*) on kind-$CLUSTER"
