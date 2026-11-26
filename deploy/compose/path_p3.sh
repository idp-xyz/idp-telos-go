# PATH P3 (platform plan knowledge-base-plan.md, KB8: the HOST's knowledge tools and the question rewrite; idp-host ADR-0001 D65 to D69, idp-cmi ADR-0001 D71 to D78; docs/adr/kb8-cmi-api.md), shared by deploy/compose/e2e.sh and
# tests/kind/full.sh, which `source` it after PATH P2. Real Keycloak tokens, the real HOST and CMI, the HOST's model double (its knowledge flows `know:`, `know-dig:`, `know-read:`, its `call:` flow, and its question rewrite: a
# deterministic model, no model call to anything else). The members are dave and erin (ws-member, host-user, cmi-reader: nothing else uses them, because P0 and P2 erase bob and carol) and alice (ws-admin, cmi-admin).
# What the caller supplies: CMI, HOST, `user_token NAME`, `call EXPECTED METHOD URL TOKEN [BODY]`, `field`, `p1_sql SQL` (psql on the CMI's database), `p3_hostsql SQL` (psql on the HOST's), L, and for the HOST's agents the
# tokens hostadmin and hostapprover (four eyes). Names carry a random suffix; the words searched for are made of it.
# The HOST keeps a member's CMI token in `runs.cmi_credential` only while a run that has the tools is running and only for the roles IDP_HOST_MEMBER_ROLES names (ws-member, ws-admin); the tools never use HOST's own credential.
set -u
R3="$(openssl rand -hex 3)"; P3_SRC="p3-docs-$R3"; P3_SRC2="p3-other-$R3"; P3_KB="p3-kb-$R3"; P3_KB2="p3-kb2-$R3"; P3_USER_SRC="p3-notes"
p3w() { echo "$1${R3//[0-9]/x}"; }
W_P1="$(p3w zorblat)"; W_P2="$(p3w quokkaz)"; W_P3="$(p3w wombatt)"; W_PHRASE="violet harbour lantern $(p3w sparrow)"; W_B="$(p3w otherdoc)"; W_C="$(p3w ledgerr)"; W_DAVE="$(p3w davenote)"; W_ERIN="$(p3w erinnote)"; W_MISS="$(p3w nothere)"
p3_bad() { echo "FAIL: PATH P3 $*" >&2; exit 1; }
P3_TOK_AT=0; : > "$L/p3-runs.txt"   # the ids of this path's runs (p3_run runs in a subshell: a variable would not keep them)
p3_fresh() { local now; now=$(date +%s); (( now - P3_TOK_AT > 240 )) && { ALICE3="$(user_token alice)"; DAVE="$(user_token dave)"; ERIN="$(user_token erin)"; HADMIN3="$(user_token hostadmin)"; HAPPROVER3="$(user_token hostapprover)"; P3_TOK_AT=$now; }; return 0; }
p3_fresh
sub_of() { python3 -c "import base64,json,sys; t=sys.argv[1].split('.')[1]; print(json.loads(base64.urlsafe_b64decode(t+'='*(-len(t)%4)))['sub'])" "$1"; }
DAVE_ID="$(sub_of "$DAVE")"; ERIN_ID="$(sub_of "$ERIN")"
# the knowledge: two sources, a base for each, the handbook (three pages, markers on each), a document of the other source, a confidential one, and a personal note of each member
call 201 POST "$CMI/api/v1/sources" "$ALICE3" '{"name":"'"$P3_SRC"'","displayName":"P3 documents","kind":"document-store","trust":"authoritative"}' >/dev/null
call 201 POST "$CMI/api/v1/sources" "$ALICE3" '{"name":"'"$P3_SRC2"'","displayName":"P3 other source","kind":"document-store","trust":"authoritative"}' >/dev/null
curl -s --noproxy '*' -o /dev/null -X POST "$CMI/api/v1/sources" -H "Authorization: Bearer $ALICE3" -H 'Content-Type: application/json' --data '{"name":"'"$P3_USER_SRC"'","displayName":"P3 members notes","kind":"user","trust":"informational"}'   # 201, or 409 on a rerun
p3_kb() { call 201 POST "$CMI/api/v1/knowledge/bases" "$ALICE3" '{"name":"'"$1"'","displayName":"'"$1"'","scope":{"kind":"tenant"},"managers":{"subjects":[],"roles":[]},"defaultSensitivity":"internal","sensitivityCeiling":"confidential","source":"'"$2"'","quotaDocuments":0,"quotaBytes":0,"ocr":false}' >/dev/null; }
p3_kb "$P3_KB" "$P3_SRC"; p3_kb "$P3_KB2" "$P3_SRC2"
p3_doc() { # p3_doc TOKEN ROUTE JSON-BODY: prints the document id (ROUTE: /api/v1 for the administrator, /v1 for a member)
  call 201 POST "$CMI$2/knowledge/documents" "$1" "$3" | field "['documentId']"; }
