# PATH M6 (platform plan browser-capability-plan.md, layer 1 "render then read"; idp-browser; idp-web D33), shared by deploy/compose/e2e.sh and tests/kind/full.sh, which
# `source` it inside PATH M and N's block (it uses their ask_agent and run_summary: bob's run of web-assistant, the answer that reached his inbox). The pages are the test
# origin's (deploy/compose/web_origin.py): a page whose text exists only after script, one whose text arrives late, an SSRF page that asks for every forbidden address in
# every way a page can, a request storm, an access-denied page in the wording of an Akamai edge (plain and made by script) and a PDF on a host that the test tenant lists in
# alwaysRender. What the caller supplies: the fixture entries m6js m6js2 m6js3 m6delayed m6ssrf m6storm m6denied m6deniedjs m6pdf m6plain, WEB_ORIGIN WEB_RENDER_ORIGIN,
# M6_SSRF_TARGETS (host:port the SSRF page asks for), M6_ISOLATION (a sentence: what isolates the renderer here), and the adapters m6_web_log m6_proxy_log m6_renderer_log
# m6_canary_log m6_render_origin_log (the logs), m6_get URL (a GET from where idp-web is: the metrics, the renderer's readiness), m6_quota_reset (idp-web's daily search count: the
# tenant may search 3 times a day and this path searches ten times), m6_renderer_stop and m6_renderer_start (the renderer, which must be ready when the latter returns).
BCHK=(python3 -I deploy/compose/browser_check.py)
m6_ask() { m6_quota_reset; M6RUN="$(ask_agent websearch ask "web: $1")"; M6ANS="$(run_summary "$M6RUN")"; }   # the answer that reached bob's inbox: $M6ANS
renders_ok() { m6_get "$M6_WEB_METRICS" | "${BCHK[@]}" metric idp_web_renders_total outcome=ok; }
want_in() { [[ "$2" == *"$3"* ]] || { echo "FAIL: PATH M6 $1: the answer does not say '$3': ${2:0:300}" >&2; exit 1; }; }
NS_SENTENCE="produced by script and cannot be read now"; BLOCKED_SENTENCE="refused to show this page to an automated reader"
U="http://$WEB_ORIGIN"
# the renderer and the proxy have not been used by any earlier path: what they log below is this path's
M6_OK0="$(renders_ok)"

# (a) a page whose text exists only after script: bob's agent reads it through the renderer
m6_ask m6js
want_in "(a)" "$M6ANS" "dock 7731"; want_in "(a)" "$M6ANS" "(rendered;"
A_AUD="$(m6_web_log | "${BCHK[@]}" audit "$U/js.html" decision=allowed reason=ok rendered=true render=ok)" || exit 1
M6_OK1="$(renders_ok)"
[[ "$M6_OK1" -eq $((M6_OK0 + 1)) ]] || { echo "FAIL: PATH M6 (a): idp_web_renders_total{outcome=ok} went from $M6_OK0 to $M6_OK1, not up by one" >&2; exit 1; }
echo "PASS: PATH M6a — bob's agent read $U/js.html, whose text exists only after script (an empty #root and a script): the answer has 'dock 7731' and the mark '(rendered;' the model sees; $A_AUD; idp_web_renders_total{outcome=\"ok\"} $M6_OK0 -> $M6_OK1 (the citation's rendered:true is in the tool result's structured content, which the HOST does not keep: the audit and the mark are what the platform can read)"

# (b) text that arrives late: the page asks a slow API (1.2 s) and writes what it gets
m6_ask m6delayed
want_in "(b)" "$M6ANS" "Berth 4418"; want_in "(b)" "$M6ANS" "(rendered;"
B_AUD="$(m6_web_log | "${BCHK[@]}" audit "$U/delayed.html" decision=allowed reason=ok rendered=true render=ok)" || exit 1
echo "PASS: PATH M6b — the page whose text arrives 1.2 s after load (a slow API call) was read with its text: 'Berth 4418' in the answer; $B_AUD"

# (c) the SSRF page asks for the metadata address, three private ranges, loopback and ::1, and the internal names of this deployment (a canary behind two names, real services),
# by fetch, XHR, an image (the renderer does not even fetch images), an iframe, a WebSocket and a beacon: every request Chromium sent was refused at the proxy and the canary got nothing
m6_ask m6ssrf
want_in "(c)" "$M6ANS" "Dock 6602"
C_AUD="$(m6_web_log | "${BCHK[@]}" audit "$U/ssrf.html" decision=allowed reason=ok rendered=true render=ok)" || exit 1
C_PROXY="$(m6_proxy_log | "${BCHK[@]}" ssrf $M6_SSRF_TARGETS)" || exit 1
C_CANARY="$(m6_canary_log | "${BCHK[@]}" canary)" || exit 1
echo "PASS: PATH M6c — the SSRF page: it was read ('Dock 6602') and $C_PROXY; $C_CANARY; $C_AUD"

