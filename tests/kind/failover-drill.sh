#!/usr/bin/env bash
# The platform on a HA PostgreSQL (tests/kind/full.sh with PG_HA=1): the primary Pod is killed while the CMI is taking writes, and
# what has to hold: the CMI recovers by itself (its Pods do not restart), writes are acknowledged again from the new primary within
# a minute, and no write the CMI acknowledged is missing afterwards. DEV ONLY: one kind node, so the failover is shown, not the
# spreading of the instances over nodes or zones.
#
#   1  a numbered memory record per ~0.3 s through the CMI API (as the HOST writes them: idp-host-svc, cmi-writer), the outcome of every try logged
#   2  the primary Pod of the cluster is killed (--force, no grace)
#   3  wait for the new primary, the CMI's readiness, three ready instances again
#   4  every acknowledged record is in the database of the new primary; bob reads his marker; the CMI Pods did not restart; all client
#      connections are TLS
#
#   BLACKHOLE=1 bash tests/kind/failover-drill.sh   instead of a kill in 2, the primary vanishes without a reset (its packets dropped in the
#      node): what a client sees when the primary's node is lost. A connection to it then hangs rather than fails, so this is where the
#      components' timeouts show
#   NODELOSS=1 KIND_CLUSTER=ha bash tests/kind/failover-drill.sh   on the multi-node cluster (tests/kind/multinode-cluster.sh, then full.sh with
#      KIND_CLUSTER=ha): the primary's node is stopped, with every other Pod of the platform on it; it is started again afterwards
#   ROLLOUT=1 bash tests/kind/failover-drill.sh   instead of a kill in 2, a rolling restart of the cluster (what an image, configuration
#      or plugin change does: every instance recreated, the standbys first, the primary last by its primaryUpdateMethod), and the
#      longest stretch of refused writes during it is what is limited
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
L=.local/kind-full
CA=.local/kind-ca/ca.crt
ISSUER=https://127.0.0.1:17443/realms/idp
[[ -f $L/clients.json ]] || { echo "BLOCKED: no $L/clients.json: run tests/kind/full.sh first" >&2; exit 2; }
log() { echo "== $*"; }
PFPIDS=()
# BLACKHOLE=1: the raw table of the (one) node, before any connection tracking: nothing from or to the address passes
HOLE=""; LOST=""; NODE="${KIND_CLUSTER:-kind}-control-plane"
kubectl() { command kubectl --context "kind-${KIND_CLUSTER:-kind}" "$@"; }   # the cluster of the drill, not the current context
blackhole() { for r in "PREROUTING -s" "PREROUTING -d" "OUTPUT -d"; do docker exec "$NODE" iptables -t raw "$1" ${r% *} ${r#* } "$HOLE" -j DROP 2>/dev/null || true; done; }
cleanup() {
  [[ -z "$HOLE" ]] || blackhole -D
  [[ -z "$LOST" ]] || docker start "$LOST" >/dev/null 2>&1 || true
  for p in "${PFPIDS[@]:-}"; do [[ -n "$p" ]] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; } || true; done
  [[ -z "${PROBE:-}" ]] || kubectl -n databases delete pod pg-newconn-probe --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT
# a port-forward that is there when we need it (the platform's own forwards may or may not be running)
ensure_forward() { # ensure_forward NAMESPACE SERVICE LOCAL:REMOTE
  local port="${3%%:*}"
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    ( while true; do kubectl -n "$1" port-forward "svc/$2" "$3" >/dev/null 2>&1 || true; sleep 1; done ) & PFPIDS+=($!)
    for _ in $(seq 1 360); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && return 0; sleep 0.5; done   # after a node restart the API server is away for a minute
    echo "BLOCKED: no forward to $2" >&2; exit 2
  fi
}
secret_of() { python3 -c "import json,sys; d=json.load(open('$L/clients.json')); print(d['users'].get(sys.argv[1]) or d[sys.argv[1]])" "$1"; }
kc() { curl -sf --noproxy '*' --cacert "$CA" "$ISSUER/protocol/openid-connect/token" "$@" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
bob_token() { kc -d grant_type=password -d client_id=e2e-cli -d "client_secret=$(secret_of e2e-cli)" -d username=bob -d "password=$(secret_of bob)"; }
host_token() { kc -d grant_type=client_credentials -d client_id=idp-host-svc -d "client_secret=$(secret_of idp-host-svc)"; }
CMI=http://127.0.0.1:17550
cmi() { # cmi WANT METHOD PATH TOKEN [BODY]
  local out status; out="$(mktemp)"
  status="$(curl -s --noproxy '*' -o "$out" -w '%{http_code}' -X "$2" "$CMI$3" -H "Authorization: Bearer $4" -H 'Content-Type: application/json' ${5:+--data "$5"})"
  [[ "$status" == "$1" ]] || { echo "FAIL: $2 $3 -> $status (want $1): $(cat "$out")" >&2; rm -f "$out"; exit 1; }
  cat "$out"; rm -f "$out"
}
psql_cmi() { kubectl -n databases exec -c postgres deploy/postgresql -- psql -U postgres -d "${2:-idp_cmi}" -tAc "$1"; }
wait_pg() { kubectl -n databases rollout status deploy/postgresql --timeout=300s >/dev/null; for _ in $(seq 1 120); do kubectl -n databases exec -c postgres deploy/postgresql -- pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && return 0; sleep 1; done; echo "FAIL: PostgreSQL did not come back" >&2; exit 1; }
wait_cmi() { # right after a node restart the Deployment still reports the old readiness: ask the CMI itself, with a time limit on every try
  for _ in $(seq 1 180); do
    [[ "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -m 3 "$CMI/readyz")" == 200 ]] && [[ "$(kubectl -n idp-cmi get deploy idp-cmi -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" == "$(kubectl -n idp-cmi get deploy idp-cmi -o jsonpath='{.spec.replicas}' 2>/dev/null)" ]] && return 0
    sleep 1
  done
  echo "FAIL: the CMI did not become ready" >&2; exit 1
}

kubectl -n databases get cluster postgresql >/dev/null 2>&1 || { echo "BLOCKED: no PostgreSQL cluster: run PG_HA=1 bash tests/kind/full.sh" >&2; exit 2; }
primary() { kubectl -n databases get cluster postgresql -o jsonpath='{.status.currentPrimary}'; }
psql_cmi() { kubectl -n databases exec -c postgres "pod/$(primary)" -- psql -U postgres -d "${2:-idp_cmi}" -tAc "$1"; }
CMI=http://127.0.0.1:17550
ensure_forward idp-cmi idp-cmi 17550:8550
ensure_forward keycloak keycloak 17443:7443
BOB_ID="$(python3 -c "import json; print(json.load(open('$L/clients.json'))['ids']['bob'])")"
RESTARTS_BEFORE="$(kubectl -n idp-cmi get pods -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}')"

log "1 a writer through the CMI"
RUN="$(date +%s)"; WLOG="$L/failover-writer.log"; : > "$WLOG"
NOW0="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
(
  TOKEN="$(host_token)"; n=0; started=$SECONDS; NOW=""
  while [[ -f "$WLOG.run" ]]; do
    [[ -z "$NOW" ]] && { n=$((n+1)); NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; }   # a retry resends the SAME request (same key, same body): that is what makes it idempotent
    [[ $((SECONDS - started)) -gt 100 ]] && TOKEN="$(host_token)" && started=$SECONDS   # tokens last minutes
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 4 --noproxy '*' -X POST "$CMI/v1/memory/records" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H "Idempotency-Key: failover-$RUN-$n" \
      --data "{\"scope\":{\"kind\":\"subject\",\"id\":\"$BOB_ID\"},\"kind\":\"episodic\",\"content\":\"failover marker $RUN-$n\",\"tags\":[\"failover\"],\"provenance\":{\"source\":\"idp-host\",\"ref\":\"failover:$RUN:$n\",\"observedAt\":\"$NOW\",\"recordedBy\":\"idp-host\"},\"sensitivity\":\"internal\"}" 2>/dev/null || echo 000)"
    if [[ "$code" == 201 || "$code" == 200 ]]; then echo "ack $n $(date +%s.%N)" >> "$WLOG"; NOW=""; else echo "fail $n $(date +%s.%N) $code" >> "$WLOG"; fi
    sleep 0.2
  done
) &
touch "$WLOG.run"; WPID=$!; PFPIDS+=($WPID)
for _ in $(seq 1 90); do [[ "$(grep -c '^ack' "$WLOG")" -ge 15 ]] && break; sleep 1; done
[[ "$(grep -c '^ack' "$WLOG")" -ge 15 ]] || { cat "$WLOG" | tail -5; echo "FAIL: the CMI does not acknowledge writes" >&2; exit 1; }
echo "PASS: the CMI acknowledges writes ($(grep -c '^ack' "$WLOG") so far) on primary $(primary)"

if [[ "${ROLLOUT:-}" == 1 ]]; then
  # the CMI's writes reuse pooled connections; a starting Pod, a Job or a growing pool opens new ones. A Pod with the backup Jobs'
  # label (the policies let it reach PostgreSQL) opens a new verify-full connection to the Service postgresql every 0.5 s
  kubectl -n databases delete pod pg-newconn-probe --ignore-not-found --wait=true >/dev/null
  kubectl apply -f - >/dev/null <<'EOF'
apiVersion: v1
kind: Pod
metadata: {name: pg-newconn-probe, namespace: databases, labels: {app.kubernetes.io/name: pg-backup}}
spec:
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 70, runAsGroup: 70, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: probe
      image: public.ecr.aws/docker/library/postgres:17-alpine
      imagePullPolicy: IfNotPresent
      securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: [ALL]}}
      env: [{name: PGPASSWORD, valueFrom: {secretKeyRef: {name: postgres-superuser, key: password}}}]
      command: [sh, -c, 'while true; do if out="$(psql "host=postgresql.databases.svc.cluster.local dbname=postgres user=postgres sslmode=verify-full sslrootcert=/ca/ca.crt connect_timeout=2" -tAXc "select 1" 2>&1)"; then echo ok; else echo "fail $(echo "$out" | head -1)"; fi; sleep 0.5; done']
      volumeMounts: [{name: ca, mountPath: /ca, readOnly: true}]
  volumes: [{name: ca, configMap: {name: pg-ca}}]
