# PATH P1 (platform plan knowledge-base-plan.md, KB2: the platform enables embeddings and semantic retrieval; idp-cmi ADR-0001 D11, D26, D37, D38, D58), shared by deploy/compose/e2e.sh and
# tests/kind/full.sh, which `source` it after PATH P0. Everything goes through CMI's HTTP API with real tokens (alice cmi-admin, carol cmi-reader); the stored vectors are looked at in the database.
# What the caller supplies: CMI, ALICE, CAROL, `call EXPECTED METHOD URL TOKEN [BODY]` and `field`, and the adapters p1_embedding_stop and p1_embedding_start (the service stopped; started and
# answering /health), p1_sql SQL (psql on the CMI's database, tuples only), p1_metrics (the CMI's Prometheus text) and p1_query_stop / p1_query_start (the second embedding instance, idp-cmi D59). Random suffix per run; the words searched for are made of it.
# EMBED (lib_acceptance.sh): with the double only (a) runs in full; (b) to (e) need semantic similarity and print NOT_RUN. The embedding service is up for every earlier path. Here it is stopped, documents are uploaded and resolved (lexically, nothing marked permanent), then it returns and backfill embeds them.
set -u
R1="$(openssl rand -hex 3)"; P1_SRC="p1-docs-$R1"; P1_KB="p1-kb-$R1"
W_RET="tulipwren${R1//[0-9]/x}"; W_PICK="hatchlark${R1//[0-9]/x}"; W_LIVE="quillfinch${R1//[0-9]/x}"
# the paraphrases share no word at all with their document (not even `the` or `to`: the lexical side is an OR of the query's words, so any shared word is a lexical hit), and the floor 0.40 is cleared by the meaning alone
# Observed on 2026-11-25 (compose, TEI cpu-1.9.4, Qwen3-Embedding-0.6B at 97b0c614, idp-cmi settings floor 0.40, lexicalBlend 0.05, queryPrefix "Instruct: Given a user question, retrieve the passages that answer it\nQuery: "):
# P1_PARA -> the returns document: match semantic, score 0.4452 (a second, unrelated document scored 0.4209); the Chinese question -> semantic, 0.4743; P1_PARA_D -> the pickup note: both (0.5124 for the
# older pickup-policy document of P0, 0.4981 for the returns document). The margin over the floor is thin (0.045 and 0.074) but the model on CPU is deterministic: if P1b or P1c fail after a change of
# model, revision, prefix or floor, look at the scores first (resolve and read `score` and `match`), then at the wording of these questions.
P1_PARA="How many weeks after receiving goods can buyers send them off for reimbursement?"; P1_PARA_D="When can I come to retrieve my deliveries?"
q1() { python3 -c "import json,sys; print(json.dumps({'query':sys.argv[1],'purpose':'assist','budget':{'maxItems':8}}))" "$1"; }
# p1_match WORD QUERY: the match of the first item of a resolve for carol whose text has WORD ("none" when no item has it)
p1_match() { call 200 POST "$CMI/v1/context/resolve" "$CAROL" "$(q1 "$2")" | python3 -c "
import json,sys
for it in json.load(sys.stdin)['package']['items']:
    if sys.argv[1] in json.dumps(it, ensure_ascii=False): print(it.get('match','?')); break
else: print('none')" "$1"; }
source deploy/compose/lib_p1_helpers.sh   # p1_metric and p1_metric_max (path_p2.sh uses them too, so they are in a file of their own)
p1_bad() { echo "FAIL: PATH P1 $*" >&2; exit 1; }
p1_wait() { local what="$1" want="$2" w="$3" q="$4" secs="$5" got="" t0; t0=$(date +%s)   # poll a resolve until the item has one of the wanted matches (words), for secs seconds
  while :; do got="$(p1_match "$w" "$q")"; [[ " $want " == *" $got "* ]] && { P1_WAITED=$(( $(date +%s) - t0 )); return 0; }; (( $(date +%s) - t0 > secs )) && p1_bad "$what: after $secs s the match is '$got', want $want"; sleep 3; done; }
P1_TEXT_A="Returns policy. $W_RET is the marker of this rule. A customer may hand back an unwanted purchase within fourteen days of its delivery, and the sum paid is credited to the original card."
P1_TEXT_B="Customs desk. Parcels to other countries wait at the border until the duties are paid; the clerk posts the receipt to the sender."
call 201 POST "$CMI/api/v1/sources" "$ALICE" '{"name":"'"$P1_SRC"'","displayName":"P1 documents","kind":"document-store","trust":"authoritative"}' >/dev/null
call 201 POST "$CMI/api/v1/knowledge/bases" "$ALICE" '{"name":"'"$P1_KB"'","displayName":"P1","scope":{"kind":"tenant"},"managers":{"subjects":[],"roles":[]},"defaultSensitivity":"internal","sensitivityCeiling":"confidential","source":"'"$P1_SRC"'","quotaDocuments":0,"quotaBytes":0,"ocr":false}' >/dev/null
p1_up() { call 201 POST "$CMI/api/v1/knowledge/documents" "$ALICE" '{"source":"'"$P1_SRC"'","knowledgeBase":"'"$P1_KB"'","uri":"kb://'"$P1_KB"'/'"$1"'.md","title":"'"$1"'","text":"'"$2"'"}' | field "['documentId']"; }

# (a) the service stopped: documents are uploaded and resolved lexically (the paraphrase finds nothing), and no chunk is marked permanent
# the query endpoint (idp-cmi D59) has passed its probe against the documents endpoint: queries use it
P1_T0=$(date +%s)
for _ in $(seq 1 400); do [[ "$(p1_metric idp_cmi_query_endpoint_state state=ok)" -ge 1 ]] && break; sleep 3; done   # up to 20 minutes: the first probe needs one answer of the documents endpoint, which is busy embedding what the earlier paths left (4 CPUs)
[[ "$(p1_metric idp_cmi_query_endpoint_state state=ok)" -ge 1 ]] || p1_bad "(a): the query endpoint's state is not ok in 20 minutes"
P1_PROBE_WAIT=$(( $(date +%s) - P1_T0 )); echo "P1: the query endpoint was ok after $P1_PROBE_WAIT s" >&2
FAILED0="$(p1_metric idp_cmi_embed_failures_total kind=unavailable role=documents)"; LS0="$(p1_metric_max idp_cmi_embedding_last_success_timestamp_seconds role=documents)"
p1_embedding_stop
DOC_A="$(p1_up returns "$P1_TEXT_A")"; DOC_B="$(p1_up customs "$P1_TEXT_B")"
M_LEX="$(p1_match "$W_RET" "$W_RET fourteen days unwanted purchase")"
[[ "$M_LEX" == lexical ]] || p1_bad "(a): with the embedding service stopped the shared-word query is '$M_LEX', want lexical"
M_PARA0="$(p1_match "$W_RET" "$P1_PARA")"
[[ "$M_PARA0" == none ]] || p1_bad "(a): the paraphrase found the document without any vector ($M_PARA0)"
for _ in $(seq 1 40); do FAILED=$(( $(p1_metric idp_cmi_embed_failures_total kind=unavailable role=documents) - FAILED0 )); [[ "$FAILED" -ge 1 ]] && break; sleep 3; done   # a backfill round (default every 30 s) or the upload's projection ran against the stopped service and failed
[[ "$FAILED" -ge 1 ]] || p1_bad "(a): idp_cmi_embed_failures_total{kind="unavailable",role="documents"} did not grow in 2 minutes with the service stopped"
sleep 35   # one more round, so that "nothing is permanent" is seen after a failure and not before it
PERM="$(p1_sql "SELECT count(*) FROM knowledge_chunks WHERE document_id IN ('$DOC_A','$DOC_B') AND embed_error IS NOT NULL")"
NOVEC="$(p1_sql "SELECT count(*) FROM knowledge_chunks WHERE document_id IN ('$DOC_A','$DOC_B') AND embedding IS NULL")"
[[ "$PERM" == 0 && "$NOVEC" -ge 2 ]] || p1_bad "(a): $PERM chunk(s) marked permanent, $NOVEC without a vector (want 0 and at least 2)"
echo "PASS: PATH P1a — the embedding service stopped: 2 documents uploaded into $P1_KB are answered lexically (match $M_LEX for a query that shares '$W_RET'), the paraphrase finds nothing, idp_cmi_embed_failures_total{kind=unavailable,role=documents} grew by $FAILED and $PERM chunk(s) are marked permanent (D58), $NOVEC wait without a vector"

# (b) to (e) are paraphrases with no shared word and a Chinese question: they need semantic similarity, which only the real embedding service has (tests/paths.yaml realEmbedding).
# With the double (EMBED=double, the default; lib_acceptance.sh) they print NOT_RUN and the service is only started again, with the part of (b) that needs no similarity checked:
# the chunks that were uploaded while it was down get their vectors through backfill.
if [[ "${EMBED:-real}" != real ]]; then
p1_embedding_start
for _ in $(seq 1 60); do LEFT="$(p1_sql "SELECT count(*) FROM knowledge_chunks WHERE document_id IN ('$DOC_A','$DOC_B') AND embedding IS NULL")"; [[ "$LEFT" == 0 ]] && break; sleep 3; done
[[ "$LEFT" == 0 ]] || p1_bad "(b): $LEFT chunk(s) of the documents uploaded while the service was down still have no vector 3 minutes after it started (the double)"
for id in P1b P1c P1d P1e; do path_needs_real "$id" || true; done
else
# (b) the service returns: the pending chunks get vectors through backfill, and the paraphrase now finds the document with match semantic
p1_embedding_start
p1_wait "(b) the paraphrase after the service returned" semantic "$W_RET" "$P1_PARA" 300
P1_B_WAIT=$P1_WAITED
for _ in $(seq 1 20); do LS1="$(p1_metric_max idp_cmi_embedding_last_success_timestamp_seconds role=documents)"; awk -v a="$LS1" -v b="$LS0" 'BEGIN{exit !(a>b)}' && break; sleep 3; done   # the newest success of any replica, polled for up to a minute
awk -v a="$LS1" -v b="$LS0" 'BEGIN{exit !(a>b)}' || p1_bad "(b): idp_cmi_embedding_last_success_timestamp_seconds{role=documents} did not move after the service returned ($LS0 -> $LS1)"
LEFT="$(p1_sql "SELECT count(*) FROM knowledge_chunks WHERE document_id IN ('$DOC_A','$DOC_B') AND embedding IS NULL")"
[[ "$LEFT" == 0 ]] || { for _ in $(seq 1 60); do LEFT="$(p1_sql "SELECT count(*) FROM knowledge_chunks WHERE document_id IN ('$DOC_A','$DOC_B') AND embedding IS NULL")"; [[ "$LEFT" == 0 ]] && break; sleep 3; done; }
[[ "$LEFT" == 0 ]] || p1_bad "(b): $LEFT chunk(s) of the documents uploaded before the service started still have no vector"
echo "PASS: PATH P1b — the service back: the chunks uploaded while it was down got their vectors through backfill ($P1_B_WAIT s after the start, none left without), and the paraphrase '$P1_PARA', which shares no content word with the document, finds it with match semantic"

# (c) a Chinese question finds the English document
p1_wait "(c) a Chinese question" semantic "$W_RET" "我不想要了，收到后多少天之内可以退还并拿回钱款" 60
echo "PASS: PATH P1c — a Chinese question ('我不想要了，收到后多少天之内可以退还并拿回钱款') finds the English returns document with match semantic"

# (d) a document uploaded while the service is up gets its vector through the projection (the outbox, D26), and the HNSW index is the one the query uses (D38)
DOC_D="$(p1_up pickup "Pickup counter. $W_PICK is the marker of this note. The hatch where parcels are collected opens at six in the morning and closes at noon.")"
p1_wait "(d) a document uploaded while the service is up" "semantic both" "$W_PICK" "$P1_PARA_D" 120
P1_D_WAIT=$P1_WAITED
BOTH="$(p1_match "$W_PICK" "$W_PICK hatch collected morning")"
HNSW="$(p1_sql "SELECT count(*) FROM pg_indexes WHERE tablename = 'knowledge_chunks' AND indexdef ILIKE '%hnsw%'")"
[[ "$HNSW" -ge 1 ]] || p1_bad "(d): no HNSW index on knowledge_chunks (vector-index 1024 did not run)"
[[ "$BOTH" == both || "$BOTH" == lexical || "$BOTH" == semantic ]] || p1_bad "(d): a query that shares the marker finds '$BOTH'"
echo "PASS: PATH P1d — a document uploaded while the service is up is found by a paraphrase with a vector match (semantic, or both when a word of the question is also in the text) $P1_D_WAIT s after the upload (projection); the query that shares its marker word is '$BOTH'; the database has $HNSW HNSW index(es) on knowledge_chunks (D38)"

# (e) the query instance stopped: a resolve still answers (from words alone), the failure is counted under role query, and with the instance back the paraphrase is semantic again (D59)
QF0="$(p1_metric idp_cmi_embed_failures_total kind=unavailable role=query)"
p1_query_stop
M_E="$(p1_match "$W_RET" "$W_RET fourteen days unwanted purchase")"
[[ "$M_E" == lexical ]] || p1_bad "(e): with the query instance stopped the shared-word query is '$M_E', want lexical (an answer from words alone)"
M_E2="$(p1_match "$W_RET" "$P1_PARA")"
[[ "$M_E2" == none ]] || p1_bad "(e): with the query instance stopped the paraphrase found the document ($M_E2)"
QF1="$(p1_metric idp_cmi_embed_failures_total kind=unavailable role=query)"
[[ "$QF1" -gt "$QF0" ]] || p1_bad "(e): idp_cmi_embed_failures_total{kind=unavailable,role=query} did not grow ($QF0 -> $QF1)"
p1_query_start
p1_wait "(e) the paraphrase after the query instance returned" semantic "$W_RET" "$P1_PARA" 120
P1_E_WAIT=$P1_WAITED
[[ "$(p1_metric idp_cmi_query_endpoint_state state=ok)" -ge 1 ]] || p1_bad "(e): the query endpoint's state is not ok after the instance returned"
echo "PASS: PATH P1e — the query instance stopped: a resolve still answers, lexically (the shared-word query is $M_E, the paraphrase finds nothing), idp_cmi_embed_failures_total{kind=unavailable,role=query} grew from $QF0 to $QF1; with it back the paraphrase is semantic again after $P1_E_WAIT s and idp_cmi_query_endpoint_state is ok"
fi
