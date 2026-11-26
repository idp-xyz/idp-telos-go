# PATH P0 (platform plan knowledge-base-plan.md, KB1; idp-cmi ADR-0001 D47 to D58), shared by deploy/compose/e2e.sh and tests/kind/full.sh, which `source` it.
# Everything goes through CMI's HTTP API with real Keycloak tokens: alice (cmi-admin), bob and carol (cmi-reader, members). What the caller supplies: CMI (the base URL), the
# tokens ALICE BOB CAROL, BOB_ID (bob's subject), `call EXPECTED METHOD URL TOKEN [BODY]` and `field`, p0_s3_keys (prints the keys in the originals bucket, one per line) and
# p0_cleanup_note (nothing). Names carry a random suffix so that a rerun against the same database does not collide; the words searched for are made of it as well.
R="$(openssl rand -hex 3)"; KB_ENT="p0-ent-$R"; KB_UNIT="p0-unit-$R"; SRC_DOC="p0-docs-$R"; TEAM="p0-team-$R"
# the user source is fixed: a member's personal base keeps the user source it was created with, so a rerun against the same database must use the same one
SRC_USER="p0-notes"
W_ENT="marigold${R//[0-9]/x}"; W_BOB="quokka${R//[0-9]/x}"; W_RATE="zephyr${R//[0-9]/x}"   # distinct words: no stemming or stop word turns two of them into one
p0_text() { python3 -c "import json,sys; print(json.dumps({'query':sys.argv[1],'purpose':'assist'${2:-}}))" "$1"; }
p0_resolve() { call 200 POST "$CMI/v1/context/resolve" "$1" "$2"; }   # TOKEN BODY -> the package envelope
hits() { python3 -c "
import json,sys
raw=sys.stdin.read()
try:
    d=json.loads(raw); raw=json.dumps(d['package']['items']) if isinstance(d, dict) and 'package' in d else raw   # a resolve answer: only the items count, its query text is the asker's own
except ValueError:
    pass
print(raw.count(sys.argv[1]))" "$1"; }   # how often a word is in the items of a package, or in a listing
rawhits() { python3 -c "import sys; print(sys.stdin.read().count(sys.argv[1]))" "$1"; }   # ... or anywhere, the query text of a package included
SECONDS_AHEAD() { python3 -c "
import datetime,sys
base=datetime.datetime.strptime(sys.argv[2], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc) if len(sys.argv) > 2 else datetime.datetime.now(datetime.timezone.utc)
print((base+datetime.timedelta(seconds=int(sys.argv[1]))).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$@"; }   # SECONDS_AHEAD N [FROM]
bad() { echo "FAIL: PATH P0 $*" >&2; exit 1; }

# setup: the sources, a unit that bob belongs to, an enterprise base and a unit base (administrator), one enterprise document
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"'"$SRC_DOC"'","displayName":"P0 documents","kind":"document-store","trust":"authoritative"}' >/dev/null
curl -s --noproxy '*' -o /dev/null -X POST "$CMI/api/v1/sources" -H "Authorization: Bearer $ALICE" -H 'Content-Type: application/json' --data '{"name":"'"$SRC_USER"'","displayName":"P0 members notes","kind":"user","trust":"informational"}'   # 201, or 409 on a rerun
call 200 PUT "$CMI/api/v1/units" "$ALICE" '{"kind":"team","id":"'"$TEAM"'","displayName":"P0 team","members":["'"$BOB_ID"'"],"roles":[]}' >/dev/null
KB_BODY() { echo '{"name":"'"$1"'","displayName":"'"$1"'","scope":'"$2"',"managers":{"subjects":[],"roles":[]},"defaultSensitivity":"internal","sensitivityCeiling":"confidential","source":"'"$SRC_DOC"'","quotaDocuments":0,"quotaBytes":0,"ocr":false}'; }
call 201 POST "$CMI/api/v1/knowledge/bases" "$ALICE" "$(KB_BODY "$KB_ENT" '{"kind":"tenant"}')" >/dev/null
call 201 POST "$CMI/api/v1/knowledge/bases" "$ALICE" "$(KB_BODY "$KB_UNIT" '{"kind":"team","id":"'"$TEAM"'"}')" >/dev/null
ENT_DOC="$(call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"'"$SRC_DOC"'","knowledgeBase":"'"$KB_ENT"'","uri":"kb://'"$KB_ENT"'/policy.md","title":"Travel policy","text":"# Travel policy\n\nThe enterprise rule for '"$W_ENT"' trips is to book economy."}' | field "['documentId']")"
UNIT_DOC="$(call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"'"$SRC_DOC"'","knowledgeBase":"'"$KB_UNIT"'","uri":"kb://'"$KB_UNIT"'/runbook.md","title":"Team runbook","text":"# Runbook\n\nThe team keeps the spare keys in the grey cabinet."}' | field "['documentId']")"
echo "P0 setup: enterprise base $KB_ENT (document $ENT_DOC), unit base $KB_UNIT over team $TEAM with bob as its member (document $UNIT_DOC)" >&2

# (1) bob (a unit member, role cmi-reader) uploads into his own personal base with kb://me/..., a personal base that the service creates on first use; the same path as the enterprise one
B_UP="$(call 201 POST "$CMI/v1/knowledge/documents" "$BOB" '{"source":"'"$SRC_USER"'","uri":"kb://me/policy.md","title":"Bob notes","text":"# Notes\n\nBob keeps the '"$W_BOB"' trip plan here, and the '"$W_ENT"' rule is his too."}')"
B_DOC="$(echo "$B_UP" | field "['documentId']")"; B_URI="$(echo "$B_UP" | field "['uri']")"; B_BASE="$(echo "$B_UP" | field "['knowledgeBase']")"
[[ "$B_URI" == "kb://$B_BASE/policy.md" && "$B_BASE" == personal-* ]] || bad "(1): kb://me/ was not stored as the canonical kb://<personal base>/ form: $B_URI in base $B_BASE"
B1_OK="bob's kb://me/policy.md was stored as $B_URI in his personal base $B_BASE"

# (2) a uri that is not kb://<base>/... into a base is refused with 422 (member route and administrator route), and so is one that names another base
call 422 POST "$CMI/v1/knowledge/documents" "$BOB" '{"source":"'"$SRC_USER"'","knowledgeBase":"'"$B_BASE"'","uri":"handbook://travel-policy","title":"x","text":"x"}' >/dev/null
call 422 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"'"$SRC_DOC"'","knowledgeBase":"'"$KB_ENT"'","uri":"handbook://travel-policy","title":"x","text":"x"}' >/dev/null
call 422 POST "$CMI/v1/knowledge/documents" "$BOB" '{"source":"'"$SRC_USER"'","uri":"kb://'"$KB_ENT"'/policy.md","title":"x","text":"x"}' >/dev/null
echo "PASS: PATH P0a — alice created an enterprise base ($KB_ENT) and a unit base ($KB_UNIT, team $TEAM); $B1_OK; a non-kb:// uri into a base is 422 on the member and the administrator route, and so is a kb:// uri that names another base"

# (3) bob's personal document with the same path as the enterprise one does not retire it: carol still retrieves the enterprise document, and it is still version 1 and current
CAROL_PKG="$(p0_resolve "$CAROL" "$(p0_text "$W_ENT trips economy")")"
[[ "$(echo "$CAROL_PKG" | hits "$W_ENT")" -ge 1 && "$(echo "$CAROL_PKG" | hits "enterprise rule for")" -ge 1 ]] || bad "(3): carol does not retrieve the enterprise document: $(echo "$CAROL_PKG" | head -c 400)"
[[ "$(echo "$CAROL_PKG" | hits "$W_BOB")" -eq 0 ]] || bad "(3): carol's package has bob's personal words"
call 200 GET "$CMI/api/v1/knowledge/documents?knowledgeBase=$KB_ENT" "$ALICE" | python3 -c "
import json,sys
items=json.load(sys.stdin)['documents']
cur=[x for x in items if x['uri'].endswith('/policy.md')]
sys.exit(0 if len(cur)==1 and cur[0]['status']=='active' and cur[0]['version']==1 else 1)" || bad "(3): the enterprise policy.md is not one active version 1 after bob's personal upload of the same path"
echo "PASS: PATH P0b — bob's personal kb://me/policy.md did not retire the enterprise $KB_ENT/policy.md: it is still version 1 and active, and carol (a member of no unit) retrieves it through a resolve without any of bob's personal words"

# (4) carol names bob's personal base: its document is 404 to her, the base is not hers to list
call 404 GET "$CMI/v1/knowledge/documents/$B_DOC" "$CAROL" >/dev/null
[[ "$(call 200 GET "$CMI/v1/knowledge/documents?knowledgeBase=$B_BASE" "$CAROL" | field "['documents']")" == "[]" ]] || bad "(4): carol lists documents of bob's personal base"
call 404 GET "$CMI/v1/knowledge/documents/$B_DOC/original" "$CAROL" >/dev/null
echo "PASS: PATH P0c — carol naming bob's personal base ($B_BASE): the document by id and its original are 404 and the base's listing is empty"

# (5) the administrator's default resolve does not contain bob's personal document; a named read does, and is audited as knowledge.read_other
BOB_PKG="$(p0_resolve "$BOB" "$(p0_text "$W_BOB trip plan")")"
[[ "$(echo "$BOB_PKG" | hits "$W_BOB")" -ge 1 ]] || bad "(5): bob does not retrieve his own personal document: $(echo "$BOB_PKG" | head -c 400)"
BOB_PKG_ID="$(echo "$BOB_PKG" | field "['packageId']")"
ADM_PKG="$(p0_resolve "$ALICE" "$(p0_text "$W_BOB trip plan")")"
[[ "$(echo "$ADM_PKG" | hits "$W_BOB")" -eq 0 ]] || bad "(5): an administrator's default resolve contains bob's personal document"
ADM_NAMED="$(p0_resolve "$ALICE" "$(p0_text "$W_BOB trip plan" ",'scope':{'kind':'subject','id':'$BOB_ID'}")")"
[[ "$(echo "$ADM_NAMED" | hits "$W_BOB")" -ge 1 ]] || bad "(5): an administrator naming bob's scope does not read his personal document"
call 404 GET "$CMI/api/v1/knowledge/documents/$B_DOC" "$ALICE" >/dev/null   # idp-cmi D78: the administrator reads another member's personal document by id only by naming the owner (?subject=), so without it the answer is 404
call 200 GET "$CMI/api/v1/knowledge/documents/$B_DOC?subject=$BOB_ID" "$ALICE" >/dev/null
AUD="$(call 200 GET "$CMI/api/v1/audit?limit=200" "$ALICE")"
[[ "$(echo "$AUD" | hits "knowledge.read_other")" -ge 2 ]] || bad "(5): the audit has fewer than two knowledge.read_other entries (the named resolve and the document read): $(echo "$AUD" | head -c 300)"
echo "PASS: PATH P0d — bob retrieves his own personal document; alice's default resolve (enterprise, her units, herself) does not contain it; naming bob's scope or the document id does, and the audit has $(echo "$AUD" | hits "knowledge.read_other") knowledge.read_other entries"

# (6) a version with validFrom in the future is not current until then (and an as-of view in the past does not show it either); as-of cannot name a future time, so this waits for the
# clock: validFrom is 20 s ahead, the "before" resolve must have returned before it (else the run is too slow to test this: said so, not a wrong assertion), and the wait is until validFrom + 2 s
FUT="$(SECONDS_AHEAD 20)"; FUT_EPOCH="$(date -u -d "$FUT" +%s)"
call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"'"$SRC_DOC"'","knowledgeBase":"'"$KB_ENT"'","uri":"kb://'"$KB_ENT"'/rates.md","title":"Rates","text":"# Rates\n\nThe '"$W_RATE"' rate is five percent."}' >/dev/null
call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"'"$SRC_DOC"'","knowledgeBase":"'"$KB_ENT"'","uri":"kb://'"$KB_ENT"'/rates.md","title":"Rates","validFrom":"'"$FUT"'","text":"# Rates\n\nThe '"$W_RATE"' rate is nine percent."}' >/dev/null
NOW_PKG="$(p0_resolve "$CAROL" "$(p0_text "$W_RATE rate percent")")"
T_BEFORE="$(date +%s)"
(( T_BEFORE < FUT_EPOCH )) || bad "(6): too slow to test: the 'before' resolve returned $((T_BEFORE - FUT_EPOCH)) s after validFrom ($FUT); this host is too loaded for a 20 s window"
[[ "$(echo "$NOW_PKG" | hits "$W_RATE rate is five percent")" -ge 1 && "$(echo "$NOW_PKG" | hits "$W_RATE rate is nine percent")" -eq 0 ]] || bad "(6): before validFrom the current version is not the only one retrieved: $(echo "$NOW_PKG" | head -c 500)"
while (( $(date +%s) < FUT_EPOCH + 2 )); do sleep 1; done
LATER_PKG="$(p0_resolve "$CAROL" "$(p0_text "$W_RATE rate percent")")"
[[ "$(echo "$LATER_PKG" | hits "$W_RATE rate is nine percent")" -ge 1 && "$(echo "$LATER_PKG" | hits "$W_RATE rate is five percent")" -eq 0 ]] || bad "(6): after validFrom version 2 is not the only one retrieved: $(echo "$LATER_PKG" | head -c 500)"
PAST_PKG="$(p0_resolve "$CAROL" "$(p0_text "$W_RATE rate percent" ",'asOf':{'valid':'$(SECONDS_AHEAD -3 "$FUT")'}")")"
[[ "$(echo "$PAST_PKG" | hits "$W_RATE rate is five percent")" -ge 1 && "$(echo "$PAST_PKG" | hits "$W_RATE rate is nine percent")" -eq 0 ]] || bad "(6): as of just before validFrom, version 1 is not the one retrieved: $(echo "$PAST_PKG" | head -c 500)"
echo "PASS: PATH P0e — a rates.md version with validFrom $FUT did not retire the current one: before that time only 'five percent' is retrieved, after it only 'nine percent', and an as-of view of 3 s before it shows 'five percent' again"

