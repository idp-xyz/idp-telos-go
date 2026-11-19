#!/usr/bin/env bash
# Build idp-pg-backup:dev (the base image is pulled first when the Docker daemon does not have it; on a host whose daemon cannot
# pull, put it there beforehand, see deploy/kubernetes/RUNBOOK.md; apk needs the proxy at build time, passed through if set).
set -euo pipefail
cd "$(dirname "$0")"
# the base image is pulled here, with retries, so that docker build does not pull it (named once, in the Dockerfile)
bash ../../../tools/pull_image.sh "$(sed -n 's/^FROM //p' Dockerfile | head -1)"
docker build --network host ${HTTPS_PROXY:+--build-arg https_proxy="$HTTPS_PROXY" --build-arg http_proxy="${HTTP_PROXY:-$HTTPS_PROXY}"} -t "${1:-idp-pg-backup:dev}" .