P3_TEXT_A="<!-- page: 1 -->\nPage one of the dock rota. The $W_P1 marker sits here and the $W_PHRASE is lit at dusk.\n<!-- page: 2 -->\nPage two of the dock rota. The $W_P2 marker sits here, with the second shift.\n<!-- page: 3 -->\nPage three of the dock rota. The $W_P3 marker sits here, with the third shift."
DOC_A="$(p3_doc "$ALICE3" /api/v1 '{"source":"'"$P3_SRC"'","knowledgeBase":"'"$P3_KB"'","uri":"kb://'"$P3_KB"'/rota.md","title":"Dock rota","sensitivity":"internal","text":"'"$P3_TEXT_A"'"}')"
DOC_B="$(p3_doc "$ALICE3" /api/v1 '{"source":"'"$P3_SRC2"'","knowledgeBase":"'"$P3_KB2"'","uri":"kb://'"$P3_KB2"'/other.md","title":"Other source","sensitivity":"internal","text":"Another source says the '"$W_B"' marker is on its only page."}')"
DOC_C="$(p3_doc "$ALICE3" /api/v1 '{"source":"'"$P3_SRC"'","knowledgeBase":"'"$P3_KB"'","uri":"kb://'"$P3_KB"'/ledger.md","title":"Ledger","sensitivity":"confidential","text":"The confidential ledger names the '"$W_C"' marker and a sum that only the enterprise may see."}')"
DOC_DAVE="$(p3_doc "$DAVE" /v1 '{"source":"'"$P3_USER_SRC"'","uri":"kb://me/dave-'"$R3"'.md","title":"Dave note","text":"Dave keeps the '"$W_DAVE"' note in his own base."}')"
DOC_ERIN="$(p3_doc "$ERIN" /v1 '{"source":"'"$P3_USER_SRC"'","uri":"kb://me/erin-'"$R3"'.md","title":"Erin note","text":"Erin keeps the '"$W_ERIN"' note in her own base."}')"
# the HOST's agents (four eyes): the model is the double (`local`, the enterprise's own) or its twin `local-ext` (not declared the enterprise's own: an external model); every one has the three tools except where said
p3_agent() { # p3_agent NAME PROVIDER CONTEXT-JSON-EXTRAS
  local def rel
  def='{"name":"'"$1"'","displayName":"'"$1"'","definition":{"instructions":"Answer from the passages you are given and cite them as [n].","model":{"provider":"'"$2"'","model":"fake-1"},"tools":{},"confirmation":{"default":"auto"},"context":{"cmi":true,"purpose":"assist","maxItems":5,"kinds":["knowledge"]'"$3"'}}}'
  call 201 POST "$HOST/api/v1/agents" "$HADMIN3" "$def" >/dev/null
  rel="$(call 201 POST "$HOST/api/v1/agents/$1/releases" "$HADMIN3" | field "['releaseId']")"
  call 200 POST "$HOST/api/v1/releases/$rel/approve" "$HAPPROVER3" >/dev/null
}
TOOLS3='"tools":["knowledge__search","knowledge__read","knowledge__find"]'
A_KNOW="p3-know-$R3"; A_OFF="p3-know-off-$R3"; A_SRC="p3-know-src-$R3"; A_EXT="p3-know-ext-$R3"; A_PLAIN="p3-plain-$R3"
p3_agent "$A_KNOW" local ",$TOOLS3,\"rewrite\":\"model\""
p3_agent "$A_OFF" local ",$TOOLS3"
p3_agent "$A_SRC" local ",$TOOLS3,\"sources\":[\"$P3_SRC\"]"
p3_agent "$A_EXT" local-ext ",$TOOLS3"
p3_agent "$A_PLAIN" local ""   # no tools: an agent as before KB8
# p3_run AGENT TOKEN MESSAGES-JSON: a run; waits (bounded, from the clock) until it has ended; prints its id; the events of the run are in $L/p3-<id>.json as a page of the member's own
p3_run() {
  local id="r3-$R3-$(openssl rand -hex 3)" t0 st body
  body="$(python3 -c "import json,sys; print(json.dumps({'threadId':'t'+sys.argv[1],'runId':sys.argv[1],'messages':json.loads(sys.argv[2]),'tools':[],'context':[]}))" "$id" "$3")"
  call 202 POST "$HOST/v1/agents/$1/runs" "$2" "$body" >/dev/null; echo "$id" >> "$L/p3-runs.txt"; t0=$(date +%s)
  while :; do
    st="$(call 200 GET "$HOST/v1/runs/$id" "$2" | field "['status']")"
    [[ "$st" == COMPLETED || "$st" == FAILED || "$st" == CANCELLED ]] && break
    (( $(date +%s) - t0 > 120 )) && p3_bad "the run $id is '$st' after 120 s"; sleep 1
  done
  [[ "$st" == COMPLETED ]] || p3_bad "the run $id ended '$st'"
  call 200 GET "$HOST/v1/runs/$id/events?limit=500" "$2" > "$L/p3-$id.json"; echo "$id"
}
p3_msgs() { python3 -c "import json,sys; print(json.dumps([{'id':'m%d'%i,'role':r,'content':c} for i,(r,c) in enumerate(zip(sys.argv[1::2], sys.argv[2::2]))]))" "$@"; }
# p3_ev RUN: the run's events, normalised: {customs: [{name, value}], results: [text of each tool result, in order], text: the assistant's answer}
p3_ev() { python3 -c "
import json,sys
out={'customs':[],'results':[],'text':''}
for x in json.load(open(sys.argv[1]))['events']:
    e=x.get('event',x)
    if e['type']=='CUSTOM': out['customs'].append({'name':e['name'],'value':e.get('value')})
    elif e['type']=='TOOL_CALL_RESULT': out['results'].append(e.get('content',''))
    elif e['type']=='TEXT_MESSAGE_CONTENT': out['text']+=e.get('delta','')
print(json.dumps(out))" "$L/p3-$1.json"; }
# p3_x JSON PYTHON-EXPR: evaluates the expression with d = the JSON
p3_x() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$1"; }
p3_named() { p3_x "$1" "json.dumps([c['value'] for c in d['customs'] if c['name']=='$2'])"; }   # the values of the events of that name, as a JSON list
p3_cmi_package() { call 200 GET "$CMI/v1/context/packages/$1" "$2"; }

# (a) dave's run searches and then finds a phrase in the document of the first result; another reads a page; each call is a package of its own, the numbers continue from the package of the start, and the CMI knows the member
RA1="$(p3_run "$A_KNOW" "$DAVE" "$(p3_msgs user "know-dig: $W_P1 | $W_PHRASE")")"; EA1="$(p3_ev "$RA1")"
RA2="$(p3_run "$A_KNOW" "$DAVE" "$(p3_msgs user "know-read: $W_P2 | 2")")"; EA2="$(p3_ev "$RA2")"
python3 - "$EA1" "$EA2" "$DOC_A" "$W_PHRASE" <<'PY' || p3_bad "(a): the events of the runs are not as the tools say (above)"
import json,sys
e1,e2,doc,phrase=json.loads(sys.argv[1]),json.loads(sys.argv[2]),sys.argv[3],sys.argv[4]
def seq(e):
    out=[]
    for c in e['customs']:
        if c['name'] in ('idp.host/context.resolved','idp.host/context.package'): out.append((c['name'].split('/')[1],c['value']))
    return out
s1,s2=seq(e1),seq(e2)
assert [n for n,_ in s1]==['context.resolved','context.package','context.package'],s1
assert [v.get('tool') for n,v in s1[1:]]==['knowledge__search','knowledge__find'],s1
assert [v.get('tool') for n,v in s2[1:]]==['knowledge__search','knowledge__read'],s2
for s in (s1,s2):
    nxt=s[0][1]['firstIndex']
    for n,v in s:
        assert v['firstIndex']==nxt,('the numbers do not continue',s)
        nxt+=v.get('numbered',v.get('returned'))
assert s1[2][1].get('documentId')==doc and s1[2][1].get('text')==phrase,s1[2]
assert s2[2][1].get('documentId')==doc and s2[2][1].get('locator')=={'page':2},s2[2]
nums=[int(x) for x in __import__('re').findall(r'\[(\d+)\]',e1['text'])]
assert nums and min(nums)>=s1[1][1]['firstIndex'] and max(nums)<nxt,('the answer cites numbers that were not given',e1['text'],s1)
print('ok')
PY
PKG_FIND="$(p3_x "$EA1" "[c['value']['packageId'] for c in d['customs'] if c['name']=='idp.host/context.package'][-1]")"
PKG_READ="$(p3_x "$EA2" "[c['value']['packageId'] for c in d['customs'] if c['name']=='idp.host/context.package'][-1]")"
PKG_SEARCH="$(p3_x "$EA1" "[c['value']['packageId'] for c in d['customs'] if c['name']=='idp.host/context.package'][0]")"
PF="$(p3_cmi_package "$PKG_FIND" "$DAVE")"; PR="$(p3_cmi_package "$PKG_READ" "$DAVE")"; PS="$(p3_cmi_package "$PKG_SEARCH" "$DAVE")"
python3 - "$PF" "$PR" "$PS" "$DAVE_ID" "$W_PHRASE" "$W_P2" <<'PY' || p3_bad "(a): what the CMI has of the packages is not as the HOST's events say (above)"
import json,sys
pf,pr,ps,dave,phrase,w2=[json.loads(x) for x in sys.argv[1:4]]+[sys.argv[4],sys.argv[5],sys.argv[6]]
for name,p in (('find',pf),('read',pr),('search',ps)):
    assert p['package']['requestedBy']==dave,(name,'the CMI sees',p['package']['requestedBy'],'not the member',dave)
it=pf['package']['items']; assert it and it[0]['match']=='exact' and it[0].get('hits') and phrase in it[0]['excerpt'] and it[0]['locator'].get('page')==1,it
it=pr['package']['items']; assert any(i['locator'].get('page')==2 and w2 in i['excerpt'] for i in it),it   # the chunks around it (the neighbours, in document order) come with it
print('ok')
PY
echo "PASS: PATH P3a — dave's runs ($RA1, $RA2): the first context package at the start, then one idp.host/context.package event per tool call (knowledge__search then knowledge__find of the phrase; knowledge__search then knowledge__read of page 2), the citation numbers continue from the start's ($(p3_x "$EA1" "[c['value']['firstIndex'] for c in d['customs'] if c['name'] in ('idp.host/context.resolved','idp.host/context.package')]")) and the answer cites numbers that were given; the CMI has the three packages as requested by dave ($DAVE_ID), not by HOST's service; the find is match exact with its hits on page 1, the read has page 2 (and its neighbours)"

p3_fresh
# (b) a member cannot read another member's personal base through the tools; an administrator cannot either (HOST never names the owner); the administrator naming the owner at the CMI can, and it is audited
p3_call() { p3_msgs user "call: $1 $2"; }   # the double calls the offered tool `$1` with exactly these arguments
RB1="$(p3_run "$A_KNOW" "$DAVE" "$(p3_call knowledge__read '{"documentId":"'"$DOC_ERIN"'"}')")"; EB1="$(p3_ev "$RB1")"
RB2="$(p3_run "$A_KNOW" "$DAVE" "$(p3_call knowledge__read '{"documentId":"'"$DOC_DAVE"'"}')")"; EB2="$(p3_ev "$RB2")"
RB3="$(p3_run "$A_KNOW" "$ALICE3" "$(p3_call knowledge__read '{"documentId":"'"$DOC_DAVE"'"}')")"; EB3="$(p3_ev "$RB3")"
RB4="$(p3_run "$A_KNOW" "$ALICE3" "$(p3_call knowledge__find '{"documentId":"'"$DOC_DAVE"'","text":"'"$W_DAVE"'"}')")"; EB4="$(p3_ev "$RB4")"
[[ "$(p3_x "$EB1" "'not found or not visible' in ' '.join(d['results'])")" == True && "$(p3_x "$EB1" "'$W_ERIN' in json.dumps(d)")" == False ]] || p3_bad "(b): dave reading erin's personal document through the tool: $(echo "$EB1" | head -c 600)"
[[ "$(p3_x "$EB2" "'$W_DAVE' in ' '.join(d['results'])")" == True ]] || p3_bad "(b): dave cannot read his own personal document through the tool: $(echo "$EB2" | head -c 600)"
for e in "$EB3" "$EB4"; do [[ "$(p3_x "$e" "'not found or not visible' in ' '.join(d['results'])")" == True && "$(p3_x "$e" "'$W_DAVE' in ' '.join(d['results'])")" == False ]] || p3_bad "(b): alice (ws-admin) reading dave's personal document through HOST's tools: $(echo "$e" | head -c 600)"; done
AUD0="$(call 200 GET "$CMI/api/v1/audit?limit=500" "$ALICE3" | python3 -c "import json,sys; print(sum(1 for e in json.load(sys.stdin).get('events',[]) if e.get('action')=='knowledge.read_other'))")"
call 404 POST "$CMI/v1/context/read" "$ALICE3" '{"documentId":"'"$DOC_DAVE"'","purpose":"assist"}' >/dev/null
OK_READ="$(call 200 POST "$CMI/v1/context/read" "$ALICE3" '{"documentId":"'"$DOC_DAVE"'","purpose":"assist","scope":{"kind":"subject","id":"'"$DAVE_ID"'"}}')"
[[ "$(echo "$OK_READ" | python3 -c "import json,sys; print('$W_DAVE' in json.dumps(json.load(sys.stdin)['package']['items']))")" == True ]] || p3_bad "(b): alice naming dave's base at the CMI did not read the document: $(echo "$OK_READ" | head -c 300)"
AUD1="$(call 200 GET "$CMI/api/v1/audit?limit=500" "$ALICE3" | python3 -c "import json,sys; print(sum(1 for e in json.load(sys.stdin).get('events',[]) if e.get('action')=='knowledge.read_other'))")"
[[ "$AUD1" -gt "$AUD0" ]] || p3_bad "(b): the read of dave's base by alice naming it left no knowledge.read_other in the audit ($AUD0 -> $AUD1)"
echo "PASS: PATH P3b — through the HOST's tools dave reads his own personal note but erin's is 'not found or not visible', and alice (ws-admin) gets the same for dave's note with knowledge__read and knowledge__find (HOST never names the owner); at the CMI, alice without naming gets 404 and naming dave's subject scope reads it, audited as knowledge.read_other ($AUD0 -> $AUD1)"

p3_fresh
# (c) a run whose model is external (or that has an external tool) is given only what the tenant's ceiling lets out: the confidential passage is withheld and context.withheld records it; the run with the enterprise's own model gets it
# the CMI's own rule comes first: a reader sees nothing above the tenant's readerCeiling (internal by default), so the tenant raises it to confidential for this step (the policy API, as alice, as PATH N (d) does) and puts it back
p3_policy() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); d.pop('default',None); d['readerCeiling']=sys.argv[2] or d['readerCeiling']; print(json.dumps(d))" "$1" "$2"; }
POLICY3="$(call 200 GET "$CMI/api/v1/policy" "$ALICE3")"
call 200 PUT "$CMI/api/v1/policy" "$ALICE3" "$(p3_policy "$POLICY3" confidential)" >/dev/null
RC1="$(p3_run "$A_EXT" "$DAVE" "$(p3_msgs user "know: $W_C")")"; EC1="$(p3_ev "$RC1")"
RC2="$(p3_run "$A_KNOW" "$DAVE" "$(p3_msgs user "know: $W_C")")"; EC2="$(p3_ev "$RC2")"
call 200 PUT "$CMI/api/v1/policy" "$ALICE3" "$(p3_policy "$POLICY3" "")" >/dev/null   # the tenant's policy as it was
python3 - "$EC1" "$EC2" "$W_C" <<'PY' || p3_bad "(c): the screening of the confidential passage is not as D44 says (above)"
import json,sys
e1,e2,w=json.loads(sys.argv[1]),json.loads(sys.argv[2]),sys.argv[3]
wh=[c['value'] for c in e1['customs'] if c['name']=='idp.host/context.withheld']
assert wh and sum(v['count'] for v in wh)>=1 and any(v.get('byLevel',{}).get('confidential') for v in wh),('no withheld event for the external model',wh)
secret='a sum that only the enterprise may see'   # the ledger's own words: the sought word is also in the question, which the double echoes
assert w not in ' '.join(e1['results']) and secret not in json.dumps(e1),'the confidential text reached the external model'
assert not [c for c in e2['customs'] if c['name']=='idp.host/context.withheld'],'the enterprise own model run has something withheld'
assert secret in ' '.join(e2['results']),'the enterprise own model did not get the passage'
print('ok',wh[0])
PY
echo "PASS: PATH P3c — the same question about the confidential ledger: with the external model ($A_EXT, the double's twin local-ext) the passage is withheld (idp.host/context.withheld, byLevel confidential; the model is told the count and the level, not the text), with the enterprise's own model ($A_KNOW) it is given (the tenant's readerCeiling was raised to confidential for the step and put back)"

