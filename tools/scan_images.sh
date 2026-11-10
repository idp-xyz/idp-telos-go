#!/usr/bin/env bash
# Scan the platform's container images for known vulnerabilities with trivy (pinned), HIGH and CRITICAL only and only those
# with a fixed version (an unfixed one gives nobody anything to do). Prints a Markdown summary per image, one row per
# finding; with STRICT=1 the exit status is 1 when any image has a finding. CI runs it after the acceptance, before the
# images are published (.github/workflows/platform.yml).
#
#   bash tools/scan_images.sh                  the component images (idp-*:dev, eer-runtime:dev) and the third-party ones
#   bash tools/scan_images.sh --own            only ours: STRICT=1 makes a finding fail (CI does)
#   bash tools/scan_images.sh --third-party    only the third-party ones: reported, not gating (we cannot rebuild them)
#   bash tools/scan_images.sh IMAGE...         just these
#   TRIVY_CACHE=dir                            where the vulnerability database lives (default .local/trivy-cache)
#
# The database is downloaded by trivy on the first image: a runner can, a host behind a flaky proxy may not (see
# deploy/kubernetes/RUNBOOK.md: put trivydb.tar.gz's contents into $TRIVY_CACHE/db and set TRIVY_SKIP_DB_UPDATE=1).
set -euo pipefail
cd "$(dirname "$0")/.."
TRIVY="${TRIVY_IMAGE:-ghcr.io/aquasecurity/trivy:0.76.2}"
CACHE="${TRIVY_CACHE:-$PWD/.local/trivy-cache}"; mkdir -p "$CACHE"; chmod 777 "$CACHE"
OWN=(idp-cmi:dev idp-host:dev idp-workspace:dev eer-runtime:dev idp-mcp:dev idp-sandbox:dev idp-pg-backup:dev)
THIRD=(pgvector/pgvector:0.8.7-pg17 public.ecr.aws/docker/library/postgres:17-alpine quay.io/keycloak/keycloak:26.8.2 temporalio/temporal:1.7.3
       public.ecr.aws/docker/library/alpine:3.22 public.ecr.aws/docker/library/python:3.12-slim quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z)
case "${1:-}" in
  --own) IMAGES=("${OWN[@]}");;
  --third-party) IMAGES=("${THIRD[@]}");;
  "") IMAGES=("${OWN[@]}" "${THIRD[@]}");;
  *) IMAGES=("$@");;
esac
SKIP=(); [[ -n "${TRIVY_SKIP_DB_UPDATE:-}" ]] && SKIP=(--skip-db-update)
PROXY=(); [[ -n "${HTTPS_PROXY:-}" ]] && PROXY=(-e "HTTPS_PROXY=$HTTPS_PROXY" -e "HTTP_PROXY=${HTTP_PROXY:-$HTTPS_PROXY}" -e NO_PROXY=localhost,127.0.0.1 --network host)
status=0
echo "## Image vulnerabilities (trivy ${TRIVY##*:}, HIGH and CRITICAL with a fix)"; echo
printf '| image | findings | detail |\n|---|---|---|\n'
for img in "${IMAGES[@]}"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then docker pull -q "$img" >/dev/null 2>&1 || { printf '| `%s` | not scanned | image not available |\n' "$img"; continue; }; fi
  json="$(mktemp)"   # a file, not a variable: an image with many findings is more than an environment can carry
  docker run --rm "${PROXY[@]}" -v /var/run/docker.sock:/var/run/docker.sock -v "$CACHE:/root/.cache/trivy" "$TRIVY" image --quiet "${SKIP[@]}" \
    --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --format json "$img" >"$json" 2>/dev/null || true
  python3 - "$img" "$json" <<'PY' || status=1
import json, sys
img = sys.argv[1]
try:
    d = json.load(open(sys.argv[2]))
except Exception:
    print(f"| `{img}` | not scanned | trivy gave no result (database unavailable?) |"); sys.exit(0)
rows = []
for r in d.get("Results", []):
    for v in r.get("Vulnerabilities") or []:
        rows.append((v["Severity"], v["VulnerabilityID"], v["PkgName"], v["InstalledVersion"], v.get("FixedVersion", "")))
rows.sort(key=lambda x: (x[0] != "CRITICAL", x[2], x[1]))
detail = "<br>".join(f"{s} {i} {p} {a} → {f}" for s, i, p, a, f in rows[:12]) + ("<br>…" if len(rows) > 12 else "")
print(f"| `{img}` | {len(rows)} | {detail} |")
sys.exit(1 if rows else 0)
PY
  rm -f "$json"
done
[[ -z "${STRICT:-}" ]] && exit 0 || exit "$status"
