#!/usr/bin/env bash
# Build idp-pg-backup:dev (the base image must already be in the Docker daemon: this host's daemon cannot always pull,
# see deploy/kubernetes/RUNBOOK.md; apk needs the proxy at build time, passed through if set).
set -euo pipefail
cd "$(dirname "$0")"
docker build --network host ${HTTPS_PROXY:+--build-arg https_proxy="$HTTPS_PROXY" --build-arg http_proxy="${HTTP_PROXY:-$HTTPS_PROXY}"} -t "${1:-idp-pg-backup:dev}" .
