#!/usr/bin/env bash
# Keep the platform on the kind cluster reachable from a browser (tests/kind/full.sh stops its own forwards when it ends):
#   Keycloak https://127.0.0.1:17443 (the issuer), the shell http://127.0.0.1:17560, the assistant page http://127.0.0.1:17561
# Starts them in the background (they restart themselves when a pod is replaced) and prints how to sign in.
#   bash tests/kind/forward.sh          start (idempotent)        bash tests/kind/forward.sh stop
set -euo pipefail
cd "$(dirname "$0")/../.."
PIDF=.local/kind-full/forward.pids
if [[ "${1:-}" == stop ]]; then
  [[ -f "$PIDF" ]] && while read -r p; do pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done < "$PIDF" || true
  rm -f "$PIDF"; echo "stopped"; exit 0
fi
mkdir -p .local/kind-full
[[ -f "$PIDF" ]] && while read -r p; do pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done < "$PIDF" || true
: > "$PIDF"
fwd() { ( while true; do kubectl -n "$1" port-forward "svc/$2" "$3" >/dev/null 2>&1; sleep 1; done ) >/dev/null 2>&1 & echo $! >> "$PIDF"; disown; }
fwd keycloak keycloak 17443:7443
fwd idp-workspace idp-workspace 17560:8560
fwd idp-workspace assistant 17561:8080
sleep 2
python3 - <<'PY'
import json
c = json.load(open(".local/kind-full/clients.json"))
print("Trust the test CA first: .local/kind-full/tls/ca.crt (a new one every full.sh run)")
print("Shell:      http://127.0.0.1:17560   (sign in at Keycloak https://127.0.0.1:17443)")
for u in ("alice", "bob"):
    print(f"  {u:6} {c['users'][u]}")
PY