p3_fresh
# (d) a follow-up turn is rewritten before the first retrieval and the CMI is asked the rewritten question; with the rewrite off nothing is rewritten and no model call is made for it (the run's step 0 holds the rewrite's tokens)
HIST="$(p3_msgs user "what is on the dock rota" assistant "The rota has three pages." user "know: $W_P1")"
RD1="$(p3_run "$A_KNOW" "$DAVE" "$HIST")"; ED1="$(p3_ev "$RD1")"
RD2="$(p3_run "$A_OFF" "$DAVE" "$HIST")"; ED2="$(p3_ev "$RD2")"
RD3="$(p3_run "$A_KNOW" "$DAVE" "$(p3_msgs user "know: $W_P1")")"; ED3="$(p3_ev "$RD3")"   # nothing earlier: nothing to rewrite
python3 - "$ED1" "$ED2" "$ED3" "$W_P1" <<'PY' || p3_bad "(d): the rewrite is not as D66 says (above)"
import json,sys
e1,e2,e3,w=json.loads(sys.argv[1]),json.loads(sys.argv[2]),json.loads(sys.argv[3]),sys.argv[4]
rw=[c['value'] for c in e1['customs'] if c['name']=='idp.host/context.rewrite']
assert len(rw)==1 and rw[0]['outcome']=='rewritten' and rw[0]['original']=='know: '+w and rw[0]['query']!=rw[0]['original'] and 'what is on the dock rota' in rw[0]['query'],rw
rs=[c['value'] for c in e1['customs'] if c['name']=='idp.host/context.resolved'][0]
assert rs['query']==rw[0]['query'],('the resolve is not of the rewritten question',rs,rw)
assert not [c for c in e2['customs'] if c['name']=='idp.host/context.rewrite'],'the rewrite is off and an event was written'
rs2=[c['value'] for c in e2['customs'] if c['name']=='idp.host/context.resolved'][0]
assert rs2['query']=='know: '+w,rs2
rw3=[c['value'] for c in e3['customs'] if c['name']=='idp.host/context.rewrite']
assert not rw3 or rw3[0]['outcome']!='rewritten',rw3
print('ok',rw[0]['query'])
PY
Q_RESOLVED="$(p3_x "$ED1" "[c['value']['packageId'] for c in d['customs'] if c['name']=='idp.host/context.resolved'][0]")"
Q_AT_CMI="$(p3_cmi_package "$Q_RESOLVED" "$DAVE" | field "['package']['query']")"
[[ "$Q_AT_CMI" == "$(p3_x "$ED1" "[c['value']['query'] for c in d['customs'] if c['name']=='idp.host/context.rewrite'][0]")" ]] || p3_bad "(d): the CMI was asked '$Q_AT_CMI', not the rewritten question"
STEP0_ON="$(p3_hostsql "SELECT count(*) FROM run_steps WHERE run_id = '$RD1' AND step = 0")"; STEP0_OFF="$(p3_hostsql "SELECT count(*) FROM run_steps WHERE run_id = '$RD2' AND step = 0")"
[[ "$STEP0_ON" == 1 && "$STEP0_OFF" == 0 ]] || p3_bad "(d): the run's step 0 (the rewrite's model call) is there $STEP0_ON time(s) with the rewrite on and $STEP0_OFF with it off (want 1 and 0)"
echo "PASS: PATH P3d — a follow-up turn is rewritten ('$Q_AT_CMI'): the idp.host/context.rewrite event (outcome rewritten, the original and the query) comes before context.resolved, the CMI's package has that query; with the rewrite off ($A_OFF) the same request has no rewrite event, the CMI is asked the user's words, and the run has no step 0 (the rewrite's model call: 1 with the rewrite on, 0 off); a first question with nothing earlier is not rewritten"

