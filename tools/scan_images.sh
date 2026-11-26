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
#   SCAN_SKIP="idp-embedding:dev"             leave these out of the list (the T2 run has the embedding double, not the real service's image)
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
TRIVY="${TRIVY_IMAGE:-ghcr.io/idp-xyz/mirror:ghcr.io__aquasecurity__trivy__0.76.2}"
CACHE="${TRIVY_CACHE:-$PWD/.local/trivy-cache}"; mkdir -p "$CACHE"; chmod 777 "$CACHE"
OWN=(idp-cmi:dev idp-host:dev idp-workspace:dev eer-runtime:dev idp-mcp:dev idp-sandbox:dev idp-sandbox-egress:dev idp-web:dev idp-mcp-bridge:dev idp-browser:dev idp-browser-proxy:dev idp-embedding:dev idp-pg-backup:dev idp-postgres-ha:17-dev)
THIRD=(ghcr.io/idp-xyz/mirror:docker.io__pgvector__pgvector__0.8.7-pg17 ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine ghcr.io/idp-xyz/mirror:quay.io__keycloak__keycloak__26.8.2 ghcr.io/idp-xyz/mirror:docker.io__temporalio__temporal__1.10.0 ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__cloudnative-pg__1.30.1
       ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__plugin-barman-cloud__v0.16.0 ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__plugin-barman-cloud-sidecar__v0.16.0
       ghcr.io/idp-xyz/mirror:quay.io__jetstack__cert-manager-controller__v1.21.3 ghcr.io/idp-xyz/mirror:quay.io__jetstack__cert-manager-webhook__v1.21.3 ghcr.io/idp-xyz/mirror:quay.io__jetstack__cert-manager-cainjector__v1.21.3
       ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__alpine__3.22 ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc
       idp-sandbox-office:dev)   # ours, but only the python image above plus the pinned OfficeCLI binary: nothing a rebuild of our code fixes
case "${1:-}" in
  --own) IMAGES=("${OWN[@]}");;
  --third-party) IMAGES=("${THIRD[@]}");;
  "") IMAGES=("${OWN[@]}" "${THIRD[@]}");;
  *) IMAGES=("$@");;
esac
if [[ -n "${SCAN_SKIP:-}" ]]; then   # the images of SCAN_SKIP are not scanned and not in the report
  KEEP=(); for i in "${IMAGES[@]}"; do [[ " $SCAN_SKIP " == *" $i "* ]] || KEEP+=("$i"); done; IMAGES=("${KEEP[@]}")
fi
OFFLINE=(); NET=()
if [[ -n "${TRIVY_OFFLINE:-}" ]]; then
  [[ -f "$CACHE/db/trivy.db" && -f "$CACHE/java-db/trivy-java.db" ]] || {
    echo "BLOCKED: TRIVY_OFFLINE=1 needs both databases in $CACHE (db/trivy.db and java-db/trivy-java.db; deploy/kubernetes/RUNBOOK.md)" >&2; exit 2; }
  OFFLINE=(--skip-db-update --skip-java-db-update --offline-scan --skip-version-check); NET=(--network none)
elif [[ -n "${HTTPS_PROXY:-}" ]]; then
  NET=(-e "HTTPS_PROXY=$HTTPS_PROXY" -e "HTTP_PROXY=${HTTP_PROXY:-$HTTPS_PROXY}" -e NO_PROXY=localhost,127.0.0.1 --network host)
fi
bash tools/pull_image.sh "$TRIVY"   # with retries, so that docker run does not pull it   # mirror:variable
status=0
echo "## Image vulnerabilities (trivy ${TRIVY##*:}, HIGH and CRITICAL with a fix)"; echo
printf '| image | findings | detail |\n|---|---|---|\n'
for img in "${IMAGES[@]}"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    if [[ -n "${TRIVY_OFFLINE:-}" ]]; then printf '| `%s` | not scanned | not in Docker (offline: crane pull + docker load) |\n' "$img"; status=1; continue; fi
    # with retries; an image that cannot be had is a row of the report, not the end of the run
    if ! pull_err="$(bash tools/pull_image.sh "$img" 2>&1 >/dev/null)"; then
      printf '| `%s` | not scanned | image not available: %s |\n' "$img" "$(tail -n 1 <<<"$pull_err" | tr '|' '/' | cut -c1-160)"; status=1; continue
    fi
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
