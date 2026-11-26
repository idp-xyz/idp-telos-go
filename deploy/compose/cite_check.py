"""Checks of PATH P4 (unified citation numbering: idp-host ADR-0001 D70 to D85, contract idp.telos.host 1.13.0, idp-web D34).

    python3 -I cite_check.py mixed     RUN.json EVENTS.json NEXT                   one run with a knowledge search and a web search
    python3 -I cite_check.py followup  RUN.json EVENTS.json NEXT_BEFORE NEXT_AFTER FIRST_RUN.json DOCUMENT_ID   a follow-up run in the same thread
    python3 -I cite_check.py tree      ROOT.json ROOT_EVENTS.json NEXT CHILD.json [CHILD.json ...]            a run that delegated to sub-agents

RUN.json is GET /v1/runs/{id}, EVENTS.json is GET /v1/runs/{id}/events (HOST's own, not the Workspace's relay), NEXT is what
`citation_counters.next` of the run's thread holds after the run. Prints one line and exits 0, or says what is wrong on stderr and exits 1.
"""
import json
import re
import sys


def fail(what, msg):
    print(f'FAIL: PATH P4 {what}: {msg}', file=sys.stderr)
    sys.exit(1)


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def events_of(path):
    return [e.get('event', e) for e in load(path).get('events', [])]


def answer_of(events):
    """The last assistant text message of the run's stream."""
    text, current = '', ''
    for e in events:
        if e.get('type') == 'TEXT_MESSAGE_START':
            current = ''
        elif e.get('type') == 'TEXT_MESSAGE_CONTENT':
            current += e.get('delta', '')
        elif e.get('type') == 'TEXT_MESSAGE_END' and current.strip():
            text = current.strip()
    if not text:
        fail('answer', 'the run has no answer on its stream')
    return text


def marks_of(text):
    return {int(n) for n in re.findall(r'\[(\d+)\]', text)}


def lists_of(run, where):
    """(web, knowledge): the run's two lists, checked for shape, order and the 1.13.0 fields."""
    web, know = run.get('sources') or [], run.get('knowledgeSources') or []
    for name, xs in (('sources', web), ('knowledgeSources', know)):
        ns = [x.get('n') for x in xs]
        if ns != sorted(set(ns)) or any(not isinstance(n, int) or n < 1 for n in ns):
            fail(where, f'{name} are not in the order of distinct positive numbers: {ns}')
        for x in xs:
            if not isinstance(x.get('cited'), bool) or not x.get('title') and x.get('title') != '':
                fail(where, f'a source of {name} lacks a field: {x}')
    for x in web:
        if not isinstance(x.get('webN'), int) or x['webN'] < 1 or 'url' not in x or 'kind' in x:
            fail(where, f'a web source wants url and webN and has no kind (contract 1.13.0): {x}')
    for x in know:
        if not x.get('documentId') or 'url' in x:
            fail(where, f'a knowledge source wants a documentId and no url: {x}')
    clash = {x['n'] for x in web} & {x['n'] for x in know}
    if clash:
        fail(where, f'a number is in both lists: {sorted(clash)}')
    return web, know


def identity(x):
    return ('web', x['url']) if 'url' in x else ('know', x['documentId'], x.get('where', ''))


def one_number_one_thing(runs, where, same_thing_twice=False):
    """Across every list of every run given: a number is one thing, and (unless the first package of a run, which is not deduplicated, D81 a, may give a passage a second one) a thing has one number. Returns {n: identity}."""
    by_n, by_id = {}, {}
    for run in runs:
        web, know = lists_of(run, where)
        for x in web + know:
            i = identity(x)
            if by_n.setdefault(x['n'], i) != i:
                fail(where, f'number {x["n"]} is {by_n[x["n"]]} and {i}: a duplicate number in the thread')
            if by_id.setdefault(i, x['n']) != x['n'] and not same_thing_twice:
                fail(where, f'{i} has the numbers {by_id[i]} and {x["n"]}')
    return by_n


def contiguous(by_n, nxt, where):
    given = sorted(by_n)
    if not given:
        fail(where, 'no source was numbered')
    if given != list(range(1, given[-1] + 1)):
        fail(where, f'the numbers given have a hole: {given}')
    if nxt != given[-1] + 1:
        fail(where, f'citation_counters.next is {nxt}, want one past the highest number given ({given[-1]} + 1)')