p3_fresh
# (e) the member's CMI token is kept only while a run that has the tools is running: after the runs have ended none of this path's runs holds one (a run of an agent without the tools never held one)
RE1="$(p3_run "$A_PLAIN" "$DAVE" "$(p3_msgs user "know: $W_P1")")"
mapfile -t P3_RUNS < "$L/p3-runs.txt"; IDS="$(printf "'%s'," "${P3_RUNS[@]}")"; IDS="${IDS%,}"
HELD="$(p3_hostsql "SELECT count(*) FROM runs WHERE id IN ($IDS) AND cmi_credential IS NOT NULL AND octet_length(cmi_credential) > 0")"
NRUNS="$(p3_hostsql "SELECT count(*) FROM runs WHERE id IN ($IDS)")"
[[ "$HELD" == 0 && "$NRUNS" == "${#P3_RUNS[@]}" ]] || p3_bad "(e): $HELD of this path's $NRUNS runs (of ${#P3_RUNS[@]}) still hold a member's CMI token after they ended"
EE1="$(p3_ev "$RE1")"; [[ "$(p3_x "$EE1" "[c for c in d['customs'] if c['name']=='idp.host/context.package']")" == "[]" && "$(p3_x "$EE1" "'knowledge__' in json.dumps(d)")" == False ]] || p3_bad "(e): the agent without tools used a knowledge tool: $(echo "$EE1" | head -c 500)"
echo "PASS: PATH P3e — after the runs ended none of the $NRUNS runs of this path holds runs.cmi_credential (the member's CMI token is cleared when a run ends); the agent without the tools ($A_PLAIN) offered none and made no package of its own"