EOF
  PROBE=1
  kubectl -n databases wait --for=condition=Ready pod/pg-newconn-probe --timeout=120s >/dev/null
  for _ in $(seq 1 30); do [[ "$(kubectl -n databases logs pg-newconn-probe 2>/dev/null | grep -c '^ok')" -ge 3 ]] && break; sleep 1; done
  [[ "$(kubectl -n databases logs pg-newconn-probe | grep -c '^ok')" -ge 3 ]] || { kubectl -n databases logs pg-newconn-probe | tail -3; echo "FAIL: the probe cannot open a connection" >&2; exit 1; }
  log "2 a rolling restart of the cluster (primaryUpdateMethod $(kubectl -n databases get cluster postgresql -o jsonpath='{.spec.primaryUpdateMethod}'))"
  OLD="$(primary)"; START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; T0=$SECONDS
  kubectl -n databases annotate cluster postgresql kubectl.kubernetes.io/restartedAt="$START" --overwrite >/dev/null   # as kubectl cnpg restart does
  done_=""
  for _ in $(seq 1 300); do
    fresh="$(kubectl -n databases get pods -l cnpg.io/cluster=postgresql,cnpg.io/podRole=instance -o json | python3 -c '
import json, sys
print(sum(p["metadata"]["creationTimestamp"] >= sys.argv[1] and all(c.get("ready") for c in p["status"].get("containerStatuses", []))
          for p in json.load(sys.stdin)["items"]))' "$START")"
    [[ "$fresh" == 3 && "$(kubectl -n databases get cluster postgresql -o jsonpath='{.status.phase}')" == "Cluster in healthy state" ]] && { done_=1; break; }
    sleep 2
  done
  [[ -n "$done_" ]] || { kubectl -n databases get cluster,pods; echo "FAIL: the rolling restart did not complete in 10 minutes" >&2; exit 1; }
  sleep 5   # a few writes after the last instance is back
  OUT="$(python3 - "$WLOG" <<'PY'
