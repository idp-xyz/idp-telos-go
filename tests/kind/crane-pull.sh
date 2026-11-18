#!/usr/bin/env bash
# crane pull, tried again when the registry refuses or the connection drops. crane gives up on the first 429: one night the HA
# drills in CI ended at "GET https://ghcr.io/v2/...: TOOMANYREQUESTS" before any of them had started.
#
#   bash tests/kind/crane-pull.sh IMAGE FILE.tar        linux/amd64; 5 tries, 5, 10, 20 and 40 s apart
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
IMG="${1:?image}"; TAR="${2:?tar file}"
wait=5
for try in 1 2 3 4 5; do
  if err="$(crane pull --platform linux/amd64 "$IMG" "$TAR" 2>&1 >/dev/null)"; then exit 0; fi
  rm -f "$TAR"
  [[ "$try" == 5 ]] && break
  echo "crane pull $IMG: try $try failed ($(echo "$err" | tail -1 | cut -c1-200)); again in $wait s" >&2
  sleep "$wait"; wait=$((wait * 2))
done
echo "$err" >&2
echo "FAIL: crane pull $IMG failed 5 times" >&2
exit 1
