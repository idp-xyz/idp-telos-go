#!/usr/bin/env bash
# The HA PostgreSQL (deploy/kubernetes/ha-postgres) on a kind cluster, on its own: a primary and two standbys, TLS with verify-full, a
# writer that inserts numbered rows through the read-write Service while the primary Pod is killed, and what has to hold afterwards: a
# new primary within seconds, no acknowledged write lost, the old primary back as a standby. On one node the failover is shown; on the
# multi-node cluster (tests/kind/multinode-cluster.sh) the spreading over nodes and zones too.
#
#   bash tests/kind/ha-postgres.sh            install the operator, create the cluster, run the drill, leave it
#   KIND_CLUSTER=ha ARCHIVE=1 ...             as production runs it: the operators of tests/kind/ha-operators.sh (CloudNativePG and the
#                                             Barman Cloud plugin with two replicas each, cert-manager) and WAL archiving to an S3 store
#                                             on the host (tests/kind/s3-target.sh: idp-kind-$KIND_CLUSTER-s3 on <gateway>:19100); then
#                                             tests/kind/ha-maintenance.sh drains and loses nodes under it
#   TEARDOWN=1 ...                            delete the test namespace at the end (and the operator's policy for it, below)
#   KIND_CLUSTER=kind                         the cluster (kubectl context kind-$KIND_CLUSTER)
# It runs next to the platform's cluster too (after PG_HA=1 tests/kind/full.sh; not with ARCHIVE, the plugin there is the platform's):
# the operator's namespace is default-deny then, and the test adds a NetworkPolicy operator-to-ha-test there so that the operator
# reaches this cluster's instances.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
CLUSTER="${KIND_CLUSTER:-kind}"; NS=ha-test; L=.local/ha-postgres; ARCHIVE="${ARCHIVE:-}"
OPERATOR_IMG=ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1
OPERAND_BASE=ghcr.io/cloudnative-pg/postgresql:17.11-standard-trixie
IMG=idp-postgres-ha:17-dev
MANIFEST_URL=https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.1/cnpg-1.30.1.yaml
MANIFEST_SHA256=37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb
log() { echo "== $*"; }
kubectl() { command kubectl --context "kind-$CLUSTER" "$@"; }   # not the global context: the platform's drills use it
mkdir -p "$L"; umask 077

log "the PostgreSQL image with pgvector"
load() { local tar; tar="$(mktemp --suffix=.tar)"; docker save --platform linux/amd64 "$1" -o "$tar" 2>/dev/null || docker save "$1" -o "$tar"; kind load image-archive "$tar" --name "$CLUSTER" >/dev/null; rm -f "$tar"; }
docker image inspect "$OPERAND_BASE" >/dev/null 2>&1 || { echo "BLOCKED: $OPERAND_BASE is not in the Docker daemon (crane pull --platform linux/amd64 $OPERAND_BASE x.tar && docker load -i x.tar)" >&2; exit 2; }
bash deploy/images/postgres-ha/build.sh "$IMG" >/dev/null   # cached: quick when the Dockerfile has not changed
load "$IMG"   # into every node, always: a node may hold an older build under the same tag

log "a restricted namespace"   # first: the plugin's RoleBinding (ARCHIVE) lives in it
kubectl delete ns "$NS" --ignore-not-found --wait=true --timeout=300s >/dev/null
kubectl create ns "$NS" >/dev/null
kubectl label ns "$NS" pod-security.kubernetes.io/enforce=restricted --overwrite >/dev/null

if [[ -n "$ARCHIVE" ]]; then
  log "the operators as production runs them (tests/kind/ha-operators.sh)"
  W="$(kubectl -n cnpg-system get deploy barman-cloud -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="WATCH_NAMESPACE")].value}' 2>/dev/null || true)"
  [[ -z "$W" || "$W" == "$NS" ]] || { echo "BLOCKED: the plugin on kind-$CLUSTER serves $W: ARCHIVE=1 would take it away (use the multi-node cluster, KIND_CLUSTER=ha)" >&2; exit 2; }
  KIND_CLUSTER="$CLUSTER" bash tests/kind/ha-operators.sh "$NS"