import sys
rows = sorted(([r[0], float(r[2])] for r in (l.split() for l in open(sys.argv[1]) if l.strip())), key=lambda r: r[1])
longest, first_fail = 0.0, None
for kind, t in rows:
    if kind == "fail" and first_fail is None: first_fail = t
    if kind == "ack" and first_fail is not None: longest, first_fail = max(longest, t - first_fail), None
print(round(longest, 1) if first_fail is None else "open")
PY
)"
  [[ "$OUT" != open ]] || { tail -8 "$WLOG"; echo "FAIL: the CMI never acknowledged a write after the rolling restart" >&2; exit 1; }
  kubectl -n databases logs pg-newconn-probe --timestamps > "$L/newconn-probe.log"
  NEWC="$(python3 - "$L/newconn-probe.log" "$START" <<'PY'
import sys, datetime as dt
def t(s): return dt.datetime.fromisoformat(s[:26].rstrip("Z") + "+00:00").timestamp()
start = t(sys.argv[2].replace("Z", ".000000"))
rows = [(l.split()[1], t(l.split()[0])) for l in open(sys.argv[1]) if len(l.split()) > 1]
longest, first_fail, fails, reasons = 0.0, None, 0, {}
for kind, ts in rows:
    if ts < start: continue
    if kind == "fail":
        fails += 1
        if first_fail is None: first_fail = ts
    if kind == "ok" and first_fail is not None: longest, first_fail = max(longest, ts - first_fail), None