def flags_follow_the_answer(run, answer, where):
    marks = marks_of(answer)
    web, know = lists_of(run, where)
    wrong = [x['n'] for x in web + know if x['cited'] != (x['n'] in marks)]
    if wrong:
        fail(where, f'cited flags that do not follow the answer {sorted(marks)}: {wrong}')
    return marks


def result_texts(events):
    return [e.get('content') or '' for e in events if e.get('type') == 'TOOL_CALL_RESULT']


def mixed(run_path, events_path, nxt):
    run, events = load(run_path), events_of(events_path)
    nxt = int(nxt)
    if run.get('status') != 'COMPLETED':
        fail('mixed', f'the run is {run.get("status")}')
    web, know = lists_of(run, 'mixed')
    if not web or not know:
        fail('mixed', f'a run with a knowledge search and a web search wants both lists: sources {len(web)}, knowledgeSources {len(know)}')
    by_n = one_number_one_thing([run], 'mixed')
    contiguous(by_n, nxt, 'mixed')
    if max(x['n'] for x in know) >= min(x['n'] for x in web):
        fail('mixed', f'the knowledge search came first, so its numbers are below the web results\': knowledge {[x["n"] for x in know]}, web {[x["n"] for x in web]}')
    answer = answer_of(events)
    marks = flags_follow_the_answer(run, answer, 'mixed')
    cited_web, cited_know = [x['n'] for x in web if x['cited']], [x['n'] for x in know if x['cited']]
    if not cited_web or not cited_know:
        fail('mixed', f'the answer {sorted(marks)} cites one source of each kind, so both lists have a cited one: web {cited_web}, knowledge {cited_know}')
    invented = sorted(marks - set(by_n))
    if run.get('unverifiedCitations') != invented or not invented or any(n < nxt for n in invented):
        fail('mixed', f'the answer cites {sorted(marks)}; the number nobody gave is {invented}, and unverifiedCitations is {run.get("unverifiedCitations")}')
    if run.get('unlistedCitations'):
        fail('mixed', f'every number the answer cites is listed or invented, yet unlistedCitations is {run["unlistedCitations"]}')
    # what the model was given: the web results are labelled with HOST's numbers; a bracketed number in the knowledge text is neutralised
    heads = {}
    for t in result_texts(events):
        for n, url in re.findall(r'(?m)^\[(\d+)\] [^\n]*\naddress: (\S+)', t):
            heads[url] = int(n)
    by_url = {x['url']: x for x in web}
    if not heads:
        fail('model', 'no web result block with a number and an address is on the stream')
    for url, n in heads.items():
        if url not in by_url or by_url[url]['n'] != n:
            fail('model', f'the model read {url} as [{n}], the source is {by_url.get(url)}: not HOST\'s number')
    texts = '\n'.join(result_texts(events))
    stray = [m for m in re.findall(r'(?m)(?<!^)\[\d+\]', texts)]
    if stray:
        fail('model', f'a bracketed number is left in what the model was given, outside the headers of the blocks: {stray[:5]}')
    if '⟦2⟧' not in texts or '⟦987⟧' not in texts:
        fail('model', 'the knowledge passage says "see [2] and [987]": the model must read it as ⟦2⟧ and ⟦987⟧ (untrusted text is neutralised)')
    seen = sorted(heads.values())
    differ = sum(1 for x in web if x['webN'] != x['n'])
    return (f'knowledge {[x["n"] for x in know]} then web {[x["n"] for x in web]} (webN {[x["webN"] for x in web]}, {differ} differ from HOST\'s), the counter is {nxt}; the answer cites '
            f'{sorted(marks)}: knowledge {cited_know} and web {cited_web} are cited, {invented} is unverified, nothing is unlisted; the model read the web results as {seen} and the passage\'s [2] and [987] as ⟦2⟧ and ⟦987⟧')