# (7) an original: upload (bob, his personal document), the digest in the document, download as an attachment with nosniff and a sandboxing CSP, bytes checked, carol cannot
ORIG="$(mktemp)"; python3 -c "print(('Bob original $R line. ' * 90).strip())" > "$ORIG"; SHA="sha256:$(sha256sum "$ORIG" | cut -d' ' -f1)"
UP="$(curl -s --noproxy '*' -o /tmp/p0-up.$R -w '%{http_code}' -X PUT "$CMI/v1/knowledge/documents/$B_DOC/original" -H "Authorization: Bearer $BOB" -H 'Content-Type: text/plain' --data-binary @"$ORIG")"
[[ "$UP" == 200 ]] || bad "(7): the upload of the original was $UP: $(cat /tmp/p0-up.$R)"
[[ "$(field "['original']['sha256']" < /tmp/p0-up.$R)" == "$SHA" ]] || bad "(7): the document's original digest is not the file's: $(cat /tmp/p0-up.$R)"
rm -f /tmp/p0-up.$R
DL_H="$(mktemp)"; DL_B="$(mktemp)"
DL="$(curl -s --noproxy '*' -D "$DL_H" -o "$DL_B" -w '%{http_code}' "$CMI/v1/knowledge/documents/$B_DOC/original" -H "Authorization: Bearer $BOB")"
[[ "$DL" == 200 ]] || bad "(7): the download was $DL"
grep -qi '^content-disposition: attachment' "$DL_H" && grep -qi '^x-content-type-options: nosniff' "$DL_H" && grep -qi '^content-security-policy: sandbox' "$DL_H" || bad "(7): the download headers are not attachment + nosniff + CSP sandbox: $(tr -d '\r' < "$DL_H" | tr '\n' ';')"
[[ "sha256:$(sha256sum "$DL_B" | cut -d' ' -f1)" == "$SHA" ]] || bad "(7): the downloaded bytes are not the uploaded ones"
call 404 GET "$CMI/v1/knowledge/documents/$B_DOC/original" "$CAROL" >/dev/null
KEYS_BEFORE="$(p0_s3_keys | grep -c "/$B_DOC/" || true)"
[[ "$KEYS_BEFORE" -ge 1 ]] || bad "(7): the originals bucket has no object under bob's document $B_DOC"
rm -f "$ORIG" "$DL_H" "$DL_B"
echo "PASS: PATH P0f — bob's original ($SHA, text/plain) uploaded to his personal document and downloaded again: 200, Content-Disposition attachment, X-Content-Type-Options nosniff, Content-Security-Policy sandbox, the bytes hash to the same digest; carol gets 404; the bucket holds $KEYS_BEFORE object(s) under the document"

