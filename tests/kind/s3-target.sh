#!/usr/bin/env bash
# An S3-compatible store on the HOST for the kind tests (outside the cluster, reached through the kind network's gateway): the encrypted
# backups and the WAL archive. RustFS (Apache-2.0; MinIO's community edition is no longer maintained or published), the glibc build
# that the Barman Cloud plugin's own tests run, pinned by digest, its console off, with a master key for server-side encryption
# (generated here, it lives and dies with the container: the WAL archive asks for AES256). The container and its volume are created afresh;
# the bucket named by S3_BUCKET (default backups) exists when this returns (exit 2 when it does not).
#
#   S3_ACCESS_KEY=... S3_SECRET_KEY=... bash tests/kind/s3-target.sh NAME ADDRESS PORT
#
# tests/kind/full.sh: idp-kind-backup-target on <gateway>:19000; tests/kind/ha-postgres.sh (ARCHIVE=1): idp-kind-<cluster>-s3 on 19100.
# tests/kind/smoke.sh and full.sh: knowledge originals, S3_BUCKET=knowledge-originals on 19510.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
[[ $# == 3 ]] || { echo "usage: S3_ACCESS_KEY=... S3_SECRET_KEY=... $0 NAME ADDRESS PORT" >&2; exit 2; }
NAME="$1" ADDR="$2" PORT="$3"
: "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"
S3_IMG=ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc S3_DIGEST=sha256:a9b705b9256be756802a60f3498abdc543918b5b5a4437a57a541e89e733c74d
BUCKET="${S3_BUCKET:-backups}"
docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm -f "$NAME" >/dev/null 2>&1 || true
if ! docker image inspect "$S3_IMG" >/dev/null 2>&1; then   # loaded by digest, named by its tag
  tar="$(mktemp --suffix=.tar)"
  bash tests/kind/crane-pull.sh "${S3_IMG%%:*}@$S3_DIGEST" "$tar" && docker tag "$(docker load -i "$tar" | sed -n 's/^Loaded image: //p' | tail -1)" "$S3_IMG"
  rm -f "$tar"
fi
docker run -d --name "$NAME" --restart unless-stopped -p "$ADDR:$PORT:9000" -v "$NAME:/data" \
  -e RUSTFS_ACCESS_KEY="$S3_ACCESS_KEY" -e RUSTFS_SECRET_KEY="$S3_SECRET_KEY" -e RUSTFS_REGION=us-east-1 -e RUSTFS_CONSOLE_ENABLE=false \
  -e RUSTFS_SSE_S3_MASTER_KEY="$(openssl rand -base64 32)" "$S3_IMG" >/dev/null   # server-side encryption (SSE-S3) for whoever asks for it
for _ in $(seq 1 30); do
  if docker image inspect idp-pg-backup:dev >/dev/null 2>&1; then
    docker run --rm --network host -e S3_ENDPOINT="http://$ADDR:$PORT" -e S3_BUCKET="$BUCKET" -e S3_ACCESS_KEY -e S3_SECRET_KEY \
      idp-pg-backup:dev sh -c '. /usr/local/bin/s3.sh; s3_ensure_bucket' 2>/dev/null && exit 0
  else
    printf 'user = "%s:%s"\n' "$S3_ACCESS_KEY" "$S3_SECRET_KEY" | curl -sS --noproxy '*' --config - --aws-sigv4 aws:amz:us-east-1:s3 -f -X PUT -o /dev/null "http://$ADDR:$PORT/$BUCKET" 2>/dev/null || true
    printf 'user = "%s:%s"\n' "$S3_ACCESS_KEY" "$S3_SECRET_KEY" | curl -sS --noproxy '*' --config - --aws-sigv4 aws:amz:us-east-1:s3 -f -o /dev/null "http://$ADDR:$PORT/$BUCKET?list-type=2&max-keys=1" 2>/dev/null && exit 0
  fi
  sleep 1
done
docker logs --tail 20 "$NAME"; echo "BLOCKED: the S3 store $NAME at $ADDR:$PORT has no bucket" >&2; exit 2
