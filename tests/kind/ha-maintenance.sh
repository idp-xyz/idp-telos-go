#!/usr/bin/env bash
# Node maintenance and node loss for the HA PostgreSQL on several nodes: what one node cannot show (deploy/kubernetes/ha-postgres/README.md).
# Run after tests/kind/multinode-cluster.sh and KIND_CLUSTER=ha ARCHIVE=1 bash tests/kind/ha-postgres.sh, which leaves the cluster "postgresql"
# (3 instances on 3 nodes in 3 zones) in the namespace ha-test. A writer on the control plane (no drain or node loss reaches it)
# inserts numbered rows through postgresql-rw over verify-full TLS, a new connection for each, while:
#   1  the primary's node is drained (kubectl drain): CloudNativePG switches the primary over before its Pod may be evicted; the
#      evicted instance waits for its node (its volume is local to that node)
#   2  a second node is drained while that instance is away, once CloudNativePG's budget for the standbys allows no disruption (right
#      after a switchover it still allowed one, README.md): the PodDisruptionBudget refuses (one instance away at a time) and the
#      writes go on (a synchronous standby is left)
#   3  both nodes are back in service (uncordon): 3 ready instances on 3 nodes in 3 zones again
#   4  the primary's node is lost (docker stop: no drain, no warning), with the operator's leader and a plugin replica on it: the other
#      replicas take over and a standby is promoted; the node comes back (docker start) and
#      its instance rejoins as a standby
# After each: every acknowledged row is there; the longest stretch of refused writes is reported and limited. DEV ONLY (kind).
#
#   bash tests/kind/ha-maintenance.sh          KIND_CLUSTER=ha (default), the namespace ha-test
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
CLUSTER="${KIND_CLUSTER:-ha}"; NS=ha-test; L=.local/ha-maintenance; IMG=idp-postgres-ha:17-dev
kubectl() { command kubectl --context "kind-$CLUSTER" "$@"; }
mkdir -p "$L"
log() { echo "== $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
primary() { kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.currentPrimary}'; }
node_of() { kubectl -n "$NS" get pod "$1" -o jsonpath='{.spec.nodeName}'; }
ready() { kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.readyInstances}'; }
psql_p() { kubectl -n "$NS" exec -c postgres "$(primary)" -- psql -U postgres -d ha_test -qtAX -c "$1"; }
STOPPED=""
cleanup() {
  [[ -z "$STOPPED" ]] || docker start "$STOPPED" >/dev/null 2>&1 || true
  for n in $(kubectl get nodes -o name 2>/dev/null); do kubectl uncordon "${n#node/}" >/dev/null 2>&1 || true; done
  kubectl -n "$NS" delete pod maint-writer --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT
wait_ready() { # wait_ready N SECONDS
  for _ in $(seq 1 "$(( $2 / 3 ))"); do [[ "$(ready 2>/dev/null)" == "$1" ]] && return 0; sleep 3; done
  kubectl -n "$NS" get cluster,pods -o wide; fail "not $1 ready instances after $2 s"
}
# the writer's log since an instant: refused tries, the longest stretch of them (first refusal to the next acknowledgement)
refused_since() { # refused_since EPOCH -> "<longest s> <refused tries> <acknowledged>"
  kubectl -n "$NS" logs maint-writer > "$L/writer.log"
  python3 - "$L/writer.log" "$1" <<'PY'
import sys
since = float(sys.argv[2]); longest, first, fails, acks = 0.0, None, 0, 0
for kind, _, t in (l.split()[:3] for l in open(sys.argv[1]) if len(l.split()) >= 3):
    t = float(t)
    if t < since: continue
    if kind == "fail":
        fails += 1; first = t if first is None else first
    elif kind == "ack":
        acks += 1
        if first is not None: longest, first = max(longest, t - first), None
print(f"{'open' if first is not None else round(longest, 1)} {fails} {acks}")
PY
}
await_acks() { # await_acks EPOCH: until the writer is acknowledged again after EPOCH (3 minutes at most)
  for _ in $(seq 1 90); do r="$(refused_since "$1")"; [[ "${r%% *}" != open && "${r##* }" -gt 5 ]] && { echo "$r"; return 0; }; sleep 2; done
  echo "$r"
}
limit() { # limit SECONDS-REFUSED MAX WHAT
  [[ "$1" != open ]] || fail "$3: the writer is not acknowledged again"
  (( $(python3 -c "print(int(float('$1') > $2))") == 0 )) || fail "$3: writes were refused for $1 s in a row (limit $2)"
}
nothing_lost() {
  kubectl -n "$NS" logs maint-writer > "$L/writer.log"
  local acked present lost
  acked="$(awk '$1=="ack"{print $2}' "$L/writer.log" | LC_ALL=C sort -u)"
  present="$(psql_p "SELECT n FROM maint_log" | LC_ALL=C sort -u)"
  lost="$(LC_ALL=C comm -23 <(echo "$acked") <(echo "$present") | grep -c . || true)"
  [[ "$lost" == 0 ]] || fail "$lost acknowledged rows are missing"
  echo "$(echo "$acked" | grep -c .)"
}
placement() {
  kubectl -n "$NS" get pods -l cnpg.io/cluster=postgresql,cnpg.io/podRole=instance -o json > "$L/pods.json"; kubectl get nodes -o json > "$L/nodes.json"
  python3 - "$L/pods.json" "$L/nodes.json" <<'PY'
import json, sys
zones = {n["metadata"]["name"]: n["metadata"]["labels"].get("topology.kubernetes.io/zone", "-") for n in json.load(open(sys.argv[2]))["items"]}
print(" ".join(f'{p["metadata"]["name"]}@{p["spec"].get("nodeName", "-")}/{zones.get(p["spec"].get("nodeName"), "-")}:{p["status"]["phase"]}'
               for p in json.load(open(sys.argv[1]))["items"]))
PY
}

log "0 preconditions: 3 instances on 3 nodes in 3 zones; a writer on the control plane"
kubectl -n "$NS" get cluster postgresql >/dev/null 2>&1 || { echo "BLOCKED: no cluster in $NS on kind-$CLUSTER: run KIND_CLUSTER=$CLUSTER bash tests/kind/ha-postgres.sh first" >&2; exit 2; }
[[ "$(ready)" == 3 ]] || fail "the cluster has $(ready) ready instances, not 3"
P0="$(placement)"; [[ "$(echo "$P0" | tr ' ' '\n' | cut -d/ -f2 | cut -d: -f1 | grep -v '^-$' | sort -u | wc -l)" == 3 ]] || fail "the instances are not in 3 zones: $P0"
echo "   $P0"
kubectl -n "$NS" get pdb -o custom-columns=NAME:.metadata.name,MIN:.spec.minAvailable,MAX:.spec.maxUnavailable,ALLOWED:.status.disruptionsAllowed --no-headers | sed 's/^/   pdb /'
psql_p "CREATE TABLE IF NOT EXISTS maint_log (n bigint PRIMARY KEY, at timestamptz DEFAULT now())" >/dev/null
kubectl -n "$NS" delete pod maint-writer --ignore-not-found --wait=true >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: maint-writer, namespace: $NS}
spec:
  restartPolicy: Never
  nodeSelector: {node-role.kubernetes.io/control-plane: ""}
  tolerations: [{key: node-role.kubernetes.io/control-plane, operator: Exists, effect: NoSchedule}]
  securityContext: {runAsNonRoot: true, runAsUser: 26, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: writer
      image: $IMG
      imagePullPolicy: IfNotPresent
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
      env:
        - {name: PGHOST, value: postgresql-rw.$NS.svc.cluster.local}
        - {name: PGUSER, value: postgres}
        - {name: PGDATABASE, value: ha_test}
        - {name: PGSSLMODE, value: verify-full}
        - {name: PGSSLROOTCERT, value: /ca/ca.crt}
        - {name: PGCONNECT_TIMEOUT, value: "2"}
        - name: PGPASSWORD
          valueFrom: {secretKeyRef: {name: postgres-superuser, key: password}}
      volumeMounts: [{name: ca, mountPath: /ca, readOnly: true}]
      command: [bash, -c]
      args:
        - |
          # a retry resends the same row: ON CONFLICT makes it an acknowledgement when the first try did commit but its answer was lost
          n=1
          while true; do
            if timeout 5 psql -qAt -v ON_ERROR_STOP=1 -c "INSERT INTO maint_log(n) VALUES (\$n) ON CONFLICT DO NOTHING" >/dev/null 2>&1; then
              echo "ack \$n \$(date +%s.%N)"; n=\$((n+1)); else echo "fail \$n \$(date +%s.%N)"; fi
            sleep 0.1
          done
  volumes: [{name: ca, secret: {secretName: postgres-server-ca}}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/maint-writer --timeout=120s >/dev/null
r="$(await_acks 0)"; [[ "${r##* }" -gt 5 ]] || fail "the writer is not acknowledged"
echo "PASS: the writer is acknowledged on $(primary) from $(node_of maint-writer)"

log "1 the primary's node is drained"
P1="$(primary)"; N1="$(node_of "$P1")"; T1="$(date +%s.%N)"; S1=$SECONDS
kubectl drain "$N1" --ignore-daemonsets --delete-emptydir-data --timeout=300s > "$L/drain1.log" 2>&1 || { tail -5 "$L/drain1.log"; fail "draining $N1 failed"; }
D1=$((SECONDS - S1))
# the drain may end while the switchover is still going: once CloudNativePG starts it the old primary is relabelled and no budget
# holds it any more, so the eviction can take it during the switchover (which stops it anyway)
for _ in $(seq 1 60); do P2="$(primary)"; [[ "$P2" != "$P1" && "$(node_of "$P2" 2>/dev/null)" != "$N1" ]] && break; sleep 1; done
[[ "$P2" != "$P1" && "$(node_of "$P2")" != "$N1" ]] || fail "the primary is still $P2 on $N1 a minute after the drain"
for _ in $(seq 1 30); do [[ "$(kubectl -n "$NS" get pod "$P1" -o jsonpath='{.status.phase}' 2>/dev/null)" == Pending ]] && break; sleep 1; done
R1="$(await_acks "$T1")"; limit "${R1%% *}" 60 "drain"
[[ "$(kubectl -n "$NS" get pod "$P1" -o jsonpath='{.status.phase}')" == Pending ]] || fail "$P1 is not waiting for its node: $(placement)"
echo "PASS: $N1 drained in $D1 s: the primary moved $P1 -> $P2 on $(node_of "$P2") first (switchover), $P1 waits for its node (Pending);" \
     "writes refused for ${R1%% *} s at most ($(echo "$R1" | cut -d' ' -f2) tries), $(ready) instances ready"
grep -h -i -E 'evict|disruption' "$L/drain1.log" | sort | uniq -c | sort -rn | head -3 | sed 's/^/   drain: /'

log "2 a second node is drained while that instance is away"
# as the procedure says (README.md): only once CloudNativePG's budget for the standbys allows no disruption. After the switchover its
# status still counts the promoted standby as a healthy replica and allows one: the Pods left the budget by a change of label, and the
# disruption controller recomputes a budget when a Pod it selects changes, so it stayed stale (40 s and more) until the budget itself
# was touched. A drain in that state evicted the last standby. A budget of our own over every instance does not help: a Pod with two
# budgets cannot be evicted at all.
allowed() { kubectl -n "$NS" get pdb postgresql -o jsonpath='{.status.disruptionsAllowed}'; }
S2=$SECONDS; STALE=""
for _ in $(seq 1 20); do [[ "$(allowed)" == 0 ]] && break; sleep 0.5; done
if [[ "$(allowed)" != 0 ]]; then
  STALE="$(kubectl -n "$NS" get pdb postgresql -o jsonpath='expected {.status.expectedPods}, healthy {.status.currentHealthy}, allowed {.status.disruptionsAllowed}')"
  kubectl -n "$NS" annotate pdb postgresql idp.dev/recheck="$(date +%s)" --overwrite >/dev/null   # a change of the budget makes it recomputed
  for _ in $(seq 1 30); do [[ "$(allowed)" == 0 ]] && break; sleep 0.5; done
fi
[[ "$(allowed)" == 0 ]] || fail "the standbys' budget still allows a disruption with an instance away: $(kubectl -n "$NS" get pdb postgresql -o jsonpath='{.status}')"
if [[ -n "$STALE" ]]; then echo "   the standbys' budget was stale 10 s after the drain ($STALE) until it was touched; then it allowed none"
else echo "   the standbys' budget allowed no disruption $((SECONDS - S2)) s after the drain ended"; fi
SB="$(kubectl -n "$NS" get pods -l cnpg.io/cluster=postgresql,cnpg.io/podRole=instance -o jsonpath='{range .items[*]}{.metadata.name} {.status.phase}{"\n"}{end}' | awk -v p="$P2" '$2=="Running" && $1!=p {print $1}' | head -1)"
N2="$(node_of "$SB")"; T2="$(date +%s.%N)"
if kubectl drain "$N2" --ignore-daemonsets --delete-emptydir-data --timeout=45s > "$L/drain2.log" 2>&1; then fail "$N2 drained although $P1 is away: nothing stopped it"; fi
grep -q -i 'disruption budget' "$L/drain2.log" || { tail -5 "$L/drain2.log"; fail "the second drain failed for another reason than the disruption budget"; }
[[ "$(kubectl -n "$NS" get pod "$SB" -o jsonpath='{.status.phase}')" == Running ]] || fail "$SB was evicted"
R2="$(refused_since "$T2")"; limit "${R2%% *}" 5 "second drain"
kubectl uncordon "$N2" >/dev/null
echo "PASS: draining $N2 ($SB, the last standby) was refused by the PodDisruptionBudget ($(grep -i -o 'violate the pod.s disruption budget' "$L/drain2.log" | head -1));" \
     "$SB kept running, writes went on (${R2##* } acknowledged, ${R2%% *} s refused)"

log "3 the nodes are back in service"
kubectl uncordon "$N1" >/dev/null; S3=$SECONDS
wait_ready 3 300
P3="$(placement)"; [[ "$(echo "$P3" | tr ' ' '\n' | cut -d/ -f2 | cut -d: -f1 | sort -u | wc -l)" == 3 ]] || fail "after the maintenance the instances are not in 3 zones: $P3"
NA="$(nothing_lost)"   # an assignment: set -e stops the run when the check fails
echo "PASS: 3 ready instances $((SECONDS - S3)) s after uncordon, on 3 nodes in 3 zones ($P3); $NA acknowledged rows, none missing"

log "4 the primary's node is lost, with the operator's leader and a plugin replica on it (the worst case)"
# the operator and the Barman Cloud plugin run two replicas each (deploy/kubernetes/ha-postgres/operator-ha.yaml, tools/ha_plugin_manifest.py);
# one of each is made to run on the primary's node (the other workers cordoned while it is recreated), and the operator's leader is
# moved there (the leader deleted, so that the replica on that node takes the lease)
P4="$(primary)"; N4="$(node_of "$P4")"
pods_of() { kubectl -n cnpg-system get pods -l "$1" --field-selector=status.phase=Running -o jsonpath='{range .items[*]}{.metadata.name}={.spec.nodeName}{"\n"}{end}'; }
op_pods() { pods_of app.kubernetes.io/name=cloudnative-pg; }
leader() { kubectl -n cnpg-system get lease db9c8771.cnpg.io -o jsonpath="{.spec.holderIdentity}" | cut -d_ -f1; }   # the operator's lease (the plugin has its own)
place_on() { # place_on NODE SELECTOR [KEEP-POD]: a Running Pod of SELECTOR on NODE; KEEP-POD is not the one recreated
  local others victim
  pods_of "$2" | grep -q "=$1$" && return 0
  others="$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -vx "$1")"
  for n in $others; do kubectl cordon "$n" >/dev/null; done
  victim="$(pods_of "$2" | grep -v "^${3:-none}=" | head -1 | cut -d= -f1)"
  kubectl -n cnpg-system delete pod "$victim" --wait=true >/dev/null
  for _ in $(seq 1 60); do pods_of "$2" | grep -q "=$1$" && break; sleep 2; done
  for n in $others; do kubectl uncordon "$n" >/dev/null; done
  pods_of "$2" | grep -q "=$1$"
}
[[ "$(kubectl -n cnpg-system get deploy cnpg-controller-manager -o jsonpath='{.spec.replicas}')" -ge 2 ]] || fail "the operator runs one replica: apply deploy/kubernetes/ha-postgres/operator-ha.yaml"
place_on "$N4" app.kubernetes.io/name=cloudnative-pg "$(leader)" || fail "no operator replica could be started on $N4"
if [[ "$(op_pods | grep "^$(leader)=" | cut -d= -f2)" != "$N4" ]]; then
  kubectl -n cnpg-system delete pod "$(leader)" --wait=false >/dev/null
  for _ in $(seq 1 60); do [[ "$(op_pods | grep "^$(leader)=" | cut -d= -f2)" == "$N4" ]] && break; sleep 2; done
fi
[[ "$(op_pods | grep "^$(leader)=" | cut -d= -f2)" == "$N4" ]] || fail "the operator's leader could not be moved to $N4"
kubectl -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=180s >/dev/null
PLUGIN=""
if kubectl -n cnpg-system get deploy barman-cloud >/dev/null 2>&1; then
  [[ "$(kubectl -n cnpg-system get deploy barman-cloud -o jsonpath='{.spec.replicas}')" -ge 2 ]] || fail "the plugin runs one replica (tools/ha_plugin_manifest.py makes two)"
  place_on "$N4" app=barman-cloud || fail "no plugin replica could be started on $N4"
  kubectl -n cnpg-system rollout status deploy/barman-cloud --timeout=180s >/dev/null
  PLUGIN="; plugin replicas on $(pods_of app=barman-cloud | cut -d= -f2 | tr '\n' ' ')"
fi
echo "   on $N4: the primary $P4 and the operator's leader $(leader); the other operator replica on $(op_pods | grep -v "=$N4$" | cut -d= -f2 | head -1)$PLUGIN"
r="$(await_acks "$(date +%s.%N)")"; [[ "${r%% *}" != open ]] || fail "the writer is not acknowledged before the node loss"
T4="$(date +%s.%N)"; S4=$SECONDS
docker stop "$N4" >/dev/null; STOPPED="$N4"
P5=""; for _ in $(seq 1 120); do P5="$(primary)"; [[ -n "$P5" && "$P5" != "$P4" ]] && break; sleep 2; done
[[ -n "$P5" && "$P5" != "$P4" ]] || fail "no new primary 4 minutes after $N4 stopped"
F4=$((SECONDS - S4)); R4="$(await_acks "$T4")"; limit "${R4%% *}" 120 "node loss"
echo "PASS: $N4 stopped: the operator replica on $(op_pods | grep -v "=$N4$" | cut -d= -f2 | head -1) took the lead ($(leader)) and promoted $P5 on $(node_of "$P5") after $F4 s;" \
     "writes refused for ${R4%% *} s at most, then acknowledged again"
docker start "$N4" >/dev/null; STOPPED=""; S5=$SECONDS
kubectl wait --for=condition=Ready "node/$N4" --timeout=300s >/dev/null
wait_ready 3 420
ROLE="$(kubectl -n "$NS" get pod "$P4" -o jsonpath='{.metadata.labels.cnpg\.io/instanceRole}')"
[[ "$ROLE" == replica && "$(primary)" == "$P5" ]] || fail "after $N4 came back: $P4 is $ROLE, primary $(primary)"
echo "PASS: $N4 back: $P4 rejoined as a standby, 3 ready instances $((SECONDS - S5)) s after the node started ($(placement))"
NA="$(nothing_lost)"
echo "PASS: $NA acknowledged rows over the four steps, none missing"
echo "ALL PASS"