p3_fresh
# (f) read and find honour the release's context.sources: a document of another source is 'not found or not visible' through the agent that names one source, whatever its id
RF1="$(p3_run "$A_SRC" "$DAVE" "$(p3_call knowledge__read '{"documentId":"'"$DOC_B"'"}')")"; EF1="$(p3_ev "$RF1")"
RF2="$(p3_run "$A_SRC" "$DAVE" "$(p3_call knowledge__find '{"documentId":"'"$DOC_B"'","text":"'"$W_B"'"}')")"; EF2="$(p3_ev "$RF2")"
RF3="$(p3_run "$A_SRC" "$DAVE" "$(p3_call knowledge__read '{"documentId":"'"$DOC_A"'","locator":{"page":3}}')")"; EF3="$(p3_ev "$RF3")"
RF4="$(p3_run "$A_KNOW" "$DAVE" "$(p3_call knowledge__read '{"documentId":"'"$DOC_B"'"}')")"; EF4="$(p3_ev "$RF4")"   # the agent without the source rule reads it
for e in "$EF1" "$EF2"; do [[ "$(p3_x "$e" "'not found or not visible' in ' '.join(d['results'])")" == True && "$(p3_x "$e" "'$W_B' in ' '.join(d['results'])")" == False ]] || p3_bad "(f): a document of another source through the source-limited agent: $(echo "$e" | head -c 600)"; done
[[ "$(p3_x "$EF3" "'$W_P3' in ' '.join(d['results'])")" == True && "$(p3_x "$EF4" "'$W_B' in ' '.join(d['results'])")" == True ]] || p3_bad "(f): the reads that must work did not: $(echo "$EF3" | head -c 300) / $(echo "$EF4" | head -c 300)"
echo "PASS: PATH P3f — through $A_SRC (context.sources: $P3_SRC) knowledge__read and knowledge__find of a document of $P3_SRC2 are 'not found or not visible' and page 3 of the document of $P3_SRC is read; through $A_KNOW (no source rule) the same document of $P3_SRC2 is read"

