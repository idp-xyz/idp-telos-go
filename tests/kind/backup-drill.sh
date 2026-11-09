#!/usr/bin/env bash
# The memory's storage on the kind cluster: it survives a restart, a backup is taken, and after the CMI's database is lost
# it comes back from that backup. DEV ONLY, on the platform tests/kind/full.sh deployed (its Keycloak realm, tenant and
# secrets in .local/kind-full; the CMI source `idp-host` registered).
#
#   1  write a marker memory for bob (as the HOST does: idp-host-svc, cmi-writer) and read it back as bob
#   2  take a backup now (the CronJob pg-backup, started by hand): every file age-encrypted, no plaintext on the volume or in
#      the bucket (a MinIO container on the host), the set shipped
#   3  restart PostgreSQL's Pod: the memory is still there (the data directory is a PersistentVolumeClaim)
#   4  NODE_RESTART=1: restart the kind node container too (what a WSL or Docker restart does)
#   5  lose the CMI database (DROP DATABASE), see that the memory is gone, restore it FROM THE BUCKET with the private key
#      (which lives only in .local/kind-full and is put into the cluster just for this Job; DRILL_SOURCE=local restores from
#      the volume instead), restart the CMI Pods, and read the memory back as bob
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
L=.local/kind-full
CA=.local/kind-ca/ca.crt
ISSUER=https://127.0.0.1:17443/realms/idp
[[ -f $L/clients.json ]] || { echo "BLOCKED: no $L/clients.json: run tests/kind/full.sh first" >&2; exit 2; }
log() { echo "== $*"; }
PFPIDS=()
cleanup() { for p in "${PFPIDS[@]:-}"; do [[ -n "$p" ]] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; } || true; done; }
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
psql_cmi() { kubectl -n databases exec deploy/postgresql -- psql -U postgres -d "${2:-idp_cmi}" -tAc "$1"; }
wait_pg() { kubectl -n databases rollout status deploy/postgresql --timeout=300s >/dev/null; for _ in $(seq 1 120); do kubectl -n databases exec deploy/postgresql -- pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && return 0; sleep 1; done; echo "FAIL: PostgreSQL did not come back" >&2; exit 1; }
wait_cmi() { # right after a node restart the Deployment still reports the old readiness: ask the CMI itself, with a time limit on every try
  for _ in $(seq 1 180); do
    [[ "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -m 3 "$CMI/readyz")" == 200 ]] && [[ "$(kubectl -n idp-cmi get deploy idp-cmi -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" == "$(kubectl -n idp-cmi get deploy idp-cmi -o jsonpath='{.spec.replicas}' 2>/dev/null)" ]] && return 0
    sleep 1
  done
  echo "FAIL: the CMI did not become ready" >&2; exit 1
}
MARK="drill marker $(date -u +%Y%m%dT%H%M%SZ): bob likes the colour vermilion"
has_marker() { cmi 200 GET "/v1/memory/records?limit=100" "$(bob_token)" | python3 -c "
import json,sys
sys.exit(0 if any('$MARK' in r['content'] for r in json.load(sys.stdin)['records']) else 1)"; }

ensure_forward idp-cmi idp-cmi 17550:8550
ensure_forward keycloak keycloak 17443:7443
wait_kc() { for _ in $(seq 1 120); do curl -sf --noproxy '*' -m 3 --cacert "$CA" -o /dev/null "$ISSUER/.well-known/openid-configuration" && return 0; sleep 1; done; echo "FAIL: Keycloak does not answer" >&2; exit 1; }
wait_kc
kubectl -n databases get pvc postgres-data postgres-backups >/dev/null || { echo "BLOCKED: the PostgreSQL volume claims are missing: redeploy with tests/kind/full.sh" >&2; exit 2; }

log "1 a marker memory for bob"
BOB_ID="$(python3 -c "import json; print(json.load(open('$L/clients.json'))['ids']['bob'])")"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cmi 201 POST /v1/memory/records "$(host_token)" "$(python3 -c "
import json,sys
print(json.dumps({'scope':{'kind':'subject','id':'$BOB_ID'},'kind':'episodic','content':'$MARK','tags':['drill'],
  'provenance':{'source':'idp-host','ref':'drill:'+'$NOW','observedAt':'$NOW','recordedBy':'idp-host'},'sensitivity':'internal'}))")" >/dev/null
has_marker || { echo "FAIL: bob does not read the marker he was just given" >&2; exit 1; }
BEFORE="$(psql_cmi "SELECT count(*) FROM memory_records")"
echo "PASS: the marker is written and bob reads it back ($BEFORE memory records in idp_cmi)"

log "2 a backup"
JOB="drill-backup-$(date +%s)"
kubectl -n databases create job --from=cronjob/pg-backup "$JOB" >/dev/null
kubectl -n databases wait --for=condition=complete "job/$JOB" --timeout=300s >/dev/null || { kubectl -n databases logs "job/$JOB" --tail=30; echo "FAIL: the backup job did not complete" >&2; exit 1; }
kubectl -n databases logs "job/$JOB" | tail -14
kubectl -n databases logs "job/$JOB" | grep -q "idp_cmi.dump.age" || { echo "FAIL: the backup holds no encrypted idp_cmi dump" >&2; exit 1; }
kubectl -n databases logs "job/$JOB" | grep -q "shipped " || { echo "FAIL: the backup was not shipped to the bucket" >&2; exit 1; }
kubectl -n databases logs "job/$JOB" | grep -E "\.(dump|sql)\$" | grep -qv "\.age" && { echo "FAIL: a plaintext file is on the backup volume" >&2; exit 1; }
# what the bucket holds, asked from the host with the target's own credentials
bucket() { # bucket SHELL-SNIPPET (s3.sh sourced)
  docker run --rm --network host -e S3_ENDPOINT="$(kubectl -n databases get secret backup-target -o jsonpath='{.data.S3_ENDPOINT}' | base64 -d)" -e S3_BUCKET=backups \
    -e S3_ACCESS_KEY="$(kubectl -n databases get secret backup-target -o jsonpath='{.data.S3_ACCESS_KEY}' | base64 -d)" -e S3_SECRET_KEY="$(kubectl -n databases get secret backup-target -o jsonpath='{.data.S3_SECRET_KEY}' | base64 -d)" \
    idp-pg-backup:dev sh -c ". /usr/local/bin/s3.sh; $1"
}
KEYS="$(bucket 's3_keys pg/')"
echo "$KEYS" | grep -q "idp_cmi.dump.age" && echo "$KEYS" | grep -q "MANIFEST.sha256" || { echo "$KEYS"; echo "FAIL: the bucket lacks the encrypted dump or the manifest" >&2; exit 1; }
echo "$KEYS" | grep -vq "\.age\$\|MANIFEST" && { echo "$KEYS"; echo "FAIL: a plaintext object is in the bucket" >&2; exit 1; }
LATEST="$(echo "$KEYS" | grep -o 'pg/[0-9TZ]*/' | sort -u | tail -1)"
[[ "$(bucket "s3_get ${LATEST}idp_cmi.dump.age /tmp/o; head -c 22 /tmp/o")" == "age-encryption.org/v1"* ]] || { echo "FAIL: the dump in the bucket is not an age file" >&2; exit 1; }
echo "PASS: a backup was taken: every file age-encrypted for a public key that is the only key in the cluster, none in the clear on the volume or in the bucket, shipped with its manifest"

log "3 PostgreSQL restarts"
kubectl -n databases delete pod -l app.kubernetes.io/name=postgresql --wait=true >/dev/null
wait_pg
[[ "$(psql_cmi "SELECT count(*) FROM memory_records")" == "$BEFORE" ]] || { echo "FAIL: records were lost with the Pod" >&2; exit 1; }
wait_cmi; has_marker || { echo "FAIL: the marker is gone after the restart" >&2; exit 1; }
echo "PASS: the memory survives a PostgreSQL Pod restart ($BEFORE records, the marker readable)"

if [[ "${NODE_RESTART:-0}" == 1 ]]; then
  log "4 the node restarts"
  cleanup; PFPIDS=()
  docker restart kind-control-plane >/dev/null
  for _ in $(seq 1 120); do kubectl get nodes 2>/dev/null | grep -q " Ready" && break; sleep 2; done
  for _ in $(seq 1 90); do kubectl get nodes >/dev/null 2>&1 && break; sleep 2; done
  wait_pg
  ensure_forward idp-cmi idp-cmi 17550:8550; ensure_forward keycloak keycloak 17443:7443
  wait_cmi; wait_kc
  [[ "$(psql_cmi "SELECT count(*) FROM memory_records")" == "$BEFORE" ]] || { echo "FAIL: records were lost with the node restart" >&2; exit 1; }
  has_marker || { echo "FAIL: the marker is gone after the node restart" >&2; exit 1; }
  echo "PASS: the memory survives a restart of the node"
fi

log "5 the CMI database is lost and restored from the backup"
kubectl -n databases exec deploy/postgresql -- psql -U postgres -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'idp_cmi' AND pid <> pg_backend_pid()" >/dev/null
kubectl -n databases exec deploy/postgresql -- psql -U postgres -d postgres -c "DROP DATABASE idp_cmi" >/dev/null
if has_marker 2>/dev/null; then echo "FAIL: the memory is still readable after its database was dropped" >&2; exit 1; fi
echo "the database is gone: bob can no longer read his memory"
RJOB="drill-restore-$(date +%s)"
SRC="${DRILL_SOURCE:-remote}"
# the private key comes into the cluster for this restore only
kubectl -n databases create secret generic backup-identity --from-file=identity.txt="$L/backup-identity.txt" >/dev/null
python3 - "$RJOB" "$SRC" <<'PY' | kubectl apply -f - >/dev/null
import sys
job, src = sys.argv[1:3]
print(f"""apiVersion: batch/v1
kind: Job
metadata: {{name: {job}, namespace: databases}}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  template:
    metadata: {{labels: {{app.kubernetes.io/name: pg-backup}}}}
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext: {{runAsNonRoot: true, runAsUser: 70, runAsGroup: 70, fsGroup: 70, seccompProfile: {{type: RuntimeDefault}}}}
      containers:
        - name: restore
          image: idp-pg-backup:dev
          imagePullPolicy: IfNotPresent
          command: [restore.sh, idp_cmi]
          envFrom:
            - secretRef: {{name: backup-target}}
          env:
            - {{name: PGHOST, value: postgresql}}
            - {{name: SOURCE, value: {src}}}
            - {{name: AGE_IDENTITY_FILE, value: /identity/identity.txt}}
            - name: PGPASSWORD
              valueFrom: {{secretKeyRef: {{name: postgres-owner, key: password}}}}
          resources: {{limits: {{cpu: "1", memory: 512Mi}}}}
          securityContext: {{allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {{drop: [ALL]}}}}
          volumeMounts:
            - {{name: backups, mountPath: /backup, readOnly: true}}
            - {{name: identity, mountPath: /identity, readOnly: true}}
            - {{name: work, mountPath: /work}}
            - {{name: tmp, mountPath: /tmp}}
      volumes:
        - {{name: backups, persistentVolumeClaim: {{claimName: postgres-backups}}}}
        - {{name: identity, secret: {{secretName: backup-identity}}}}
        - {{name: work, emptyDir: {{sizeLimit: 2Gi}}}}
        - {{name: tmp, emptyDir: {{}}}}""")
PY
kubectl -n databases wait --for=condition=complete "job/$RJOB" --timeout=300s >/dev/null || { kubectl -n databases logs "job/$RJOB" --tail=30; kubectl -n databases delete secret backup-identity >/dev/null 2>&1; echo "FAIL: the restore job did not complete" >&2; exit 1; }
kubectl -n databases logs "job/$RJOB" | tail -4
kubectl -n databases delete secret backup-identity >/dev/null   # the private key leaves the cluster again
kubectl -n idp-cmi rollout restart deploy/idp-cmi >/dev/null   # the pools hold connections to a database that was dropped
wait_cmi
RESTORED="$(psql_cmi "SELECT count(*) FROM memory_records")"
[[ "$RESTORED" -ge "$BEFORE" ]] || { echo "FAIL: $RESTORED records restored, $BEFORE expected" >&2; exit 1; }
has_marker || { echo "FAIL: bob cannot read the marker after the restore" >&2; exit 1; }
echo "PASS: the CMI database was dropped and restored from the $SRC encrypted backup with the private key brought in for the purpose ($RESTORED records), the CMI serves it again and bob reads his memory"
