#!/usr/bin/env bash
# Build the embedding double: bash deploy/images/embedding-double/build.sh [TAG]   (default idp-embedding-double:dev). BASE names another Python image.
set -euo pipefail
cd "$(dirname "$0")"
TAG="${1:-idp-embedding-double:dev}"
BASE="${BASE:-ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim}"
bash ../../../tools/pull_image.sh "$BASE"   # mirror:variable (BASE defaults to the mirror; an override names another Python image)
docker build -q --build-arg "BASE=$BASE" -t "$TAG" . >/dev/null
echo "OK: $TAG"
