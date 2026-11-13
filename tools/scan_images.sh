#!/usr/bin/env bash
# Scan the platform's container images for known vulnerabilities with trivy (pinned), HIGH and CRITICAL only and only those
# with a fixed version (an unfixed one gives nobody anything to do). Prints a Markdown summary, one row per image; a
# vulnerability is counted once per package version even when several binaries carry it (the CloudNativePG operator ships
# an amd64 and an arm64 manager). With STRICT=1 the exit status is 1 when any image has a finding or could not be scanned
# (a gate that passes when it saw nothing is no gate). CI runs it after the acceptance, before the images are published
# (.github/workflows/platform.yml).
#
#   bash tools/scan_images.sh                  the component images (idp-*:dev, eer-runtime:dev) and the third-party ones
#   bash tools/scan_images.sh --own            only ours: STRICT=1 makes a finding fail (CI does)
#   bash tools/scan_images.sh --third-party    only the third-party ones: reported, not gating (we cannot rebuild them)
#   bash tools/scan_images.sh IMAGE...         just these
#   TRIVY_CACHE=dir                            where the databases live (default .local/trivy-cache)
#   TRIVY_OFFLINE=1                            no network: the two databases already in $TRIVY_CACHE, the images already in Docker,
#                                              trivy's container started without a network
#
# trivy downloads its two databases (the vulnerabilities, db/, and the Java index, java-db/: about 3 GB unpacked) into
# $TRIVY_CACHE when it first needs them and refreshes them when they are due: a runner can, a host behind a flaky proxy may not.
# deploy/kubernetes/RUNBOOK.md ("镜像漏洞扫描") packs them on a machine that can and scans here with TRIVY_OFFLINE=1. The summary
# ends with the databases' dates: a finding published after them is not seen, so an old database reports too little.
set -euo pipefail
cd "$(dirname "$0")/.."
TRIVY="${TRIVY_IMAGE:-ghcr.io/aquasecurity/trivy:0.76.2}"
CACHE="${TRIVY_CACHE:-$PWD/.local/trivy-cache}"; mkdir -p "$CACHE"; chmod 777 "$CACHE"
OWN=(idp-cmi:dev idp-host:dev idp-workspace:dev eer-runtime:dev idp-mcp:dev idp-sandbox:dev idp-pg-backup:dev idp-postgres-ha:17-dev)
THIRD=(pgvector/pgvector:0.8.7-pg17 public.ecr.aws/docker/library/postgres:17-alpine quay.io/keycloak/keycloak:26.8.2 temporalio/temporal:1.10.0 ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1
       ghcr.io/cloudnative-pg/plugin-barman-cloud:v0.16.0 ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.16.0
       quay.io/jetstack/cert-manager-controller:v1.21.3 quay.io/jetstack/cert-manager-webhook:v1.21.3 quay.io/jetstack/cert-manager-cainjector:v1.21.3
       public.ecr.aws/docker/library/alpine:3.22 public.ecr.aws/docker/library/python:3.12-slim docker.io/rustfs/rustfs:1.0.4-glibc
       idp-sandbox-office:dev)   # ours, but only the python image above plus the pinned OfficeCLI binary: nothing a rebuild of our code fixes
case "${1:-}" in
  --own) IMAGES=("${OWN[@]}");;
  --third-party) IMAGES=("${THIRD[@]}");;
  "") IMAGES=("${OWN[@]}" "${THIRD[@]}");;
  *) IMAGES=("$@");;
esac
OFFLINE=(); NET=()
if [[ -n "${TRIVY_OFFLINE:-}" ]]; then
  [[ -f "$CACHE/db/trivy.db" && -f "$CACHE/java-db/trivy-java.db" ]] || {
    echo "BLOCKED: TRIVY_OFFLINE=1 needs both databases in $CACHE (db/trivy.db and java-db/trivy-java.db; deploy/kubernetes/RUNBOOK.md)" >&2; exit 2; }
  OFFLINE=(--skip-db-update --skip-java-db-update --offline-scan --skip-version-check); NET=(--network none)
