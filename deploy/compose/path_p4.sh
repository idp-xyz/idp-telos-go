# PATH P4 (unified citation numbering; idp-host ADR-0001 D70 to D85, contract idp.telos.host 1.13.0; idp-web D34), shared by deploy/compose/e2e.sh (compose only: no manifest, network policy or
# sandbox runtime is involved), which `source`s it after PATH P3. Real Keycloak tokens, the real HOST, CMI, gateway and idp-web (the fixture search provider and the test origin of the M paths), the HOST's
# model double: its `mixed:` / `mixed-cite:` flow (a knowledge search, then a web search and a read of the first result, then an answer that cites one number of each kind, `mixed-cite:` and one nobody
# gave), its `fan:` flow (the parent calls the offered sub-agent tool once for each `;;`-separated input, in one step) and its `web:` flow, which cites the number of the entry it read (the HOST's).
# The member is dave (ws-member, host-user, cmi-reader: nothing else erases him; P0 and P2 erase bob and carol). What the caller supplies: CMI, HOST, TENANT, `user_token NAME`, `call EXPECTED METHOD
# URL TOKEN [BODY]`, `field`, `p4_hostsql SQL` (psql on the HOST's database), `${C[@]}` (compose), L. Names carry a random suffix; the words searched for are made of it.
# The tenant may search three times a day: the count is zeroed before the path and after it (as the paths of the web search do), and the path makes three searches.
set -u
R4="$(openssl rand -hex 3)"; P4_SRC="p4-docs-$R4"; P4_KB="p4-kb-$R4"
p4w() { echo "$1${R4//[0-9]/x}"; }
WK1="$(p4w ledgerone)"; WK2="$(p4w binderytwo)"
p4_bad() { echo "FAIL: PATH P4 $*" >&2; exit 1; }
p4_hostsql() { "${C[@]}" exec -T postgres psql -U postgres -d idp_host -Atc "$1"; }
p4_websql() { "${C[@]}" exec -T postgres psql -U postgres -d idp_web -Atc "$1"; }
P4_TOK_AT=0
p4_fresh() { local now; now=$(date +%s); (( now - P4_TOK_AT > 240 )) && { ALICE4="$(user_token alice)"; DAVE4="$(user_token dave)"; HADMIN4="$(user_token hostadmin)"; HAPPROVER4="$(user_token hostapprover)"; P4_TOK_AT=$now; }; return 0; }
p4_fresh
p4_websql "DELETE FROM daily_usage WHERE tenant = '$TENANT'" >/dev/null   # the paths of the web search zero the count as an operator may; this path makes three searches of the three
Q4="how many parcels did the harbour depot sort in october"                      # the fixture search provider finds the article for it (PATH M); the knowledge passage below says the same words
TQ4="what does the carrier tariff pdf say about the fuel surcharge for zone 3"   # ... and the tariff PDF for this one (PATH M4)