print("open" if first_fail is not None else f"{round(longest, 1)} {fails}")
PY
)"
  [[ "$NEWC" != open ]] || { tail -5 "$L/newconn-probe.log"; echo "FAIL: no new connection succeeds after the rolling restart" >&2; exit 1; }
  echo "   the CMI's longest stretch of refused writes: $OUT s; new connections: ${NEWC#* } refused, the longest stretch ${NEWC%% *} s ($(grep fail "$L/newconn-probe.log" | sed 's/^[^ ]* fail //' | cut -c1-70 | sort | uniq -c | sort -rn | head -2 | tr -s ' ' | paste -sd ';'))"
  (( $(python3 -c "print(int(float($OUT) > 90))") == 0 )) || { echo "FAIL: writes were refused for $OUT s in a row during the rolling restart (limit 90)" >&2; exit 1; }
  (( $(python3 -c "print(int(float('${NEWC%% *}') > 60))") == 0 )) || { echo "FAIL: new connections were refused for ${NEWC%% *} s in a row during the rolling restart (limit 60)" >&2; exit 1; }
  echo "PASS: rolling restart of the 3 instances in $((SECONDS - T0)) s, primary $OLD -> $(primary); the longest stretch of refused writes was $OUT s, of refused new connections ${NEWC%% *} s"
else
OLD="$(primary)"
if [[ "${BLACKHOLE:-}" == 1 ]]; then
  log "2 the primary vanishes: every packet from and to its Pod is dropped (as when its node loses power: no reset comes back)"
  HOLE="$(kubectl -n databases get pod "$OLD" -o jsonpath='{.status.podIP}')"; KILL_AT="$(date +%s.%N)"; blackhole -I
elif [[ "${NODELOSS:-}" == 1 ]]; then
  LOST="$(kubectl -n databases get pod "$OLD" -o jsonpath='{.spec.nodeName}')"
  log "2 the primary's node $LOST is lost (docker stop), with everything else of the platform that runs on it:"
  kubectl get pods -A --field-selector="spec.nodeName=$LOST,status.phase=Running" -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' \
    | grep -v -E '^(kube-system|local-path-storage)/' | sed -E 's/-[a-z0-9]{8,10}-[a-z0-9]{5}$//' | sort -u | paste -sd' ' | sed 's/^/   /'
  KILL_AT="$(date +%s.%N)"; docker stop "$LOST" >/dev/null
else
  log "2 the primary Pod is killed"
  KILL_AT="$(date +%s.%N)"
  kubectl -n databases delete pod "$OLD" --grace-period=0 --force >/dev/null 2>&1
fi
NEW=""; for _ in $(seq 1 240); do NEW="$(primary)"; [[ -n "$NEW" && "$NEW" != "$OLD" ]] && break; sleep 0.5; done
[[ -n "$NEW" && "$NEW" != "$OLD" ]] || { echo "FAIL: no new primary" >&2; exit 1; }
OUT=""
for _ in $(seq 1 240); do
  OUT="$(python3 - "$WLOG" "$KILL_AT" <<'PY'