elif [[ -n "${HTTPS_PROXY:-}" ]]; then
  NET=(-e "HTTPS_PROXY=$HTTPS_PROXY" -e "HTTP_PROXY=${HTTP_PROXY:-$HTTPS_PROXY}" -e NO_PROXY=localhost,127.0.0.1 --network host)
fi
status=0
echo "## Image vulnerabilities (trivy ${TRIVY##*:}, HIGH and CRITICAL with a fix)"; echo
printf '| image | findings | detail |\n|---|---|---|\n'
for img in "${IMAGES[@]}"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    if [[ -n "${TRIVY_OFFLINE:-}" ]]; then printf '| `%s` | not scanned | not in Docker (offline: crane pull + docker load) |\n' "$img"; status=1; continue; fi
    docker pull -q "$img" >/dev/null 2>&1 || { printf '| `%s` | not scanned | image not available |\n' "$img"; status=1; continue; }
  fi
  json="$(mktemp)"; err="$(mktemp)"   # files, not variables: an image with many findings is more than an environment can carry
  trivy_() { docker run --rm "${NET[@]}" -v /var/run/docker.sock:/var/run/docker.sock -v "$CACHE:/root/.cache/trivy" "$TRIVY" image --quiet "${OFFLINE[@]}" \
    "$@" --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --format json "$img" >"$json" 2>"$err"; }
  # the daemon's copy first; when trivy cannot read an image from the daemon (the cert-manager images on the CI runner), from the
  # registry (not offline)
  trivy_ || { [[ -z "${TRIVY_OFFLINE:-}" ]] && trivy_ --image-src remote; } || true
  python3 - "$img" "$json" "$err" <<'PY' || status=1
import json, sys
img = sys.argv[1]
try:
    d = json.load(open(sys.argv[2]))
except Exception:
    why = ([l.strip() for l in open(sys.argv[3], errors="replace") if l.strip()] or ["no output (database unavailable?)"])[-1]
    why = why.split("\t")[-1]   # not the time and the level; a Go error chain ends with its cause, so keep the end
    why = why if len(why) <= 160 else "…" + why[-159:]
    print(f"| `{img}` | not scanned | trivy gave no result: {why.replace('|', '/')} |"); sys.exit(1)
rows = set()
for r in d.get("Results", []):
    for v in r.get("Vulnerabilities") or []:
        rows.add((v["Severity"], v["VulnerabilityID"], v["PkgName"], v["InstalledVersion"], v.get("FixedVersion", "")))
rows = sorted(rows, key=lambda x: (x[0] != "CRITICAL", x[2], x[1], x[3]))
detail = "<br>".join(f"{s} {i} {p} {a} → {f}" for s, i, p, a, f in rows[:12]) + ("<br>…" if len(rows) > 12 else "")
print(f"| `{img}` | {len(rows)} | {detail} |")
sys.exit(1 if rows else 0)
PY
  rm -f "$json" "$err"
done
python3 - "$CACHE" <<'PY'
import datetime as dt, json, sys
now, parts = dt.datetime.now(dt.timezone.utc), []
for name, sub in (("vulnerabilities", "db"), ("Java index", "java-db")):
    try:
        t = dt.datetime.fromisoformat(json.load(open(f"{sys.argv[1]}/{sub}/metadata.json"))["UpdatedAt"][:19] + "+00:00")
    except Exception:
        continue   # the Java index is fetched only for an image with Java in it
    age = (now - t).days
    parts.append(f"{name} {t:%Y-%m-%d %H:%M} UTC" + (f" (**{age} days old**: findings published since are not seen)" if age >= 7 else ""))
if parts:
    print("\nDatabases: " + "; ".join(parts))
PY
[[ -z "${STRICT:-}" ]] && exit 0 || exit "$status"
