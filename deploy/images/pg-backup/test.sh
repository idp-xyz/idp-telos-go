#!/usr/bin/env bash
# Tests the backup image with Docker only (PostgreSQL + an S3-compatible store, RustFS + this image): encryption (no plaintext in the bucket),
# the shipped manifest, restore with the right and with the wrong private key, a tampered object, an incomplete set,
# local and remote retention. Needs idp-pg-backup:dev, postgres:17-alpine and the RustFS image in the daemon (tests/kind/full.sh
# loads it: crane pull by digest, docker load, tagged docker.io/rustfs/rustfs:1.0.4-glibc).
set -euo pipefail
IMG="${IMG:-idp-pg-backup:dev}"; PG_IMG="${PG_IMG:-public.ecr.aws/docker/library/postgres:17-alpine}"
S3_IMG="${S3_IMG:-docker.io/rustfs/rustfs:1.0.4-glibc}"
N=idp-bk-test; AK=testaccess; SK=testsecret-0123456789
cleanup() { docker rm -f $N-pg $N-s3 >/dev/null 2>&1 || true; docker network rm $N >/dev/null 2>&1 || true; docker volume rm -f $N-backup >/dev/null 2>&1 || true; rm -rf "${T:-}"; }
trap cleanup EXIT; cleanup; T="$(mktemp -d)"; chmod 755 "$T"
ok() { echo "PASS: $*"; }; fail() { echo "FAIL: $*" >&2; exit 1; }
docker network create $N >/dev/null
docker run -d --name $N-pg --network $N -e POSTGRES_PASSWORD=pw "$PG_IMG" >/dev/null
docker run -d --name $N-s3 --network $N -e RUSTFS_ACCESS_KEY=$AK -e RUSTFS_SECRET_KEY=$SK -e RUSTFS_REGION=us-east-1 -e RUSTFS_CONSOLE_ENABLE=false "$S3_IMG" >/dev/null
for _ in $(seq 1 60); do docker exec $N-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec $N-pg psql -U postgres -qc "CREATE DATABASE appdb" -c "\c appdb" -c "CREATE TABLE secret_notes (id int, note text)" -c "INSERT INTO secret_notes SELECT g, 'PLAINTEXT-MARKER-' || g FROM generate_series(1,2000) g" >/dev/null
docker exec $N-pg psql -U postgres -d appdb -qc "CREATE TABLE secret_notes (id int, note text)" >/dev/null 2>&1 || true
docker exec $N-pg psql -U postgres -d appdb -qc "INSERT INTO secret_notes SELECT g, 'PLAINTEXT-MARKER-' || g FROM generate_series(1,2000) g" >/dev/null 2>&1 || true
ROWS="$(docker exec $N-pg psql -U postgres -d appdb -tAc 'SELECT count(*) FROM secret_notes')"
docker run --rm "$IMG" age-keygen 2>"$T/pub.txt" >"$T/identity.txt"; sed -n 's/^Public key: //p' "$T/pub.txt" > "$T/recipients.txt"; [[ -s "$T/recipients.txt" ]] || fail "no recipient"
docker run --rm "$IMG" age-keygen 2>/dev/null >"$T/other-identity.txt"
chmod 644 "$T"/*
docker volume create $N-backup >/dev/null
docker run --rm --user 0 --entrypoint chown -v $N-backup:/backup "$IMG" 70:70 /backup   # a Kubernetes PVC gets this from fsGroup
ENVS=(-e PGHOST=$N-pg -e PGPASSWORD=pw -e S3_ENDPOINT=http://$N-s3:9000 -e S3_BUCKET=backups -e S3_ACCESS_KEY=$AK -e S3_SECRET_KEY=$SK -e AGE_RECIPIENTS_FILE=/keys/recipients.txt)
run() { docker run --rm --network $N --read-only --tmpfs /work --tmpfs /tmp -v $N-backup:/backup -v "$T":/keys:ro "${ENVS[@]}" "$@"; }
for _ in $(seq 1 60); do run --entrypoint sh "$IMG" -c '. /usr/local/bin/s3.sh; s3_ensure_bucket' 2>/dev/null && break; sleep 1; done

# 1 no recipients, no backup
if docker run --rm --network $N -e PGHOST=$N-pg -e PGPASSWORD=pw -e AGE_RECIPIENTS_FILE=/nonexistent -v $N-backup:/backup --tmpfs /work "$IMG" backup.sh >/dev/null 2>&1; then fail "a backup without a recipient file succeeded"; fi
ok "no recipients: the backup refuses to write an unencrypted set"

# 2 a backup is encrypted, local and in the bucket
run -e KEEP_REMOTE=2 "$IMG" backup.sh >"$T/b1.log" 2>&1 || { cat "$T/b1.log"; fail "backup failed"; }
run --entrypoint sh "$IMG" -c ". /usr/local/bin/s3.sh; s3_keys pg/" > "$T/keys.txt"; echo "keys: $(cat $T/keys.txt | tr "\n" " ")"
grep -q 'appdb.dump.age$' "$T/keys.txt" && grep -q 'MANIFEST.sha256$' "$T/keys.txt" || { cat "$T/keys.txt"; fail "the bucket lacks the encrypted dump or the manifest"; }
grep -q 'dump$\|sql$' "$T/keys.txt" && fail "a plaintext object is in the bucket"
SET="$(grep -o 'pg/[0-9TZ]*/' "$T/keys.txt" | head -1 | cut -d/ -f2)"
run --entrypoint sh "$IMG" -c ". /usr/local/bin/s3.sh; s3_get pg/$SET/appdb.dump.age /tmp/x; grep -c PLAINTEXT-MARKER /tmp/x || true; head -c 22 /tmp/x" > "$T/obj.txt"
head -1 "$T/obj.txt" | grep -qx 0 || fail "plaintext found inside the encrypted object"
grep -q "age-encryption.org" "$T/obj.txt" || fail "the object is not an age file"
ok "the set in the bucket is age-encrypted (no plaintext marker, age header), manifest shipped and read back"

