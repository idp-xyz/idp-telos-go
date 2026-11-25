#!/usr/bin/env bash
# Build idp-embedding:dev. The model files come from, in this order:
#   1. EMBEDDING_MODEL_DIR (a directory with the files of model.sha256), or ~/.cache/idp-eval/models/Qwen3-Embedding-0.6B (what idp-cmi's tests/eval/embedding-server.sh fetches),
#   2. the pinned revision (MODEL_REVISION) downloaded from $HF_ENDPOINT (default https://huggingface.co; hf-mirror.com serves the same revisions) into
#      .local/embedding-model/<revision>/ (CI caches that directory with actions/cache, keyed on the revision).
# Whatever the source, every file is checked against model.sha256 here and again inside the image build, so a cache that holds another revision fails, never builds.
# The base image is the mirror's copy of text-embeddings-inference (EMBEDDING_BASE overrides it, e.g. a local retag before the mirror workflow has copied it).
#   bash deploy/images/embedding/build.sh [IMAGE]            default idp-embedding:dev
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../../.. && pwd)"
IMAGE="${1:-idp-embedding:dev}"
read -r REPO REVISION < MODEL_REVISION
FILES="$(awk '{print $2}' model.sha256)"
ok_dir() { local d="$1"; [[ -d "$d" ]] && (cd "$d" && sha256sum --quiet -c "$OLDPWD/model.sha256" >/dev/null 2>&1); }
SRC=""
for d in "${EMBEDDING_MODEL_DIR:-}" "$HOME/.cache/idp-eval/models/$(basename "$REPO")" "$ROOT/.local/embedding-model/$REVISION"; do
  [[ -n "$d" ]] && ok_dir "$d" && { SRC="$d"; break; }
done
if [[ -z "$SRC" ]]; then
  SRC="$ROOT/.local/embedding-model/$REVISION"; mkdir -p "$SRC"
  echo "== fetching $REPO@$REVISION from ${HF_ENDPOINT:-https://huggingface.co} into $SRC"
  for f in $FILES; do
    mkdir -p "$SRC/$(dirname "$f")"
    curl -sSL --http1.1 --retry 20 --retry-all-errors --retry-delay 3 -C - -m 3000 -o "$SRC/$f" "${HF_ENDPOINT:-https://huggingface.co}/$REPO/resolve/$REVISION/$f"
  done
  ok_dir "$SRC" || { echo "FAIL: the files fetched for $REPO@$REVISION do not match model.sha256" >&2; exit 1; }
fi
echo "== model files from $SRC (sha256 checked)"
# the build context: the files with the modes the image keeps (files 0444, directories 0555), so that the COPY in the Dockerfile is the only layer with the weights
CTX="$(mktemp -d)"; trap 'chmod -R u+w "$CTX" 2>/dev/null; rm -rf "$CTX"' EXIT
mkdir -p "$CTX/model"
for f in $FILES; do mkdir -p "$CTX/model/$(dirname "$f")"; cp "$SRC/$f" "$CTX/model/$f"; done
find "$CTX/model" -type f -exec chmod 0444 {} +; find "$CTX/model" -type d -exec chmod 0555 {} +
cp Dockerfile model.sha256 start.sh "$CTX/"
BASE="${EMBEDDING_BASE:-}"
[[ -z "$BASE" ]] || echo "== base image: $BASE"
if [[ -z "$BASE" ]]; then base="$(sed -n 's/^ARG BASE=//p' Dockerfile)"; else base="$BASE"; fi
bash "$ROOT/tools/pull_image.sh" "$base"   # with retries, so that docker build does not pull
docker build --network none ${BASE:+--build-arg BASE="$BASE"} -t "$IMAGE" "$CTX"
echo "OK: $IMAGE $(docker image inspect --format '{{.Id}}' "$IMAGE" | cut -c1-19)… ($(docker image inspect --format '{{.Size}}' "$IMAGE" | awk '{printf "%.0f MB", $1/1e6}'))"
