#!/bin/sh
# Restore one database from the newest complete backup set. SOURCE=remote (default when S3_ENDPOINT is set) downloads from
# the bucket, SOURCE=local reads /backup. The set's manifest is checked, then the dump is decrypted with the private key
# in $AGE_IDENTITY_FILE - supplied only for a restore - and loaded with pg_restore.
#   restore.sh DATABASE [TARGET]   (TARGET, default DATABASE, must not exist)
set -eu
. /usr/local/bin/s3.sh
DB="${1:?usage: restore.sh DATABASE [TARGET]}"; TARGET="${2:-$DB}"
: "${AGE_IDENTITY_FILE:?no private key given}"
export PGHOST="${PGHOST:-postgresql}" PGUSER="${PGUSER:-postgres}"
PREFIX="${S3_PREFIX:-pg}"; SRC="${SOURCE:-$([ -n "${S3_ENDPOINT:-}" ] && echo remote || echo local)}"
W=/work/restore; mkdir -p "$W"
if [ "$SRC" = remote ]; then
  SET=""; SETS="$(s3_dirs "$PREFIX/")" || { echo "listing the bucket failed" >&2; exit 1; }
  for d in $(printf '%s\n' "$SETS" | sort -r); do
    if s3_get "$PREFIX/$d/MANIFEST.sha256" "$W/MANIFEST.sha256" 2>/dev/null; then SET="$d"; break; fi   # complete sets have a manifest
  done
  [ -n "$SET" ] || { echo "no complete backup set in the bucket" >&2; exit 1; }
  echo "restoring $DB from the bucket, set $SET"
  s3_get "$PREFIX/$SET/$DB.dump.age" "$W/$DB.dump.age"
else
  D="$(ls -1dt /backup/2* | head -n1)"; SET="$(basename "$D")"
  echo "restoring $DB from /backup, set $SET"
  cp "$D/MANIFEST.sha256" "$D/$DB.dump.age" "$W/"
fi
(cd "$W" && grep " $DB.dump.age\$" MANIFEST.sha256 | sha256sum -c -)   # the ciphertext is what the manifest says
age -d -i "$AGE_IDENTITY_FILE" -o "$W/$DB.dump" "$W/$DB.dump.age"
createdb "$TARGET"
pg_restore -d "$TARGET" --exit-on-error "$W/$DB.dump"
rm -rf "$W"
echo "restored $DB into $TARGET from set $SET"