# 3 restore with the right key, from the bucket
run -v "$T":/keys:ro -e AGE_IDENTITY_FILE=/keys/identity.txt "$IMG" restore.sh appdb appdb_restored >"$T/r1.log" 2>&1 || { cat "$T/r1.log"; fail "restore failed"; }
GOT="$(docker exec $N-pg psql -U postgres -d appdb_restored -tAc 'SELECT count(*) FROM secret_notes')"
[[ "$GOT" == "$ROWS" ]] || fail "restored $GOT rows, expected $ROWS"
ok "restore from the bucket with the private key: $GOT rows"

# 4 the wrong key cannot restore
if run -e AGE_IDENTITY_FILE=/keys/other-identity.txt "$IMG" restore.sh appdb appdb_wrongkey >/dev/null 2>&1; then fail "a restore with the wrong key succeeded"; fi
docker exec $N-pg psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='appdb_wrongkey'" | grep -q 1 && fail "a database was created before the key was checked"
ok "the wrong private key cannot restore (and creates nothing)"

# 5 a tampered object is caught by the manifest
run --entrypoint sh "$IMG" -c ". /usr/local/bin/s3.sh; echo tampered > /tmp/bad; s3_put /tmp/bad pg/$SET/appdb.dump.age"
if run -e AGE_IDENTITY_FILE=/keys/identity.txt "$IMG" restore.sh appdb appdb_tampered >"$T/r2.log" 2>&1; then fail "a tampered object was restored"; fi
grep -q "FAILED\|did NOT match" "$T/r2.log" || { cat "$T/r2.log"; fail "the tamper was not reported by the checksum"; }
ok "a tampered object is rejected by the manifest check before decryption"

# 6 an incomplete newest set (no manifest) is skipped; remote retention keeps the newest KEEP_REMOTE
sleep 1; run -e KEEP_REMOTE=2 "$IMG" backup.sh >/dev/null 2>&1; sleep 1; run -e KEEP_REMOTE=2 "$IMG" backup.sh >/dev/null 2>&1
run --entrypoint sh "$IMG" -c '. /usr/local/bin/s3.sh; s3_dirs pg/' > "$T/dirs.txt"
[[ "$(wc -l < "$T/dirs.txt")" == 2 ]] || { cat "$T/dirs.txt"; fail "remote retention did not leave 2 sets"; }
ok "remote retention keeps the newest 2 sets"
run --entrypoint sh "$IMG" -c ". /usr/local/bin/s3.sh; echo x > /tmp/x; s3_put /tmp/x pg/99990101T000000Z/appdb.dump.age"
run -e AGE_IDENTITY_FILE=/keys/identity.txt "$IMG" restore.sh appdb appdb_skip >"$T/r3.log" 2>&1 || { cat "$T/r3.log"; fail "restore with a newer incomplete set failed"; }
grep -q "set 2" "$T/r3.log" || fail "the incomplete set was not skipped"
ok "a newer set without a manifest is skipped: the restore uses the newest complete one"

# 7 local restore, and local retention
run -e SOURCE=local -e AGE_IDENTITY_FILE=/keys/identity.txt "$IMG" restore.sh appdb appdb_local >/dev/null 2>&1 || fail "local restore failed"
ok "restore from the local encrypted copy"
echo "ALL PASS"