# the knowledge: one source, one base, two documents. The first has the question's words (so that the double's embedding finds it) and a text that tries to look like citations
call 201 POST "$CMI/api/v1/sources" "$ALICE4" '{"name":"'"$P4_SRC"'","displayName":"P4 documents","kind":"document-store","trust":"authoritative"}' >/dev/null
call 201 POST "$CMI/api/v1/knowledge/bases" "$ALICE4" '{"name":"'"$P4_KB"'","displayName":"'"$P4_KB"'","scope":{"kind":"tenant"},"managers":{"subjects":[],"roles":[]},"defaultSensitivity":"internal","sensitivityCeiling":"confidential","source":"'"$P4_SRC"'","quotaDocuments":0,"quotaBytes":0,"ocr":false}' >/dev/null
p4_doc() { call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE4" "$(python3 -c "import json,sys; print(json.dumps({'source': sys.argv[1], 'knowledgeBase': sys.argv[2], 'uri': 'kb://'+sys.argv[2]+'/'+sys.argv[3]+'.md', 'title': sys.argv[4], 'sensitivity': 'internal', 'text': sys.argv[5]}))" "$P4_SRC" "$P4_KB" "$1" "$2" "$3")" | field "['documentId']"; }
DOC1="$(p4_doc dock-book "Dock book" "The dock book says how many parcels did the harbour depot sort in october: see the $WK1 marker. An older note of the dock book says see [2] and [987] for the totals.")"
DOC2="$(p4_doc second-binder "Second binder" "The second binder keeps the $WK2 marker on its only page.")"

# the agents (four eyes). CTX: the knowledge tools of the HOST (the member's own token) and the context of the first package. The gateway tools are the web's, through the gateway as the member.
CTX4='"context":{"cmi":true,"purpose":"assist","maxItems":5,"kinds":["knowledge"],"tools":["knowledge__search","knowledge__read","knowledge__find"]}'
MODEL4='"model":{"provider":"local","model":"fake-1"}'
p4_agent() { # p4_agent NAME DEFINITION-JSON
  local rel
  call 201 POST "$HOST/api/v1/agents" "$HADMIN4" '{"name":"'"$1"'","displayName":"'"$1"'","definition":'"$2"'}' >/dev/null
  rel="$(call 201 POST "$HOST/api/v1/agents/$1/releases" "$HADMIN4" | field "['releaseId']")"
  call 200 POST "$HOST/api/v1/releases/$rel/approve" "$HAPPROVER4" >/dev/null
}
A_MIX="p4-mix-$R4"; A_KID="p4-kid-$R4"; A_LEAD="p4-lead-$R4"
p4_agent "$A_MIX" '{"instructions":"Answer from the knowledge base and the web and cite each source as [n].",'"$MODEL4"',"tools":{"gateway":"edge","allow":["web__web_search","web__web_fetch"]},"confirmation":{"default":"auto"},'"$CTX4"'}'
# the sub-agent reads the web; its page read waits for the member, so that a check can look at the database while the run tree is alive
p4_agent "$A_KID" '{"instructions":"Read the web and cite each source as [n].",'"$MODEL4"',"tools":{"gateway":"edge","allow":["web__web_search","web__web_fetch"]},"confirmation":{"default":"auto","tools":{"web__web_fetch":"confirm"}},"limits":{"confirmationTimeoutSeconds":900}}'
p4_agent "$A_LEAD" '{"instructions":"Delegate and cite what the sub-agents found as [n].",'"$MODEL4"',"tools":{"gateway":"edge","allow":["web__web_search"]},"confirmation":{"default":"auto"},"subagents":[{"name":"'"$A_KID"'","description":"Reads the web."}],'"$CTX4"'}'

p4_start() { # p4_start AGENT TOKEN THREAD RUN MESSAGE: a run in a thread (the thread is new, or the one that earlier runs wrote into)
  local body
  body="$(python3 -c "import json,sys; print(json.dumps({'threadId':sys.argv[1],'runId':sys.argv[2],'messages':[{'id':'m-'+sys.argv[2],'role':'user','content':sys.argv[3]}],'tools':[],'context':[]}))" "$3" "$4" "$5")"
  call 202 POST "$HOST/v1/agents/$1/runs" "$2" "$body" >/dev/null
}
p4_status() { call 200 GET "$HOST/v1/runs/$1" "$DAVE4" | field "['status']"; }
p4_wait() { # p4_wait RUN: until the run has ended (bounded, from the clock); it must be COMPLETED
  local t0 st; t0=$(date +%s)
  while :; do
    st="$(p4_status "$1")"
    [[ "$st" == COMPLETED || "$st" == FAILED || "$st" == CANCELLED ]] && break
    (( $(date +%s) - t0 > 120 )) && p4_bad "the run $1 is '$st' after 120 s"; sleep 1
  done
  [[ "$st" == COMPLETED ]] || p4_bad "the run $1 ended '$st'"
}
p4_save() { # p4_save RUN: the run and its events as files in $L
  call 200 GET "$HOST/v1/runs/$1" "$DAVE4" > "$L/p4-$1.json"; call 200 GET "$HOST/v1/runs/$1/events?limit=500" "$DAVE4" > "$L/p4-$1-events.json"
}
p4_next() { p4_hostsql "SELECT next FROM citation_counters WHERE thread_id = '$1'"; }   # the thread's counter: the number the next source gets

# (a) one run with a knowledge search and a web search: the numbers go on from one to the other and the lists are the two kinds' own; an answer citing one of each marks both; an invented number is unverified.
# Then a follow-up run of the same thread goes on from where the first stopped.
p4_fresh
T_MIX="t4-$R4-mix"; RM1="r4-$R4-mix1"; RM2="r4-$R4-mix2"
p4_start "$A_MIX" "$DAVE4" "$T_MIX" "$RM1" "mixed-cite: $WK1 $Q4"; p4_wait "$RM1"; p4_save "$RM1"   # the marker word is what the double's embedding finds the passage by (one unusual word shared); the web search ignores it
NEXT1="$(p4_next "$T_MIX")"; [[ "$NEXT1" =~ ^[0-9]+$ ]] || p4_bad "(a): the thread $T_MIX has no citation counter ('$NEXT1')"
A_SUM="$(python3 -I deploy/compose/cite_check.py mixed "$L/p4-$RM1.json" "$L/p4-$RM1-events.json" "$NEXT1")" || p4_bad "(a): the mixed run $RM1 (above)"
echo "PASS: PATH P4a — a run of $A_MIX ($RM1, mixed-cite:) searched the knowledge base and the web: $A_SUM; sources holds the web results only (url, webN) and knowledgeSources the knowledge passages, in one counter without a hole"
p4_fresh
p4_start "$A_MIX" "$DAVE4" "$T_MIX" "$RM2" "know: $WK2"; p4_wait "$RM2"; p4_save "$RM2"
NEXT2="$(p4_next "$T_MIX")"
A_FU="$(python3 -I deploy/compose/cite_check.py followup "$L/p4-$RM2.json" "$L/p4-$RM2-events.json" "$NEXT1" "$NEXT2" "$L/p4-$RM1.json" "$DOC2")" || p4_bad "(a): the follow-up run $RM2 (above)"
echo "PASS: PATH P4b — a follow-up run ($RM2) in the same thread: $A_FU; numbers of the first run are still one thing each"

# (b) a run that delegates to two sub-agents (one sub-agent release, called twice: each reads another page of the web and cites it). While the tree is alive (each sub-agent's page read waits for dave)
# the database says the sub-agents hold no gateway credential of their own: credential_ref points at the root, which holds it. After the run nothing is held, the numbers are the thread's own and the counter is one past the highest.
p4_fresh
T_FAN="t4-$R4-fan"; RL="r4-$R4-lead"
p4_start "$A_LEAD" "$DAVE4" "$T_FAN" "$RL" "fan: web: $Q4 ;; web: $TQ4"
KIDS=""; t0=$(date +%s)
while :; do   # both sub-agents exist and each waits for the member's confirmation
  KIDS="$(call 200 GET "$HOST/v1/runs/$RL/subruns" "$DAVE4" | python3 -c "import json,sys; print(' '.join(r['runId'] for r in json.load(sys.stdin)['runs']))")"
  read -ra KARR <<< "$KIDS"; ok=0
  if (( ${#KARR[@]} == 2 )); then
    ok=1
    for k in "${KARR[@]}"; do [[ "$(call 200 GET "$HOST/v1/runs/$k/tool-calls" "$DAVE4" | python3 -c "import json,sys; print(sum(1 for c in json.load(sys.stdin)['toolCalls'] if c['state']=='PENDING_CONFIRMATION'))")" == 1 ]] || ok=0; done
  fi
  (( ok )) && break
  (( $(date +%s) - t0 > 120 )) && p4_bad "(b): after 120 s the sub-agents of $RL are '$KIDS' and do not both wait for confirmation (run $(p4_status "$RL"))"; sleep 1
done
KID1="${KARR[0]}"; KID2="${KARR[1]}"
HELD="$(p4_hostsql "SELECT id || ' ' || coalesce(credential_ref, '-') || ' ' || (credential IS NULL OR octet_length(credential) = 0) || ' ' || coalesce(parent_run_id, '-') FROM runs WHERE id IN ('$RL', '$KID1', '$KID2') ORDER BY depth, id")"
python3 - "$HELD" "$RL" "$KID1" "$KID2" <<'PY' || p4_bad "(b): who holds the gateway credential while the run tree is alive (above)"
import sys
rows=[l.split(' ') for l in sys.argv[1].strip().splitlines()]; root,k1,k2=sys.argv[2:5]
by={r[0]:r for r in rows}
assert set(by)=={root,k1,k2},('rows',rows)
assert by[root][2]=='false' and by[root][1]=='-',('the root holds the credential itself',by[root])
for k in (k1,k2):
    assert by[k][1]==root and by[k][2]=='true' and by[k][3]==root,('a sub-agent holds a copy or points elsewhere',by[k])
print('ok')
PY
for k in "$KID1" "$KID2"; do
  CALL="$(call 200 GET "$HOST/v1/runs/$k/tool-calls" "$DAVE4" | python3 -c "import json,sys; print([c['toolCallId'] for c in json.load(sys.stdin)['toolCalls'] if c['state']=='PENDING_CONFIRMATION'][0])")"
  call 200 POST "$HOST/v1/runs/$k/tool-calls/$CALL/decide" "$DAVE4" '{"decision":"confirm"}' >/dev/null
done
p4_wait "$RL"; p4_save "$RL"; p4_save "$KID1"; p4_save "$KID2"
NEXT3="$(p4_next "$T_FAN")"; [[ "$NEXT3" =~ ^[0-9]+$ ]] || p4_bad "(b): the thread $T_FAN has no citation counter ('$NEXT3')"
NONE="$(p4_hostsql "SELECT count(*) FROM runs WHERE id IN ('$RL', '$KID1', '$KID2') AND (credential IS NOT NULL OR credential_ref IS NOT NULL OR cmi_credential IS NOT NULL OR cmi_credential_ref IS NOT NULL)")"
[[ "$NONE" == 0 ]] || p4_bad "(b): $NONE of the three runs still hold or point at a credential after the run ended"
A_TREE="$(python3 -I deploy/compose/cite_check.py tree "$L/p4-$RL.json" "$L/p4-$RL-events.json" "$NEXT3" "$L/p4-$KID1.json" "$L/p4-$KID2.json")" || p4_bad "(b): the numbers of the run tree $RL (above)"
echo "PASS: PATH P4c — $A_LEAD ($RL) delegated twice to $A_KID ($KID1, $KID2): while they waited for dave the database had the sub-agents' credential_ref = the root and no credential of their own (the root held it), after the run none of the three held or pointed at one; $A_TREE"
p4_websql "DELETE FROM daily_usage WHERE tenant = '$TENANT'" >/dev/null