def followup(run_path, events_path, before, after, first_path, doc):
    run, events, first = load(run_path), events_of(events_path), load(first_path)
    before, after = int(before), int(after)
    if run.get('status') != 'COMPLETED':
        fail('follow-up', f'the run is {run.get("status")}')
    resolved = [e['value'] for e in events if e.get('type') == 'CUSTOM' and e.get('name') == 'idp.host/context.resolved']
    if len(resolved) != 1 or resolved[0].get('firstIndex') != before:
        fail('follow-up', f'the first package of the follow-up run starts at {[r.get("firstIndex") for r in resolved]}, want {before}: the thread\'s counter after the first run (it restarted at 1 if that is what is shown)')
    web, know = lists_of(run, 'follow-up')
    if web:
        fail('follow-up', f'the follow-up run made no web search, yet its sources are {web}')
    # a passage the thread numbered already (the first run's search found the second document too) keeps its number (D81 a); the start package is not deduplicated and takes new ones
    if not know or not any(x['n'] >= before for x in know) or doc not in {x['documentId'] for x in know}:
        fail('follow-up', f'the follow-up run\'s knowledge sources want new numbers from {before} and the second document {doc}: {know}')
    old = {identity(x): x['n'] for x in (first.get('knowledgeSources') or [])}
    reused = [x['n'] for x in know if x['n'] < before]
    if any(old.get(identity(x)) != x['n'] for x in know if x['n'] < before):
        fail('follow-up', f'a number below {before} that the first run did not give to that passage: {know}')
    by_n = one_number_one_thing([first, run], 'follow-up', same_thing_twice=True)
    if after <= before:
        fail('follow-up', f'the counter did not go on: {before} -> {after}')
    contiguous(by_n, after, 'follow-up')
    answer = answer_of(events)
    marks = flags_follow_the_answer(run, answer, 'follow-up')
    if not marks or any(n >= after for n in marks):
        fail('follow-up', f'the answer cites {sorted(marks)}, want numbers the thread gave (below {after}); the search of the passage the first run numbered gives its old number')
    if run.get('unverifiedCitations') or run.get('unlistedCitations'):
        fail('follow-up', f'nothing is unverified or unlisted: {run.get("unverifiedCitations")} {run.get("unlistedCitations")}')
    return f'the follow-up\'s first package starts at {before} (the first run left the counter there, it did not restart at 1; {reused} are the first run\'s own numbers for passages it had numbered), its sources {[x["n"] for x in know]} continue it, the answer cites {sorted(marks)}, the counter is {after}'


def tree(root_path, events_path, nxt, *child_paths):
    root, events = load(root_path), events_of(events_path)
    kids = [load(p) for p in child_paths]
    nxt = int(nxt)
    for r in [root] + kids:
        if r.get('status') != 'COMPLETED':
            fail('sub-agents', f'run {r.get("runId")} is {r.get("status")}')
    if len(kids) != 2 or any(k.get('parentRunId') != root.get('runId') for k in kids):
        fail('sub-agents', f'want two children of the root: {[k.get("parentRunId") for k in kids]}')
    by_n = one_number_one_thing([root] + kids, 'sub-agents')
    contiguous(by_n, nxt, 'sub-agents')
    mine = []
    for k in kids:
        web, know = lists_of(k, 'sub-agents')
        if not web or not any(x['cited'] for x in web):
            fail('sub-agents', f'a sub-agent searched and read the web and cited it: {web}')
        mine.append({x['n'] for x in web})
        if k.get('unverifiedCitations'):
            fail('sub-agents', f'a sub-agent\'s own numbers are not invented: {k["unverifiedCitations"]}')
    if mine[0] & mine[1]:
        fail('sub-agents', f'the two sub-agents were given the same numbers: {sorted(mine[0] & mine[1])}')
    answer = answer_of(events)
    marks = marks_of(answer)
    theirs = {n for s in mine for n in s}
    from_children = sorted(marks & theirs)
    if len(from_children) < 2 or not marks <= set(by_n):
        fail('sub-agents', f'the parent cites {sorted(marks)}: it repeats the children\'s numbers {sorted(theirs)} (at least one of each) and all of them were given in the thread')
    if root.get('unverifiedCitations'):
        fail('sub-agents', f'the parent citing its children\'s numbers is flagged as inventing: {root["unverifiedCitations"]}')
    own = {x['n'] for x in (root.get('sources') or []) + (root.get('knowledgeSources') or [])}
    if not marks <= own | set(root.get('unlistedCitations') or []):
        fail('sub-agents', f'a number the parent cites is neither in its lists nor unlisted: {sorted(marks - own - set(root.get("unlistedCitations") or []))}')
    return (f'numbers by run: parent {sorted(own)}, children {[sorted(s) for s in mine]}, none repeated and none with two meanings, the counter is {nxt} (highest {nxt - 1}); the parent cites '
            f'{sorted(marks)} (children\'s {from_children}), unverified {root.get("unverifiedCitations") or []}, unlisted {root.get("unlistedCitations") or []}')


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    print({'mixed': mixed, 'followup': followup, 'tree': tree}[cmd](*args))
