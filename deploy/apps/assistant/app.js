// The assistant: a chat page framed by the IDP Workspace shell, speaking idp.bridge/v1 by hand (no SDK).
// A question is one of this app's own actions (host.run: the HOST runs the agent for the member); the answer
// is that run's inbox item, which the shell shows this app because the app started the run (idp-workspace D20).
'use strict';

const SHELL = new URLSearchParams(location.search).get('shell') || 'http://127.0.0.1:17560';
const AGENTS = {
  ask: { hint: '按企业知识回答订单、取件与政策问题。', placeholder: '例如：贵重包裹的取件有什么规定？' },
  code: { hint: '在隔离的沙箱里写代码并运行，告诉你结果。', placeholder: '例如：用 Python 算一下 2 的 100 次方' },
  chat: { hint: '同一次对话里它记得你说过的话；点“新对话”后，它靠企业记忆（CMI）想起你以前告诉过它的事。记忆可在工作台的“记忆”页查看和删除。', placeholder: '例如：我最喜欢的颜色是青色' },
};
const POLL_MS = 2000;
const GIVE_UP_MS = 10 * 60 * 1000; // the shell follows a run for 10 minutes (idp-workspace D11)

let seq = 0;
const pending = new Map();
window.addEventListener('message', (ev) => {
  if (ev.origin !== SHELL || ev.source !== window.parent) return;
  const m = ev.data;
  if (!m || m.protocol !== 'idp.bridge/v1' || m.type !== 'response' || !pending.has(m.id)) return;
  const done = pending.get(m.id);
  pending.delete(m.id);
  done(m);
});
function call(method, params) {
  return new Promise((resolve) => {
    const id = 'assistant.' + (++seq);
    const timer = setTimeout(() => { if (pending.delete(id)) resolve({ error: { code: 'NO_SHELL', message: '没有收到工作台的应答' } }); }, 30000);
    pending.set(id, (m) => { clearTimeout(timer); resolve(m); });
    parent.postMessage({ protocol: 'idp.bridge/v1', type: 'request', id, method, ...(params ? { params } : {}) }, SHELL);
  });
}

const $ = (id) => document.getElementById(id);
let agent = 'ask';
let workspace = '';
let history = [];
// a conversation per agent: the thread of its last run, so the agent has this conversation's history (the HOST keeps the
// thread); "新对话" forgets the thread, the CMI's memory of the member stays
let threads = {};
let convos = {};
const storageKey = () => 'assistant.' + workspace;
const threadsKey = () => 'assistant.threads.' + workspace;
function saveThreads() { try { localStorage.setItem(threadsKey(), JSON.stringify({ threads, convos })); } catch { /* ignore */ } }
function loadThreads() { try { const s = JSON.parse(localStorage.getItem(threadsKey()) || '{}'); threads = s.threads || {}; convos = s.convos || {}; } catch { threads = {}; convos = {}; } }
function save() {
  try { localStorage.setItem(storageKey(), JSON.stringify(history.slice(-50))); } catch { /* storage blocked: the chat still works */ }
}
function load() {
  try { history = JSON.parse(localStorage.getItem(storageKey()) || '[]'); } catch { history = []; }
}

// a little Markdown for the answers (code blocks, quotes, list bullets, **bold**, `code`), built from text nodes only
function renderAnswer(el, text) {
  el.replaceChildren();
  text.split(/```[^\n]*\n?/).forEach((part, i) => {
    if (i % 2 === 1) { // inside a fence
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      code.textContent = part.replace(/\n$/, '');
      pre.append(code);
      el.append(pre);
      return;
    }
    for (const line of part.replace(/^\n+|\n+$/g, '').split('\n')) {
      const quote = /^\s*>\s?/.test(line);
      const row = document.createElement(quote ? 'blockquote' : 'div');
      const body = quote ? line.replace(/^\s*>\s?/, '') : line.replace(/^\s*[-*]\s+/, '• ');
      for (const tok of body.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
        if (!tok) continue;
        if (/^\*\*[^*]+\*\*$/.test(tok)) { const b = document.createElement('strong'); b.textContent = tok.slice(2, -2); row.append(b); }
        else if (/^`[^`]+`$/.test(tok)) { const c = document.createElement('code'); c.textContent = tok.slice(1, -1); row.append(c); }
        else row.append(document.createTextNode(tok));
      }
      if (!row.childNodes.length) row.append(document.createElement('br'));
      el.append(row);
    }
  });
}