# (8) the erasure of bob as a subject: personal documents, originals, uris and the query text of his packages are gone, and the erasure is verified
ER="$(call 201 POST "$CMI/api/v1/erasures" "$ALICE" '{"subject":"'"$BOB_ID"'","reason":"P0: the member left"}')"
ER_ID="$(echo "$ER" | field "['erasureId']")"
ER_STATE=""
for _ in $(seq 1 60); do
  ER_STATE="$(call 200 GET "$CMI/api/v1/erasures/$ER_ID" "$ALICE" | field "['status']")"
  [[ "$ER_STATE" == verified || "$ER_STATE" == failed ]] && break; sleep 1
done
[[ "$ER_STATE" == verified ]] || bad "(8): the erasure $ER_ID is $ER_STATE, not verified: $(call 200 GET "$CMI/api/v1/erasures/$ER_ID" "$ALICE" | head -c 600)"
call 404 GET "$CMI/v1/knowledge/documents/$B_DOC" "$BOB" >/dev/null || bad "(8): bob still reads his erased personal document"
AFTER="$(p0_resolve "$BOB" "$(p0_text "$W_BOB trip plan")")"
[[ "$(echo "$AFTER" | hits "$W_BOB")" -eq 0 ]] || bad "(8): bob's resolve still returns his erased personal document"
KEYS_AFTER="$(p0_s3_keys | grep -c "/$B_DOC/" || true)"
[[ "$KEYS_AFTER" -eq 0 ]] || bad "(8): $KEYS_AFTER object(s) of the erased document are still in the originals bucket"
# the document of an erased member is 200 with nothing of his in it or, since idp-cmi D78, 404 (`call` ends its own subshell with exit 1 on another status, so `|| true` would not catch it: curl, and look at the body)
ADM_DOC="$(curl -s --noproxy '*' "$CMI/api/v1/knowledge/documents/$B_DOC?subject=$BOB_ID" -H "Authorization: Bearer $ALICE" || true)"
[[ -z "$ADM_DOC" || ("$(echo "$ADM_DOC" | hits "$B_BASE/policy.md")" -eq 0 && "$(echo "$ADM_DOC" | hits "$W_BOB")" -eq 0) ]] || bad "(8): the erased document still carries bob's uri or text: $ADM_DOC"
PK="$(curl -s --noproxy '*' "$CMI/v1/context/packages/$BOB_PKG_ID" -H "Authorization: Bearer $ALICE")"
[[ "$(echo "$PK" | rawhits "$W_BOB")" -eq 0 ]] || bad "(8): bob's package $BOB_PKG_ID still has his query text: $(echo "$PK" | head -c 400)"
[[ "$(call 200 GET "$CMI/api/v1/audit?limit=500" "$ALICE" | hits "package.read_other")" -ge 1 ]] || bad "(8): the administrator's read of bob's package left no package.read_other in the audit (idp-cmi D78)"
CAROL_AFTER="$(p0_resolve "$CAROL" "$(p0_text "$W_ENT trips economy")")"
[[ "$(echo "$CAROL_AFTER" | hits "enterprise rule for")" -ge 1 ]] || bad "(8): the enterprise document is gone for carol after bob's erasure"
echo "PASS: PATH P0g — the erasure of bob ($ER_ID) is verified: his personal document is 404 to him and carries neither his uri nor his words, his resolve finds nothing of it, the originals bucket has no object under it (was $KEYS_BEFORE), his package $BOB_PKG_ID no longer has his query text, and carol still retrieves the enterprise document"