else
  log "the operator (the release manifest, checked against its pinned checksum)"
  docker image inspect "$OPERATOR_IMG" >/dev/null 2>&1 || { echo "BLOCKED: $OPERATOR_IMG is not in the Docker daemon (crane pull --platform linux/amd64 $OPERATOR_IMG x.tar && docker load -i x.tar)" >&2; exit 2; }
  load "$OPERATOR_IMG"   # into every node (on several nodes the operator may run on any of them)
  [[ -f "$L/cnpg.yaml" ]] || curl -sSL --retry 5 --retry-all-errors -m 180 -o "$L/cnpg.yaml" "$MANIFEST_URL"
  [[ "$(sha256sum "$L/cnpg.yaml" | cut -d' ' -f1)" == "$MANIFEST_SHA256" ]] || { echo "FAIL: the operator manifest does not match its pinned checksum" >&2; rm -f "$L/cnpg.yaml"; exit 1; }
  kubectl apply --server-side --force-conflicts -f "$L/cnpg.yaml" >/dev/null   # the pull-policy patch below is ours
  # the release manifest says imagePullPolicy: Always; the images were loaded into the node (a kind node has no route to the registry)
  kubectl -n cnpg-system patch deploy cnpg-controller-manager --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]' >/dev/null
  kubectl -n cnpg-system patch deploy cnpg-controller-manager --patch-file deploy/kubernetes/ha-postgres/operator-ha.yaml >/dev/null   # two replicas, a leader
  kubectl -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=300s >/dev/null
  echo "PASS: CloudNativePG 1.30.1 runs in cnpg-system"
fi
# after PG_HA=1 tests/kind/full.sh the operator's namespace is default-deny (deploy/kubernetes/ha-postgres/platform.yaml) and the
# operator reaches the instances in databases only: this test's cluster gets the same rule for its namespace. Only then: on a cluster
# without those policies, an egress policy that selects the operator would cut everything else it reaches (the API server)
if kubectl -n cnpg-system get networkpolicy operator >/dev/null 2>&1; then
  kubectl apply -f - >/dev/null <<YAML
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: operator-to-$NS, namespace: cnpg-system, labels: {app.kubernetes.io/part-of: tests-kind-ha-postgres}}
spec:
  podSelector: {matchLabels: {app.kubernetes.io/name: cloudnative-pg}}
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: $NS}}
          podSelector: {matchExpressions: [{key: cnpg.io/cluster, operator: Exists}]}
      ports: [{protocol: TCP, port: 5432}, {protocol: TCP, port: 8000}]
YAML
  echo "PASS: cnpg-system is default-deny; the operator may reach the instances in $NS too (NetworkPolicy operator-to-$NS)"
fi

log "the secrets (test CA) and the cluster"
mkdir -p .local/kind-ca
[[ -f .local/kind-ca/ca.crt && -f .local/kind-ca/ca.key ]] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=IDP TELOS platform test CA" -keyout .local/kind-ca/ca.key -out .local/kind-ca/ca.crt 2>/dev/null
SAN=""; for s in postgresql-rw postgresql-ro postgresql-r; do SAN+="DNS:$s,DNS:$s.$NS,DNS:$s.$NS.svc,DNS:$s.$NS.svc.cluster.local,"; done
openssl req -newkey rsa:2048 -nodes -subj "/CN=postgresql-rw.$NS.svc.cluster.local" -keyout "$L/server.key" -out "$L/server.csr" 2>/dev/null
printf 'subjectAltName=%sDNS:localhost\nextendedKeyUsage=serverAuth\n' "$SAN" > "$L/san.ext"
openssl x509 -req -in "$L/server.csr" -CA .local/kind-ca/ca.crt -CAkey .local/kind-ca/ca.key -CAcreateserial -days 30 -extfile "$L/san.ext" -out "$L/server.crt" 2>/dev/null
PGPASS="$(openssl rand -hex 16)"
kubectl -n "$NS" create secret generic postgres-superuser --type=kubernetes.io/basic-auth --from-literal=username=postgres --from-literal=password="$PGPASS" >/dev/null
kubectl -n "$NS" create secret generic postgres-server-ca --from-file=ca.crt=.local/kind-ca/ca.crt >/dev/null
kubectl -n "$NS" create secret tls postgres-tls --cert="$L/server.crt" --key="$L/server.key" >/dev/null
for s in postgres-superuser postgres-server-ca postgres-tls; do kubectl -n "$NS" label secret "$s" cnpg.io/reload=true >/dev/null; done
MERGE=()
if [[ -n "$ARCHIVE" ]]; then   # WAL archiving and a base backup through the plugin, as deploy/kubernetes/ha-postgres/pitr.yaml says
  GW="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1)"
  AK="ha$(openssl rand -hex 4)"; SK="$(openssl rand -hex 16)"
  S3_ACCESS_KEY="$AK" S3_SECRET_KEY="$SK" bash tests/kind/s3-target.sh "idp-kind-$CLUSTER-s3" "$GW" 19100
  kubectl -n "$NS" create secret generic pitr-s3 --from-literal=ACCESS_KEY_ID="$AK" --from-literal=ACCESS_SECRET_KEY="$SK" >/dev/null
  python3 - "$GW:19100" "$NS" deploy/kubernetes/ha-postgres/pitr.yaml "$L" <<'PY'
