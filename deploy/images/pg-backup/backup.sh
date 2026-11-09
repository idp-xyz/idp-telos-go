#!/bin/sh
# Dump every database and the roles, check each dump is readable, ENCRYPT it with the public key(s) in
# $AGE_RECIPIENTS_FILE (the private key is never in the cluster), keep the last $KEEP_LOCAL sets on /backup and, when
# S3_ENDPOINT is set, ship the set to the bucket (the manifest last, so a set without a manifest is incomplete) and keep
# the last $KEEP_REMOTE sets there. Any failure fails the Job, which the missed-backup alert (deploy/observability) sees.
set -eu
. /usr/local/bin/s3.sh
: "${AGE_RECIPIENTS_FILE:?no recipients: refusing to write an unencrypted backup}"
[ -s "$AGE_RECIPIENTS_FILE" ] || { echo "recipients file is empty: refusing to write an unencrypted backup" >&2; exit 1; }
export PGHOST="${PGHOST:-postgresql}" PGUSER="${PGUSER:-postgres}"
KEEP_LOCAL="${KEEP_LOCAL:-7}"; KEEP_REMOTE="${KEEP_REMOTE:-30}"; PREFIX="${S3_PREFIX:-pg}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"; W="/work/$TS"; D="/backup/$TS"; mkdir -p "$W" "$D.partial"
enc() { age -R "$AGE_RECIPIENTS_FILE" -o "$D.partial/$1.age" "$W/$1"; rm -f "$W/$1"; }

pg_dumpall --globals-only > "$W/globals.sql"; enc globals.sql
for db in $(psql -d postgres -tAc "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres' ORDER BY 1"); do
  pg_dump -Fc -d "$db" -f "$W/$db.dump"
  pg_restore -l "$W/$db.dump" >/dev/null   # the dump must be readable, or the backup failed
  enc "$db.dump"
done
(cd "$D.partial" && sha256sum *.age > MANIFEST.sha256)   # over the ciphertext: verifiable without the key
mv "$D.partial" "$D"; rmdir "$W" 2>/dev/null || true
ls -1dt /backup/2* | tail -n +"$((KEEP_LOCAL + 1))" | xargs -r rm -rf

if [ -n "${S3_ENDPOINT:-}" ]; then
  for f in "$D"/*.age; do s3_put "$f" "$PREFIX/$TS/$(basename "$f")"; done
  s3_put "$D/MANIFEST.sha256" "$PREFIX/$TS/MANIFEST.sha256"
  # the shipped set must be what was written: read the manifest back and compare
  s3_get "$PREFIX/$TS/MANIFEST.sha256" /tmp/manifest.remote
  cmp -s /tmp/manifest.remote "$D/MANIFEST.sha256" || { echo "the manifest in the bucket differs from the local one" >&2; exit 1; }
  SETS="$(s3_dirs "$PREFIX/")" || { echo "listing the bucket failed" >&2; exit 1; }
  for old in $(printf '%s\n' "$SETS" | sort | head -n -"$KEEP_REMOTE"); do
    KEYS="$(s3_keys "$PREFIX/$old/")" || { echo "listing the bucket failed" >&2; exit 1; }
    for k in $KEYS; do s3_delete "$k"; done
    echo "removed remote set $old"
  done
  echo "shipped $TS to $S3_ENDPOINT/$S3_BUCKET/$PREFIX/"
fi
echo "backup $TS (encrypted):"; ls -l "$D"
