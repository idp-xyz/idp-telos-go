#!/usr/bin/env bash
# Point-in-time recovery of the HA PostgreSQL (tests/kind/full.sh with PG_HA=1; deploy/kubernetes/ha-postgres/pitr.yaml): a mistake that
# replication copies to every standby (here a DELETE of all rows) is undone by recreating the cluster from the base backup and the
# archived WAL, stopped at the instant before the mistake. Archiving, the base backup and the recovery all go through the Barman Cloud
# plugin. DEV ONLY: one kind node and a RustFS on the host as the object store.
#
#   1  preconditions: continuous archiving is on, a base backup completed
#   2  a table with 50 rows; the instant T is read from the server; a few seconds later 20 more rows, then DELETE of every row
#   3  the WAL up to the end of the mistake is archived (pg_switch_wal, wait for the archiver)
#   4  a new cluster postgresql-restore is bootstrapped from the object store with recoveryTarget.targetTime = T
#   5  the restored database has the 50 rows of before T, none of the 20 after it, and the original cluster has 0 rows (the mistake was real)
#   6  the recovery time is reported; the restored cluster is removed
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
N=databases
log() { echo "== $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
primary() { kubectl -n $N get cluster postgresql -o jsonpath='{.status.currentPrimary}'; }
psql_p() { kubectl -n $N exec -c postgres "$(primary)" -- psql -U postgres -d pitr_drill -qtAX "$@"; }   # the local socket (trust), as the operator does
cleanup() {
  kubectl -n $N delete cluster postgresql-restore --ignore-not-found --wait=false >/dev/null 2>&1 || true
  kubectl -n $N delete networkpolicy postgresql-restore-ha --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "1. preconditions"
[[ "$(kubectl -n $N get cluster postgresql -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}')" == True ]] || fail "continuous archiving is not on"
[[ "$(kubectl -n $N get backups -o jsonpath='{.items[*].status.phase}')" == *completed* ]] || fail "no base backup has completed"
kubectl -n $N delete cluster postgresql-restore --ignore-not-found >/dev/null
kubectl -n $N exec -c postgres "$(primary)" -- psql -U postgres -qtAX -c "DROP DATABASE IF EXISTS pitr_drill" -c "CREATE DATABASE pitr_drill" >/dev/null

log "2. 50 rows, the instant T, 20 more rows, the mistake"
psql_p -c "CREATE TABLE ledger(id int PRIMARY KEY, note text)" -c "INSERT INTO ledger SELECT g, 'before T' FROM generate_series(1,50) g" >/dev/null
sleep 2
T="$(psql_p -c "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US') || '+00'")"
sleep 3
psql_p -c "INSERT INTO ledger SELECT g, 'after T' FROM generate_series(51,70) g" >/dev/null
sleep 1
psql_p -c "DELETE FROM ledger" >/dev/null
[[ "$(psql_p -c 'SELECT count(*) FROM ledger')" == 0 ]] || fail "the mistake did not happen"
echo "   T = $T"

log "3. the WAL of the mistake reaches the object store"
BEFORE="$(psql_p -c "SELECT last_archived_wal FROM pg_stat_archiver" | tr -d '[:space:]')"
CUR="$(psql_p -c 'SELECT pg_walfile_name(pg_switch_wal())' | tr -d '[:space:]')"
for _ in $(seq 1 60); do
  A="$(psql_p -c "SELECT last_archived_wal FROM pg_stat_archiver" | tr -d '[:space:]')"
  [[ -n "$A" && "$A" != "$BEFORE" && ! "$A" < "$CUR" ]] && break; sleep 2
done
[[ ! "$A" < "$CUR" ]] || fail "the segment $CUR was not archived (last: $A)"
echo "   archived up to $A"

log "4. recovery to T into a new cluster"
# the ObjectStore the running cluster archives to (through the Barman Cloud plugin): the new cluster reads the base backup and the WAL
# of the server "postgresql" from it
STORE="$(kubectl -n $N get cluster postgresql -o json | python3 -c '
import json, sys
p = [p for p in json.load(sys.stdin)["spec"].get("plugins", []) if p["name"] == "barman-cloud.cloudnative-pg.io" and p.get("isWALArchiver")]
print(p[0]["parameters"]["barmanObjectName"] if p else "")')"
[[ -n "$STORE" ]] || fail "the cluster does not archive through the Barman Cloud plugin"
python3 - "$STORE" "$T" <<'PY' | kubectl apply -f - >/dev/null
import sys, yaml
print(yaml.safe_dump({
    "apiVersion": "postgresql.cnpg.io/v1", "kind": "Cluster", "metadata": {"name": "postgresql-restore", "namespace": "databases"},
    "spec": {
        "instances": 1, "imageName": "idp-postgres-ha:17-dev", "imagePullPolicy": "IfNotPresent",
        "enableSuperuserAccess": True, "superuserSecret": {"name": "postgres-superuser"},
        "storage": {"size": "5Gi"}, "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "1Gi"}},
        "certificates": {"serverCASecret": "postgres-server-ca", "serverTLSSecret": "postgres-tls"},
        "bootstrap": {"recovery": {"source": "origin", "recoveryTarget": {"targetTime": sys.argv[2]}}},
        "externalClusters": [{"name": "origin", "plugin": {"name": "barman-cloud.cloudnative-pg.io",
                                                           "parameters": {"barmanObjectName": sys.argv[1], "serverName": "postgresql"}}}],
    }}))
PY
# the same policy as the running cluster's (instances, operator, API server, object store), for the new cluster's label
kubectl -n $N get networkpolicy postgresql-ha -o json | python3 -c '
import json, sys
d = json.load(sys.stdin); d["metadata"] = {"name": "postgresql-restore-ha", "namespace": "databases"}
print(json.dumps(d).replace("\"cnpg.io/cluster\": \"postgresql\"", "\"cnpg.io/cluster\": \"postgresql-restore\""))' | kubectl apply -f - >/dev/null
S=$(date +%s)
for _ in $(seq 1 150); do [[ "$(kubectl -n $N get cluster postgresql-restore -o jsonpath='{.status.readyInstances}' 2>/dev/null)" == 1 ]] && break; sleep 4; done
[[ "$(kubectl -n $N get cluster postgresql-restore -o jsonpath='{.status.readyInstances}')" == 1 ]] || { kubectl -n $N get cluster,pods; fail "the restored cluster did not become ready"; }
E=$(date +%s)

log "5. what the restored database holds"
RP="$(kubectl -n $N get cluster postgresql-restore -o jsonpath='{.status.currentPrimary}')"
rq() { kubectl -n $N exec -c postgres "$RP" -- psql -U postgres -d pitr_drill -qtAX -c "$1"; }
BEF="$(rq "SELECT count(*) FROM ledger WHERE note='before T'")"; AFT="$(rq "SELECT count(*) FROM ledger WHERE note='after T'")"
ORIG="$(psql_p -c 'SELECT count(*) FROM ledger')"
echo "   restored: $BEF rows before T, $AFT after T; the original cluster: $ORIG rows"
[[ "$BEF" == 50 ]] || fail "expected the 50 rows from before T, got $BEF"
[[ "$AFT" == 0 ]] || fail "$AFT rows written after T are in the restored database"
[[ "$ORIG" == 0 ]] || fail "the original is not empty: the drill's mistake did not hold"

log "6. recovery time"
echo "   $((E - S)) s from the request to a ready cluster (a 1 MB database; it grows with the base backup and the WAL to replay)"
kubectl -n $N exec -c postgres "$(primary)" -- psql -U postgres -qtAX -c "DROP DATABASE pitr_drill" >/dev/null
echo "ALL PASS: point-in-time recovery to $T: 50 rows back, the 20 written after T and the DELETE not replayed"
