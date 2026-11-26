#!/usr/bin/env bash
# Runs PATH P0, P2 and/or P3 (not the others) against a compose platform that is already up, to try a path's script on its own:
#   KEEP=1 STOP_AFTER_SETUP=1 bash deploy/compose/e2e.sh     build, start and configure the platform, run nothing, leave it up
#   bash deploy/compose/p_only.sh p0 p2 p3                   the paths, in that order, with the adapters e2e.sh gives them
#   docker compose ... down -v                                (the project's own compose command, as e2e.sh's cleanup does)
# It defines what the paths take from e2e.sh (the same adapters) and nothing else. Not part of the acceptance: e2e.sh is.
set -euo pipefail
cd "$(dirname "$0")/../.."
L=.local/platform; ENVF=$L/compose.env
source deploy/compose/lib_acceptance.sh   # path_needs_real and EMBED (double by default: set EMBED=real when the platform was started with the real embedding image)
C=(docker compose -f deploy/compose/platform.yaml --env-file "$ENVF" --profile tools --profile egress-test --profile web-test --profile bridge-test --profile embedding-query)
set -a; source "$ENVF"; set +a
ISSUER=https://127.0.0.1:7443/realms/idp CMI=http://127.0.0.1:8550 HOST=http://127.0.0.1:8450 MODEL=fake
SYSGW_URL=http://mcp-gateway-system:3320/mcp
SYSTEM_TENANT="$(python3 -c "import re;print(re.search(r'workloadTenant: (\S+)',open('$L/cmi.yaml').read()).group(1))")"
secret_of() { python3 -c "import json,sys; d=json.load(open('$L/clients.json')); print(d['users'].get(sys.argv[1]) or d[sys.argv[1]])" "$1"; }
token_json() { python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
KC=(curl -sf --noproxy '*' --cacert "$L/tls/ca.crt")
user_token() { "${KC[@]}" "$ISSUER/protocol/openid-connect/token" -d grant_type=password -d client_id=e2e-cli -d "client_secret=$(secret_of e2e-cli)" -d "username=$1" -d "password=$(secret_of "$1")" | token_json; }
call() { local want="$1" method="$2" url="$3" token="$4" body="${5:-}" out status; out="$(mktemp)"; status="$(curl -s --noproxy '*' -o "$out" -w '%{http_code}' -X "$method" "$url" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' ${body:+--data "$body"})"; if [[ "$status" != "$want" ]]; then echo "FAIL: $method $url -> $status (want $want): $(cat "$out")" >&2; rm -f "$out"; exit 1; fi; cat "$out"; rm -f "$out"; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
kbs3() { printf 'user = "%s:%s"\n' "$IDP_KB_S3_ACCESS_KEY" "$IDP_KB_S3_SECRET_KEY" | curl -sS --noproxy '*' --config - --aws-sigv4 aws:amz:us-east-1:s3 "$@"; }
p0_s3_keys() { kbs3 -f "http://127.0.0.1:19510/knowledge-originals?list-type=2&max-keys=1000" | grep -o '<Key>[^<]*</Key>' | sed 's#</\?Key>##g'; }
p1_metrics() { curl -s --noproxy '*' http://127.0.0.1:19564/metrics; }
p1_sql() { "${C[@]}" exec -T postgres psql -U postgres -d idp_cmi -Atc "$1"; }
p3_hostsql() { "${C[@]}" exec -T postgres psql -U postgres -d idp_host -Atc "$1"; }
source deploy/compose/lib_p1_helpers.sh
ALICE="$(user_token alice)"; BOB="$(user_token bob)"; CAROL="$(user_token carol)"
BOB_ID="$(python3 -c "import json; print(json.load(open('$L/clients.json'))['ids']['bob'])")"
for p in "$@"; do
  case "$p" in
    p0) source deploy/compose/path_p0.sh;;
    p2) eval "$(sed -n '/^P2_SYS_AUD=/,/^P2_NET_WHAT=/p' deploy/compose/e2e.sh)"; source deploy/compose/path_p2.sh;;
    p3) source deploy/compose/path_p3.sh;;
    *) echo "unknown path $p" >&2; exit 2;;
  esac
done
