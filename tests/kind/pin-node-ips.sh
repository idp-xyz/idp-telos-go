#!/usr/bin/env bash
# Keeps a kind cluster's node addresses across restarts of Docker or the machine. Docker hands out the addresses on the "kind"
# network anew at every start, in start order; kind follows on the inside (kubeadm config, the API server's certificate), but the
# NetworkPolicies that full.sh renders hold the API server's address as an ipBlock and silently go stale: every Pod that talks
# to the API server (cert-manager, the CloudNativePG operator and instances, the sandbox) times out and the platform after them.
#
#   bash tests/kind/pin-node-ips.sh            pin the nodes of "kind" (again: nothing to do)
#   KIND_CLUSTER=ha bash tests/kind/pin-node-ips.sh
#
# 1. a node without a static address is stopped, reconnected with --ip at the address it has now, and started (about a minute
#    for the cluster; nothing inside changes since the address does not);
# 2. NetworkPolicies whose rules to the API server (port 6443) name another address on the kind network than the API server's
#    now are rewritten to it, so a cluster that already drifted heals without re-running full.sh (which would empty its data).
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
NAME="${KIND_CLUSTER:-kind}"
k() { kubectl --context "kind-$NAME" "$@"; }
log() { echo "== $*"; }

mapfile -t NODES < <(kind get nodes --name "$NAME" 2>/dev/null)
[[ ${#NODES[@]} -gt 0 ]] || { echo "BLOCKED: no kind cluster $NAME" >&2; exit 2; }

declare -A IP=()
for n in "${NODES[@]}"; do
  [[ -z "$(docker inspect "$n" -f '{{with .NetworkSettings.Networks.kind.IPAMConfig}}{{.IPv4Address}}{{end}}')" ]] || continue
  IP[$n]="$(docker inspect "$n" -f '{{.NetworkSettings.Networks.kind.IPAddress}}')"
  [[ -n "${IP[$n]}" ]] || { echo "BLOCKED: $n has no address on the kind network (is it running?)" >&2; exit 2; }
done
if [[ ${#IP[@]} -gt 0 ]]; then
  log "pin ${!IP[*]} at ${IP[*]}"
  docker stop "${!IP[@]}" >/dev/null   # all at once: a stopped node's address is free again for a dynamic one
  for n in "${!IP[@]}"; do
    docker network disconnect kind "$n"
    docker network connect --ip "${IP[$n]}" kind "$n"
  done
  docker start "${!IP[@]}" >/dev/null
  for _ in $(seq 120); do k get --raw /readyz >/dev/null 2>&1 && break; sleep 1; done   # not node Ready: before Calico there is none
  k get --raw /readyz >/dev/null
else
  log "nodes of $NAME already pinned"
fi

log "API server address in the NetworkPolicies"
APIIP="$(k get endpointslices -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].endpoints[0].addresses[0]}')"
SUBNET="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -E '^[0-9.]+/[0-9]+$' | head -1)"
read -r -a STALE <<< "$(k get networkpolicy -A -o json | python3 -c '
import ipaddress, json, sys
api, subnet = sys.argv[1], ipaddress.ip_network(sys.argv[2])
stale = set()
for p in json.load(sys.stdin)["items"]:
    s = p["spec"]
    for rule in s.get("egress", []) + s.get("ingress", []):
        if 6443 not in {x.get("port") for x in rule.get("ports", [])}:
            continue
        for peer in rule.get("to", []) + rule.get("from", []):
            cidr = peer.get("ipBlock", {}).get("cidr", "")
            if cidr.endswith("/32") and cidr != api + "/32" and ipaddress.ip_address(cidr[:-3]) in subnet:
                stale.add(cidr)
print(" ".join(sorted(stale)))
' "$APIIP" "$SUBNET")"
if [[ ${#STALE[@]} -eq 0 ]]; then
  echo "PASS: nodes of $NAME pinned; the NetworkPolicies name the API server at $APIIP"
  exit 0
fi
[[ ${#STALE[@]} -eq 1 ]] || { echo "BLOCKED: several stale API server addresses (${STALE[*]}); re-render with tests/kind/full.sh" >&2; exit 2; }
log "rewrite ${STALE[0]} to $APIIP/32"
k get networkpolicy -A -o json | python3 -c '
import json, sys
old, new = sys.argv[1], sys.argv[2] + "/32"
out = []
for p in json.load(sys.stdin)["items"]:
    s = json.dumps(p["spec"])
    if "\"" + old + "\"" in s:
        out.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                    "metadata": {"name": p["metadata"]["name"], "namespace": p["metadata"]["namespace"]},
                    "spec": json.loads(s.replace("\"" + old + "\"", "\"" + new + "\""))})
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": out}))
' "${STALE[0]}" "$APIIP" | k apply -f -
echo "PASS: nodes of $NAME pinned; NetworkPolicies moved to the API server at $APIIP (Pods in CrashLoopBackOff recover at their next restart)"