import sys
rows = [l.split() for l in open(sys.argv[1]) if l.strip()]
kill = float(sys.argv[2])
fails = [float(r[2]) for r in rows if r[0] == "fail" and float(r[2]) >= kill]
if not fails: print("0.0"); sys.exit()
acks = [float(r[2]) for r in rows if r[0] == "ack" and float(r[2]) > max(fails)]
print(round(acks[0] - kill, 1) if acks else "")
PY
)"
  [[ -n "$OUT" ]] && break; sleep 1
done
[[ -n "$OUT" ]] || { tail -8 "$WLOG"; echo "FAIL: the CMI never acknowledged a write after the failover" >&2; exit 1; }
# a killed Pod is an event the operator sees at once (25-29 s measured); a primary that vanishes has to fail its checks first
# (55-87 s measured), so that case gets more room
LIMIT=90; [[ "${BLACKHOLE:-}" == 1 || "${NODELOSS:-}" == 1 ]] && LIMIT=120
(( $(python3 -c "print(int(float($OUT) > $LIMIT))") == 0 )) || { echo "FAIL: writes were refused for $OUT s (limit $LIMIT)" >&2; exit 1; }
echo "PASS: $OLD killed, $NEW promoted; the CMI refused writes for $OUT s, then acknowledged them again, with no restart"
fi

[[ -z "$HOLE" ]] || { blackhole -D; HOLE=""; }   # the old primary is reachable again and has to come back as a standby
if [[ -n "$LOST" ]]; then docker start "$LOST" >/dev/null; kubectl wait --for=condition=Ready "node/$LOST" --timeout=300s >/dev/null; WASLOST="$LOST"; LOST=""; fi
log "3 the cluster is whole again; nothing acknowledged is missing"
sleep 3; rm -f "$WLOG.run"; sleep 1; wait "$WPID" 2>/dev/null || true
for _ in $(seq 1 180); do [[ "$(kubectl -n databases get cluster postgresql -o jsonpath='{.status.readyInstances}')" == 3 ]] && break; sleep 2; done
[[ "$(kubectl -n databases get cluster postgresql -o jsonpath='{.status.readyInstances}')" == 3 ]] || { kubectl -n databases get cluster,pods; echo "FAIL: not back to 3 ready instances" >&2; exit 1; }
ACKED="$(awk '$1=="ack"{print $2}' "$WLOG" | LC_ALL=C sort -u)"
PRESENT="$(psql_cmi "SELECT substring(content from 'failover marker [0-9]+-([0-9]+)') FROM memory_records WHERE content LIKE 'failover marker $RUN-%'" | LC_ALL=C sort -u)"
LOST="$(LC_ALL=C comm -23 <(echo "$ACKED") <(echo "$PRESENT") | wc -l)"
NACK="$(echo "$ACKED" | wc -l)"; NFAIL="$(grep -c '^fail' "$WLOG" || true)"
[[ "$LOST" == 0 ]] || { echo "FAIL: $LOST acknowledged records are missing after the failover" >&2; exit 1; }
echo "PASS: $NACK records were acknowledged (and $NFAIL tries refused during the switch): none is missing on the new primary $(primary)"
RESTARTS_AFTER="$(kubectl -n idp-cmi get pods -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}')"
[[ "$RESTARTS_BEFORE" == "$RESTARTS_AFTER" || -n "${WASLOST:-}" ]] || { echo "FAIL: CMI restarts $RESTARTS_BEFORE -> $RESTARTS_AFTER" >&2; exit 1; }   # a Pod on the lost node restarts with it
echo "PASS: the CMI Pods did not restart (restart counts $RESTARTS_AFTER)"
BAD="$(psql_cmi "SELECT count(*) FROM pg_stat_ssl s JOIN pg_stat_activity a USING (pid) WHERE a.client_addr IS NOT NULL AND NOT s.ssl AND a.backend_type='client backend'" postgres)"
[[ "$BAD" == 0 ]] || { echo "FAIL: $BAD client connections without TLS" >&2; exit 1; }
cmi 200 GET "/v1/memory/records?limit=5" "$(bob_token)" >/dev/null && echo "PASS: bob reads his memory through the CMI on the new primary; every client connection is TLS"
echo "ALL PASS"
