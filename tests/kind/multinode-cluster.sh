#!/usr/bin/env bash
# A second kind cluster, "ha", with several nodes (tests/kind/multinode.yaml: a control plane and three workers in the zones zone-a,
# zone-b, zone-c), for what one node cannot show about the HA PostgreSQL: instances spread over nodes and zones, a node drained for
# maintenance, a node lost (tests/kind/ha-postgres.sh and tests/kind/ha-maintenance.sh run on it). Calico as on the main cluster.
# It does not touch the main cluster "kind" and leaves kubectl's current context as it was (kind create switches it).
#
#   bash tests/kind/multinode-cluster.sh        create "ha" (again: nothing to do)
#   KIND_CLUSTER=ha bash tests/kind/ha-postgres.sh && bash tests/kind/ha-maintenance.sh
#   kind delete cluster --name ha               remove it (the main cluster keeps running)
#
# The images come from the main cluster's node or the Docker daemon (this host's Docker cannot pull from quay.io or Docker Hub; see
# deploy/kubernetes/RUNBOOK.md), crane through the shell's proxy otherwise.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
NAME=ha NODE_IMG=kindest/node:v1.35.0 CALICO=v3.30.3
CALICO_SHA256=9382d2b27a76f40c170454b408653e6d71e2205ef0aef069e942bb690e7381d0
L=.local/multinode; mkdir -p "$L"
log() { echo "== $*"; }
PREV="$(kubectl config current-context 2>/dev/null || true)"
restore() { [[ -z "$PREV" ]] || kubectl config use-context "$PREV" >/dev/null 2>&1 || true; }
trap restore EXIT
k() { kubectl --context "kind-$NAME" "$@"; }

log "the cluster $NAME (1 control plane, 3 workers in 3 zones)"
docker image inspect "$NODE_IMG" >/dev/null 2>&1 || { echo "BLOCKED: $NODE_IMG is not in Docker (idp-sandbox/tests/kind/local-cluster.sh loads it)" >&2; exit 2; }
# five kind nodes on one host share its inotify limits (128 instances by default): with them exhausted kube-proxy dies with "too many
# open files" on every node of both clusters, as after a restart of the machine. Raised through a node container (privileged; no sudo)
docker exec kind-control-plane sysctl -w fs.inotify.max_user_instances=8192 fs.inotify.max_user_watches=1048576 >/dev/null 2>&1 || true
kind get clusters 2>/dev/null | grep -qx "$NAME" || kind create cluster --name "$NAME" --config tests/kind/multinode.yaml --image "$NODE_IMG" --wait 0s
restore
docker exec "$NAME-control-plane" sysctl -w fs.inotify.max_user_instances=8192 fs.inotify.max_user_watches=1048576 >/dev/null

log "Calico $CALICO"
IMAGES=(quay.io/calico/cni:$CALICO quay.io/calico/node:$CALICO quay.io/calico/kube-controllers:$CALICO)
if [[ ! -s "$L/calico-images-$CALICO.tar" ]]; then
  if docker exec kind-control-plane crictl inspecti "${IMAGES[1]}" >/dev/null 2>&1; then   # the main cluster's node has them
    docker exec kind-control-plane ctr -n k8s.io images export --platform linux/amd64 /tmp/calico.tar "${IMAGES[@]}"
    docker exec kind-control-plane cat /tmp/calico.tar > "$L/calico-images-$CALICO.tar"   # its /tmp is a tmpfs, which docker cp cannot read
    docker exec kind-control-plane rm -f /tmp/calico.tar
  else
    for i in "${IMAGES[@]}"; do crane pull --platform linux/amd64 "$i" "$L/$(basename "${i%%:*}").tar"; kind load image-archive "$L/$(basename "${i%%:*}").tar" --name "$NAME" >/dev/null; done
  fi
fi
[[ ! -s "$L/calico-images-$CALICO.tar" ]] || kind load image-archive "$L/calico-images-$CALICO.tar" --name "$NAME" >/dev/null   # every node
[[ -f "$L/calico-$CALICO.yaml" ]] || curl -sSL --retry 5 --retry-all-errors -m 180 -o "$L/calico-$CALICO.yaml" "https://raw.githubusercontent.com/projectcalico/calico/$CALICO/manifests/calico.yaml"
[[ "$(sha256sum "$L/calico-$CALICO.yaml" | cut -d' ' -f1)" == "$CALICO_SHA256" ]] || { echo "BLOCKED: the Calico manifest does not match its pinned checksum" >&2; rm -f "$L/calico-$CALICO.yaml"; exit 2; }
sed 's#docker.io/calico/#quay.io/calico/#' "$L/calico-$CALICO.yaml" | k apply -f - >/dev/null
k -n kube-system rollout status ds/calico-node --timeout=300s >/dev/null
k wait --for=condition=Ready nodes --all --timeout=300s >/dev/null

ZONES="$(k get nodes -l '!node-role.kubernetes.io/control-plane' -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.labels.topology\.kubernetes\.io/zone} {end}')"
[[ "$(echo "$ZONES" | tr ' ' '\n' | grep -c '=zone-')" == 3 ]] || { echo "FAIL: expected 3 workers with a zone, got: $ZONES" >&2; exit 1; }
echo "PASS: cluster $NAME ready with Calico; workers: $ZONES; kubectl's context is still ${PREV:-unset}"