import sys, yaml
by = {d.get("kind", "patch"): d for d in yaml.safe_load_all(open(sys.argv[3]).read().replace("203.0.113.20:19000", sys.argv[1])) if d}
for kind in ("ObjectStore", "ScheduledBackup"):
    by[kind]["metadata"]["namespace"] = sys.argv[2]
    open(f"{sys.argv[4]}/pitr-{kind}.yaml", "w").write(yaml.safe_dump(by[kind]))
PY
  kubectl apply -f "$L/pitr-ObjectStore.yaml" >/dev/null
  MERGE=(--merge deploy/kubernetes/ha-postgres/pitr.yaml)
fi
# cluster.yaml as production has it on three or more schedulable nodes in zones (tests/kind/multinode-cluster.sh); on fewer (the one-node
# cluster) tools/ha_render.py relaxes the spreading and says so
kubectl get nodes -o json | python3 -I tools/ha_render.py --namespace "$NS" "${MERGE[@]}" --nodes - 2>"$L/render.note" | kubectl apply -f - >/dev/null
SPREAD=1; [[ -s "$L/render.note" ]] && { SPREAD=""; sed 's/^/   /' "$L/render.note"; }
[[ -z "$ARCHIVE" ]] || kubectl apply -f "$L/pitr-ScheduledBackup.yaml" >/dev/null
for _ in $(seq 1 180); do
  [[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.readyInstances}' 2>/dev/null)" == 3 ]] && break; sleep 3
done
[[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.readyInstances}')" == 3 ]] || { kubectl -n "$NS" get cluster,pods; kubectl -n "$NS" describe cluster postgresql | tail -20; echo "FAIL: the cluster did not reach 3 ready instances" >&2; exit 1; }
PRIMARY="$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.currentPrimary}')"
kubectl -n "$NS" get pods -l cnpg.io/cluster=postgresql,cnpg.io/podRole=instance -o json > "$L/pods.json"; kubectl get nodes -o json > "$L/nodes.json"
PLACED="$(python3 - "$L/pods.json" "$L/nodes.json" <<'PY'
import json, sys
zones = {n["metadata"]["name"]: n["metadata"]["labels"].get("topology.kubernetes.io/zone", "-") for n in json.load(open(sys.argv[2]))["items"]}
print(" ".join(f'{p["metadata"]["name"]}@{p["spec"]["nodeName"]}/{zones.get(p["spec"]["nodeName"], "-")}' for p in json.load(open(sys.argv[1]))["items"]))
PY
)"
echo "PASS: 3 instances ready, primary $PRIMARY ($PLACED)"
if [[ -n "$SPREAD" ]]; then
  NODES="$(echo "$PLACED" | tr ' ' '\n' | cut -d@ -f2 | cut -d/ -f1 | sort -u | wc -l)"; ZONES="$(echo "$PLACED" | tr ' ' '\n' | cut -d/ -f2 | grep -v '^-$' | sort -u | wc -l)"
  [[ "$NODES" == 3 && "$ZONES" == 3 ]] || { echo "FAIL: the instances are on $NODES node(s) in $ZONES zone(s), not 3 and 3: $PLACED" >&2; exit 1; }
  echo "PASS: the instances are on 3 nodes in 3 zones (anti-affinity required on the node, spread over the zones)"
fi
if [[ -n "$ARCHIVE" ]]; then
  for _ in $(seq 1 60); do
    [[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}')" == True \
       && "$(kubectl -n "$NS" get backups -o jsonpath='{.items[*].status.phase}')" == *completed* ]] && break; sleep 3
  done
  [[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}')" == True ]] || { kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.conditions}'; echo "FAIL: the WAL is not archived" >&2; exit 1; }
  [[ "$(kubectl -n "$NS" get backups -o jsonpath='{.items[*].status.phase}')" == *completed* ]] || { kubectl -n "$NS" get backups; echo "FAIL: no base backup completed" >&2; exit 1; }
  echo "PASS: the WAL is archived and a base backup completed through the Barman Cloud plugin ($(kubectl -n cnpg-system get pods -l app=barman-cloud -o jsonpath='{range .items[*]}{.spec.nodeName} {end}'))"
fi

psql_primary() { kubectl -n "$NS" exec -c postgres "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.currentPrimary}')" -- psql -U postgres -d "${2:-postgres}" -tAc "$1"; }
log "pgvector, synchronous replication, TLS"
psql_primary "CREATE DATABASE ha_test" >/dev/null
[[ "$(psql_primary "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector'" ha_test | tail -1)" =~ ^0\.8 ]] || { echo "FAIL: pgvector is not available in the image" >&2; exit 1; }
SYNC="$(psql_primary "SELECT count(*) FROM pg_stat_replication WHERE sync_state IN ('sync','quorum')")"
[[ "$SYNC" -ge 1 ]] || { psql_primary "SELECT application_name, state, sync_state FROM pg_stat_replication"; echo "FAIL: no synchronous standby" >&2; exit 1; }
psql_primary "CREATE TABLE ha_log (n bigint PRIMARY KEY, at timestamptz DEFAULT now())" ha_test >/dev/null
echo "PASS: pgvector $(psql_primary "SELECT extversion FROM pg_extension WHERE extname='vector'" ha_test), $SYNC synchronous standby, ssl on: $(psql_primary 'SHOW ssl')"

log "a writer through the read-write Service (verify-full), inserting numbered rows"
kubectl -n "$NS" delete pod writer --ignore-not-found --wait=true >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: writer, namespace: $NS}
spec:
  restartPolicy: Never
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
          # every try ends within 5 s: across nodes a connection to a killed primary hangs instead of failing (no reset comes back);
          # a retry resends the same row, and ON CONFLICT counts it when the timed-out try had committed
          n=0
          while true; do
            n=\$((n+1))
            if timeout 5 psql -qAt -v ON_ERROR_STOP=1 -c "INSERT INTO ha_log(n) VALUES (\$n) ON CONFLICT DO NOTHING" >/dev/null 2>&1; then echo "ack \$n \$(date +%s.%N)"; else echo "fail \$n \$(date +%s.%N)"; n=\$((n-1)); fi
            sleep 0.1
          done
  volumes: [{name: ca, secret: {secretName: postgres-server-ca}}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/writer --timeout=120s >/dev/null
for _ in $(seq 1 60); do [[ "$(kubectl -n "$NS" logs writer 2>/dev/null | grep -c '^ack')" -ge 20 ]] && break; sleep 1; done
[[ "$(kubectl -n "$NS" logs writer | grep -c '^ack')" -ge 20 ]] || { kubectl -n "$NS" logs writer | tail -5; echo "FAIL: the writer does not get acknowledgements" >&2; exit 1; }
echo "PASS: the writer inserts through $PRIMARY over verify-full TLS ($(kubectl -n "$NS" logs writer | grep -c '^ack') acknowledged so far)"

log "the primary Pod is killed"
OLD="$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.currentPrimary}')"
KILL_AT="$(date +%s.%N)"
kubectl -n "$NS" delete pod "$OLD" --grace-period=0 --force >/dev/null 2>&1
NEW=""
for _ in $(seq 1 120); do
  NEW="$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.currentPrimary}')"
  [[ -n "$NEW" && "$NEW" != "$OLD" ]] && break; sleep 0.5
done
[[ -n "$NEW" && "$NEW" != "$OLD" ]] || { kubectl -n "$NS" get cluster,pods; echo "FAIL: no new primary after the kill" >&2; exit 1; }
# the outage the client saw: from the kill to the first acknowledgement after the LAST failed attempt (the old primary can still
# answer for a moment after its Pod object is gone, so "the first acknowledgement after the kill" would be the old server). Only once the
# writer has been acknowledged five times after the kill: a writer that is stuck has no failures either, which is not 0 s
RTO=""
for _ in $(seq 1 240); do
  RTO="$(kubectl -n "$NS" logs writer | python3 -c "
import sys
kill=float('$KILL_AT'); after=[(r[0], float(r[2])) for r in (l.split() for l in sys.stdin if l.strip()) if float(r[2])>=kill]
fails=[t for k, t in after if k=='fail']
acks=[t for k, t in after if k=='ack' and (not fails or t>max(fails))]
print('' if len(acks) < 5 else round(acks[0]-kill, 1) if fails else '0.0')")"
  [[ -n "$RTO" ]] && break; sleep 1
done
[[ -n "$RTO" ]] || { kubectl -n "$NS" logs writer | tail -8; echo "FAIL: the writer never got an acknowledgement after the failover" >&2; exit 1; }
echo "PASS: primary $OLD killed, $NEW promoted; writes were refused for $RTO s, then acknowledged again by the new primary"
(( $(python3 -c "print(int(float($RTO) > 60))") == 0 )) || { echo "FAIL: the failover took $RTO s (limit 60)" >&2; exit 1; }

log "nothing acknowledged is lost; the old primary comes back as a standby"
sleep 3
kubectl -n "$NS" logs writer > "$L/writer.log"
kubectl -n "$NS" delete pod writer --wait=true >/dev/null 2>&1 || true
for _ in $(seq 1 180); do [[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.readyInstances}')" == 3 ]] && break; sleep 2; done
[[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.readyInstances}')" == 3 ]] || { kubectl -n "$NS" get cluster,pods; echo "FAIL: the cluster did not return to 3 ready instances" >&2; exit 1; }
ACKED="$(awk '$1=="ack"{print $2}' "$L/writer.log" | sort -n | uniq)"
PRESENT="$(psql_primary "SELECT n FROM ha_log ORDER BY n" ha_test)"
LOST="$(LC_ALL=C comm -23 <(echo "$ACKED" | LC_ALL=C sort) <(echo "$PRESENT" | LC_ALL=C sort) | wc -l)"
NACK="$(echo "$ACKED" | wc -l)"; NFAIL="$(grep -c '^fail' "$L/writer.log" || true)"
[[ "$LOST" == 0 ]] || { echo "FAIL: $LOST acknowledged writes are missing after the failover" >&2; exit 1; }
echo "PASS: $NACK writes were acknowledged (and $NFAIL attempts failed during the switch): none is missing on the new primary"
ROLES="$(kubectl -n "$NS" get pods -l cnpg.io/cluster=postgresql -L cnpg.io/instanceRole --no-headers | awk '{print $1":"$NF}' | tr '\n' ' ')"
[[ "$(kubectl -n "$NS" get cluster postgresql -o jsonpath='{.status.currentPrimary}')" == "$NEW" ]] && echo "$ROLES" | grep -q "$OLD:replica" || { echo "FAIL: roles after the failover: $ROLES" >&2; exit 1; }
echo "PASS: the old primary $OLD rejoined as a replica ($ROLES)"

log "TLS: every client connection is encrypted; a cleartext one is refused"
BAD="$(psql_primary "SELECT count(*) FROM pg_stat_ssl s JOIN pg_stat_activity a USING (pid) WHERE a.client_addr IS NOT NULL AND NOT s.ssl AND a.backend_type='client backend'")"
[[ "$BAD" == 0 ]] || { echo "FAIL: $BAD client connections without TLS" >&2; exit 1; }
kubectl -n "$NS" delete pod plain --ignore-not-found --wait=true >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: plain, namespace: $NS}
spec:
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 26, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: plain
      image: $IMG
      imagePullPolicy: IfNotPresent
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
      env:
        - name: PGPASSWORD
          valueFrom: {secretKeyRef: {name: postgres-superuser, key: password}}
      command: [bash, -c]
      args: ["psql 'host=postgresql-rw.$NS.svc.cluster.local user=postgres dbname=postgres sslmode=disable connect_timeout=5' -c 'select 1' 2>&1; true"]
YAML
for _ in $(seq 1 60); do [[ "$(kubectl -n "$NS" get pod plain -o jsonpath='{.status.phase}')" == Succeeded ]] && break; sleep 1; done
kubectl -n "$NS" logs plain | grep -qi "no encryption\|rejects" || { kubectl -n "$NS" logs plain; echo "FAIL: a cleartext connection was not refused for lack of encryption" >&2; exit 1; }
kubectl -n "$NS" delete pod plain --wait=false >/dev/null
echo "PASS: all client connections use TLS; a cleartext connection is rejected by pg_hba"
[[ -n "${TEARDOWN:-}" ]] && { kubectl delete ns "$NS" --wait=false >/dev/null; kubectl -n cnpg-system delete networkpolicy "operator-to-$NS" --ignore-not-found >/dev/null;
  [[ -z "$ARCHIVE" ]] || { docker rm -f "idp-kind-$CLUSTER-s3" >/dev/null; docker volume rm -f "idp-kind-$CLUSTER-s3" >/dev/null; }; }
echo "ALL PASS"