p3_fresh
# (g) erasing the document redacts the packages that read and find made of it, the empty find (a package without items) among them, and the tools no longer find the document
RG0="$(p3_run "$A_KNOW" "$DAVE" "$(p3_call knowledge__find '{"documentId":"'"$DOC_A"'","text":"'"$W_MISS"' words that the document does not have"}')")"; EG0="$(p3_ev "$RG0")"
PKG_EMPTY="$(p3_x "$EG0" "[c['value']['packageId'] for c in d['customs'] if c['name']=='idp.host/context.package'][-1]")"
PE0="$(p3_cmi_package "$PKG_EMPTY" "$DAVE")"
[[ "$(echo "$PE0" | field "['package']['items']")" == "[]" && "$(echo "$PE0" | python3 -c "import json,sys; print('$W_MISS' in json.load(sys.stdin)['package']['query'])")" == True ]] || p3_bad "(g): the empty find is not an empty package with the sought text in its query: $(echo "$PE0" | head -c 400)"
ER="$(call 201 POST "$CMI/api/v1/erasures" "$ALICE3" '{"documentIds":["'"$DOC_A"'"],"reason":"P3: the document is withdrawn"}')"; ER_ID="$(echo "$ER" | field "['erasureId']")"
for _ in $(seq 1 120); do ER_STATE="$(call 200 GET "$CMI/api/v1/erasures/$ER_ID" "$ALICE3" | field "['status']")"; [[ "$ER_STATE" == verified || "$ER_STATE" == failed ]] && break; sleep 1; done
[[ "$ER_STATE" == verified ]] || p3_bad "(g): the erasure $ER_ID is '$ER_STATE', not verified"
p3_fresh
for pk in "$PKG_FIND" "$PKG_READ" "$PKG_EMPTY"; do
  PX="$(p3_cmi_package "$pk" "$DAVE")"
  python3 - "$PX" "$W_PHRASE" "$W_P2" "$W_P1" "$W_MISS" <<'PY' || p3_bad "(g): package $pk still has what the document said (above)"
import json,sys
d=json.loads(sys.argv[1]); raw=json.dumps(d,ensure_ascii=False)
assert d.get('redacted') is True,('not redacted',raw[:300])
for w in sys.argv[2:]:
    assert w not in raw,('still there',w)
print('ok')
PY
done
RG1="$(p3_run "$A_KNOW" "$DAVE" "$(p3_call knowledge__read '{"documentId":"'"$DOC_A"'"}')")"; EG1="$(p3_ev "$RG1")"
[[ "$(p3_x "$EG1" "'not found or not visible' in ' '.join(d['results'])")" == True ]] || p3_bad "(g): the erased document is still read through the tool: $(echo "$EG1" | head -c 400)"
echo "PASS: PATH P3g — a find that found nothing is a package with no items and the sought text in its query; after the erasure of the document ($ER_ID verified) that package, the find's and the read's are redacted (redacted: true, none of the document's words, the page, the phrase or the sought text is left in them) and knowledge__read of the document is 'not found or not visible'"