function render() {
  const chat = $('chat');
  chat.replaceChildren();
  let lastConvo = null;
  for (const t of history.filter((x) => x.agent === agent)) {
    if (lastConvo !== null && (t.convo || 0) !== lastConvo) {
      const sep = document.createElement('li');
      sep.className = 'sep';
      sep.textContent = '— 新对话 —';
      chat.append(sep);
    }
    lastConvo = t.convo || 0;
    const q = document.createElement('li');
    q.className = 'me';
    q.textContent = t.question;
    const a = document.createElement('li');
    a.className = 'bot ' + t.state;
    if (t.state === 'done') renderAnswer(a, t.answer);
    else a.textContent = t.state === 'waiting' ? (t.note || '思考中…') : t.answer;
    chat.append(q, a);
  }
  if (chat.lastElementChild) chat.lastElementChild.scrollIntoView({ block: 'end' });
}
function select(next) {
  agent = next;
  for (const b of document.querySelectorAll('[data-action]')) b.setAttribute('aria-selected', String(b.dataset.action === agent));
  $('hint').textContent = AGENTS[agent].hint;
  $('message').placeholder = AGENTS[agent].placeholder;
  render();
}

// the run's item: " — running", while it waits for its owner " — awaiting confirmation" or " — waiting for input"
// (idp-workspace D18), and at its end " — finished", " — failed" or " — result unavailable"
async function follow(turn) {
  let until = Date.now() + GIVE_UP_MS;
  while (Date.now() < until) {
    const r = await call('inbox.list', { kind: 'run' });
    const item = (r.result || []).find((i) => i.externalRef === 'run:' + turn.runId && typeof i.summary === 'string');
    if (item && / — (finished|failed|result unavailable)$/.test(item.title)) {
      turn.state = / — finished$/.test(item.title) ? 'done' : 'failed';
      turn.answer = item.summary || '（没有内容）';
      turn.note = '';
      save();
      render();
      return;
    }
    const waiting = Boolean(item && / — (awaiting confirmation|waiting for input)$/.test(item.title));
    if (waiting) until = Date.now() + GIVE_UP_MS; // time spent waiting for the owner does not count
    const note = waiting ? '等待中：' + item.summary : '';
    if (note !== (turn.note || '')) { turn.note = note; save(); render(); }
    await new Promise((res) => setTimeout(res, POLL_MS));
  }
  turn.state = 'failed';
  turn.answer = '等了 10 分钟还没有结果，去收件箱看看。';
  save();
  render();
}

async function ask(question) {
  const turn = { agent, question, state: 'waiting', answer: '', startedAt: Date.now(), convo: convos[agent] || 0 };
  history.push(turn);
  save();
  render();
  const r = await call('actions.invoke', { action: agent, input: { message: question }, ...(threads[agent] ? { threadId: threads[agent] } : {}) });
  const inv = r.result;
  if (r.error || !inv || inv.status !== 'succeeded' || !inv.result || typeof inv.result.id !== 'string') {
    turn.state = 'failed';
    turn.answer = '没能发起：' + (r.error ? r.error.code + ' ' + r.error.message : JSON.stringify(inv));
    save();
    render();
    return;
  }
  turn.runId = inv.result.id;
  if (typeof inv.result.threadId === 'string') { threads[turn.agent] = inv.result.threadId; saveThreads(); }
  save();
  await follow(turn);
}

$('ask').addEventListener('submit', (e) => {
  e.preventDefault();
  const question = $('message').value.trim();
  if (!question || $('send').disabled) return;
  $('message').value = '';
  ask(question);
});
$('message').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('ask').requestSubmit(); }
});
for (const b of document.querySelectorAll('[data-action]')) b.addEventListener('click', () => select(b.dataset.action));
$('fresh').addEventListener('click', () => { delete threads[agent]; convos[agent] = (convos[agent] || 0) + 1; saveThreads(); render(); });

(async () => {
  $('send').disabled = true;
  if (window.parent === window) { $('where').textContent = '请在 IDP Workspace 里打开'; return; }
  const hello = await call('bridge.hello', { app: 'assistant', version: '1.0.0' });
  if (hello.error) { $('where').textContent = '请在 IDP Workspace 里打开（' + hello.error.code + '）'; return; }
  workspace = hello.result.workspace.name;
  $('where').textContent = '工作空间 ' + workspace;
  load();
  loadThreads();
  for (const t of history) {
    if (t.state === 'waiting' && !t.runId) { t.state = 'failed'; t.answer = '页面关闭前没有发起成功。'; }
  }
  save();
  select(agent);
  $('send').disabled = false;
  for (const t of history) if (t.state === 'waiting') follow(t); // still running when the page was closed
})();