# (d) a request storm (3000 sub-resource requests): the render ends within the budgets, the next render works
m6_ask m6storm
want_in "(d)" "$M6ANS" "Storm page 9913"
D_AUD="$(m6_web_log | "${BCHK[@]}" audit "$U/storm.html" decision=allowed reason=ok rendered=true render=ok)" || exit 1
D_PROXY="$(m6_proxy_log | "${BCHK[@]}" storm 300)" || exit 1
D_RENDERER="$(m6_renderer_log | "${BCHK[@]}" render-lines 3000 2000)" || exit 1   # the page made 3000 requests and the renderer refused all but 300 of them
m6_ask m6js2
want_in "(d)" "$M6ANS" "dock 7731"; want_in "(d)" "$M6ANS" "(rendered;"
D_NEXT="$(m6_web_log | "${BCHK[@]}" audit "$U/js.html?next=1" decision=allowed reason=ok rendered=true render=ok)" || exit 1
echo "PASS: PATH M6d — the storm page ($D_RENDERER: requests the page made, and those the renderer's budget refused) ended in time and was read ('Storm page 9913'); $D_PROXY; the next render worked: $D_NEXT"

# (e) an access-denied page that answers 200, in an Akamai edge's wording: both when it is plain text (no render) and when script writes it (rendered, then judged)
m6_ask m6denied
want_in "(e)" "$M6ANS" "$BLOCKED_SENTENCE"
E1="$(m6_web_log | "${BCHK[@]}" audit "$U/denied.html" decision=failed reason=blocked_by_site rendered=false render=)" || exit 1
m6_ask m6deniedjs
want_in "(e)" "$M6ANS" "$BLOCKED_SENTENCE"
E2="$(m6_web_log | "${BCHK[@]}" audit "$U/denied-js.html" decision=failed reason=blocked_by_site rendered=false render=blocked_by_site)" || exit 1
echo "PASS: PATH M6e — an HTTP 200 'Access Denied' page gives the blocked_by_site sentence ('$BLOCKED_SENTENCE'), plain: $E1; and made by script, after a render: $E2"

# (f) a PDF on a host the test tenant lists in alwaysRender goes to the PDF reader, and the renderer is not called
FO0="$(m6_render_origin_log | grep -c 'tariff.pdf' || true)"; RL0="$(m6_renderer_log | "${BCHK[@]}" render-lines)"; PX0="$(m6_proxy_log | "${BCHK[@]}" proxy-host "$WEB_RENDER_ORIGIN")"; OK_F0="$(renders_ok)"
m6_ask m6pdf
want_in "(f)" "$M6ANS" "29.47"; want_in "(f)" "$M6ANS" "PDF, 2 pages"
F_AUD="$(m6_web_log | "${BCHK[@]}" no-audit "http://$WEB_RENDER_ORIGIN/tariff.pdf")" || exit 1
RL1="$(m6_renderer_log | "${BCHK[@]}" render-lines)"; PX1="$(m6_proxy_log | "${BCHK[@]}" proxy-host "$WEB_RENDER_ORIGIN")"
[[ "$RL1" == "$RL0" && "$PX1" == "$PX0" && "$(renders_ok)" == "$OK_F0" ]] || { echo "FAIL: PATH M6 (f): the renderer was called for a PDF (renders $RL0 -> $RL1, its proxy lines for the host $PX0 -> $PX1)" >&2; exit 1; }
F_ORIGIN="$(( $(m6_render_origin_log | grep -c 'tariff.pdf' || true) - FO0 ))"
[[ "$F_ORIGIN" == 1 ]] || { echo "FAIL: PATH M6 (f): the host in alwaysRender was asked for the PDF $F_ORIGIN times, want once (idp-web's own read)" >&2; exit 1; }
echo "PASS: PATH M6f — the PDF on $WEB_RENDER_ORIGIN (a host in the tenant's alwaysRender) was read by the PDF reader ('29.47', 'PDF, 2 pages'): $F_AUD; the renderer did not render ($RL0 -> $RL1) and its proxy was not asked for that host; the host was asked once"

# (g) the renderer stopped: a page that needs script gives the needs_script sentence with the audit reason render_unreachable; another page still reads
m6_renderer_stop
m6_ask m6js3
want_in "(g)" "$M6ANS" "$NS_SENTENCE"
G1="$(m6_web_log | "${BCHK[@]}" audit "$U/js.html?g=1" decision=failed reason=render_unreachable rendered=false render=render_unreachable)" || exit 1
m6_ask m6plain
want_in "(g)" "$M6ANS" "41,872"
G2="$(m6_web_log | "${BCHK[@]}" audit "$U/article.html?plain=1" decision=allowed reason=ok rendered=false render=)" || exit 1
m6_renderer_start
echo "PASS: PATH M6g — with the renderer stopped a page that needs script gave the needs_script sentence ('$NS_SENTENCE'): $G1; an ordinary page still read ('41,872'): $G2; the renderer was started again and is ready. Isolation of the renderer in this run: $M6_ISOLATION"
