"""report.md from recorded results. Only text tables: three repetitions are too few for anything else."""
import json
from pathlib import Path

from . import cases as casesmod
from . import metrics

CAVEAT = """**Read this first.** With three repetitions per case a significance test is not possible, and this report does not
make one: it shows the median, the range and every individual value, and a difference between two reports is a difference
between a few runs, to be read next to the individual values. All runs use one local model, whose output is random: another
model, or the same model on another day, may differ. A delivered file is only checked to be what its name says (a readable
zip with its main part, a PDF with a page, non-empty UTF-8); whether it is good is for a person to judge."""


def load(path) -> tuple[list, dict]:
    p = Path(path)
    recs = []
    f = p / "results.jsonl"
    if f.exists():
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = {}
    if (p / "meta.json").exists():
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    return recs, meta


def by_case(recs: list, agent=None) -> dict:
    """{case: {rep: [records by turn]}}, cases in the order they first appear."""
    out = {}
    for r in recs:
        if agent and r.get("agent") not in (agent, None):
            continue
        out.setdefault(r["case"], {}).setdefault(r["rep"], []).append(r)
    for reps in out.values():
        for rs in reps.values():
            rs.sort(key=lambda r: r["turn"])
    return out


def _sum(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) if xs else None


def rep_metrics(rs: list) -> dict:
    """One repetition (all its turns) as numbers: steps, minutes and input tokens add up over the turns; the largest prompt is
    the largest of the turns."""
    shares = [r.get("max_prompt_share") for r in rs if r.get("max_prompt_share") is not None]
    calls = _sum(r["repeated"]["calls"] for r in rs if r.get("repeated")) or 0
    rep = _sum(r["repeated"]["repeated"] for r in rs if r.get("repeated")) or 0
    spec = rs[0].get("success_spec") or {}
    return {"steps": _sum(r.get("steps") for r in rs), "minutes": _sum(r.get("wall_minutes") for r in rs),
            "input": _sum(r.get("input_tokens") for r in rs), "output": _sum(r.get("output_tokens") for r in rs),
            "max_prompt": max((r.get("max_prompt") or 0 for r in rs), default=None),
            "share": max(shares) if shares else None,
            "repeated": rep, "repeated_share": (rep / calls) if calls else None,
            "success": casesmod.rep_success(spec, rs[0].get("turns_total", len(rs)), rs),
            "statuses": [("skipped" if r.get("outcome") == "skipped" else (r.get("status") or "?") + (f" {r['failure_code']}" if r.get("failure_code") else "")) for r in rs],
            "delivered": any(r.get("files") for r in rs)}


# ---- formatting ----
def n0(x):
    return "n/a" if x is None else f"{x:,.0f}"


def n1(x):
    return "n/a" if x is None else f"{x:,.1f}"


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def pct1(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


METRICS = [("steps", "steps", n0), ("minutes", "minutes", n1), ("input", "input tokens in all", n0), ("share", "largest prompt, share of the window", pct),
           ("repeated_share", "repeated tool calls, share", pct1)]


def cell(mr: dict, fmt) -> str:
    if mr["median"] is None:
        return "n/a"
    return f"{fmt(mr['median'])} ({fmt(mr['min'])} to {fmt(mr['max'])})"


def values(mr: dict, fmt) -> str:
    return ", ".join(fmt(v) for v in mr["values"]) if mr["median"] is not None else "n/a"


def agg(reps: dict) -> dict:
    ms = [rep_metrics(reps[k]) for k in sorted(reps)]
    out = {name: metrics.med_range([m[name] for m in ms]) for name, _, _ in METRICS}
    succ = [m["success"] for m in ms]
    out["_n"] = len(ms)
    out["_success"] = None if all(s is None for s in succ) else sum(1 for s in succ if s)
    out["_statuses"] = [m["statuses"] for m in ms]
    out["_delivered"] = sum(1 for m in ms if m["delivered"])
    return out


def status_summary(reps: dict) -> str:
    c = {}
    for k in sorted(reps):
        last = rep_metrics(reps[k])["statuses"]
        s = " + ".join(last) if len(last) > 1 else last[0]
        c[s] = c.get(s, 0) + 1
    return ", ".join(f"{n} x {s}" for s, n in sorted(c.items(), key=lambda kv: -kv[1]))


def success_text(a: dict) -> str:
    return "no success condition (the history has none)" if a["_success"] is None else f"{a['_success']} of {a['_n']}"


def longest(reps: dict):
    best = None
    for rs in reps.values():
        for r in rs:
            if r.get("series") and (best is None or len(r["series"]) > len(best["series"])):
                best = r
    return best


def series_table(r: dict) -> list:
    """Every 5th step (1, 5, 10 …) and the last, as a text table."""
    ser = r["series"]
    pick = [s for s in ser if s["step"] == 1 or s["step"] % 5 == 0 or s is ser[-1]]
    win = r.get("window")
    # the estimate, level and hint exist only on a run that had the feature on (older result files have none of them)
    ctx = any("estimate" in s for s in ser)
    lines = ["| step | prompt tokens | share of window | completion tokens | latency s | stop |" + (" estimate | level | hint |" if ctx else ""),
             "|---:|---:|---:|---:|---:|---|" + ("---:|---:|:---:|" if ctx else "")]
    for s in pick:
        row = f"| {s['step']} | {s['prompt']:,} | {pct(s['prompt'] / win) if win else 'n/a'} | {s['completion']:,} | {s['latency_ms'] / 1000:.1f} | {s['stop'] or s['error'] or ''} |"
        if ctx:
            row += f" {n0(s.get('estimate'))} | {s.get('level', '-')} | {'H' if s.get('hint') else ''} |"
        lines.append(row)
    if ctx:
        lines.append("")
        lines.append("estimate: the HOST's own count before the call (corrected by the previous step's report); level: the deepest shortening level the step had to reach, 0 when it needed no new cut (a cut stays cut); H: the step was told the window is nearly full.")
    return lines


def event_line(reps: dict) -> str:
    tot = {k: 0 for k in metrics.WATCHED}
    interrupted = 0
    for rs in reps.values():
        for r in rs:
            for k, v in ((r.get("events") or {}).get("watched") or {}).items():
                tot[k] = tot.get(k, 0) + v
            interrupted += (r.get("events") or {}).get("interrupted_calls", 0)
    return ", ".join(f"{k} {v}" for k, v in tot.items()) + f", interrupted calls {interrupted}"


def stop_line(reps: dict) -> str:
    stops = {}
    for rs in reps.values():
        for r in rs:
            for k, v in (r.get("stop_reasons") or {}).items():
                stops[k] = stops.get(k, 0) + v
            for k, v in (r.get("error_codes") or {}).items():
                stops["error " + k] = stops.get("error " + k, 0) + v
    return ", ".join(f"{k} {v}" for k, v in sorted(stops.items())) or "none"


def _turns(reps: dict) -> list:
    return [r for rs in reps.values() for r in rs if r.get("outcome") != "skipped"]


def context_agg(reps: dict) -> dict:
    """The context-view numbers of a case over its turns. Records from before the feature have no "context" and count as off."""
    ts = _turns(reps)
    on = [r["context"] for r in ts if (r.get("context") or {}).get("present")]
    med = lambda k: metrics.med_range([c.get(k) for c in on])["median"]
    errs = [c["estimate_error"] for c in on if c.get("estimate_error")]
    unshown = lambda kind: metrics.med_range([(c.get("bytes_not_shown") or {}).get(kind) for c in on])["median"]
    return {"turns": len(ts), "on": len(on), "cut_steps": med("steps_with_new_cut"), "max_level": max((c.get("max_level", 0) for c in on), default=None),
            "shortened_last": med("shortened_last"), "unshown_result": unshown("result"), "unshown_arguments": unshown("arguments"),
            "hint_steps": med("steps_with_hint"), "err_median": metrics.med_range([e["median"] for e in errs])["median"],
            "err_largest": max((e["largest"] for e in errs), key=abs, default=None),
            "windows": sorted({c["window_used"] for c in on if c.get("window_used")}), "warnings": [w for c in on for w in c.get("warnings", [])]}


def spct(x):
    return "n/a" if x is None else f"{100 * x:+.1f}%"


def feature_text(c: dict) -> str:
    return "no turns" if not c["turns"] else f"{'on' if c['on'] == c['turns'] else 'off' if not c['on'] else 'mixed'} ({c['on']} of {c['turns']} turns have context.view events)"


CONTEXT_ROWS = [("steps with a new cut (median per turn)", "cut_steps", n0), ("deepest level reached", "max_level", n0),
                ("items shown shortened at the last step (median)", "shortened_last", n0), ("bytes not shown, results (median)", "unshown_result", n0),
                ("bytes not shown, arguments (median)", "unshown_arguments", n0), ("steps with the hint (median)", "hint_steps", n0),
                ("estimate against reported prompt tokens, median error", "err_median", spct), ("estimate error, largest", "err_largest", spct)]


def context_block(c: dict, o=None, name="", other="") -> list:
    """The short context-view block of a case; with o, the two evaluations side by side."""
    if o is None:
        L = [f"Context view (idp-host D52): feature {feature_text(c)}."]
        if c["on"]:
            L += ["", "| numbers from the events | value |", "|---|---|"]
            L += [f"| {label} | {fmt(c[k])} |" for label, k, fmt in CONTEXT_ROWS]
            L.append(f"| window the HOST used | {', '.join(n0(w) for w in c['windows'])} |")
        return L + [f"- WARNING: {w}" for w in sorted(set(c["warnings"]))]
    L = [f"Context view (idp-host D52): feature {name}: {feature_text(c)}; {other}: {feature_text(o)}.", "",
         f"| numbers from the events | {name} | {other} | difference |", "|---|---|---|---|",
         f"| feature | {'on' if c['on'] else 'off'} | {'on' if o['on'] else 'off'} | |"]
    for label, k, fmt in CONTEXT_ROWS:
        x, y = c[k], o[k]
        d = "n/a" if x is None or y is None else (f"{(x - y) * 100:+.1f} points" if fmt is spct else f"{x - y:+,.0f}")
        L.append(f"| {label} | {fmt(x)} | {fmt(y)} | {d} |")
    return L + [f"- WARNING ({n}): {w}" for n, cc in ((name, c), (other, o)) for w in sorted(set(cc["warnings"]))]


def meta_lines(meta: dict) -> list:
    out = []
    if meta.get("source") == "history":
        out.append(f"- From a dump of an earlier platform: `{meta.get('dump')}` ({meta.get('runs')} runs, models {', '.join(meta.get('models', [])) or 'unknown'}).")
    else:
        ag = meta.get("agent") or {}
        out.append(f"- Agent `{ag.get('name')}`, model {json.dumps(ag.get('model'))}, limits {json.dumps(ag.get('limits'))}.")
    w = meta.get("window") or {}
    out.append(f"- Context window: {n0(w.get('value'))} tokens ({w.get('source', 'unknown')}).")
    v = meta.get("versions") or {}
    if v:
        out.append("- Components: " + "; ".join(f"{k} checkout {d.get('checkout')}, reported {d.get('reported')}" for k, d in v.items() if isinstance(d, dict) and ("checkout" in d or "reported" in d)) + ".")
    return out


def render(name: str, recs: list, meta: dict, other_name=None, other_recs=None, agent=None) -> str:
    cases = by_case(recs, agent)
    others = by_case(other_recs, agent) if other_recs is not None else None
    L = [f"# Long-task evaluation: {name}" + (f" against {other_name}" if others is not None else ""), "", CAVEAT, ""]
    L += meta_lines(meta)
    L.append(f"- {len(recs)} turn records, {len(cases)} cases.")
    if others is not None:
        L.append(f"- Differences are `{name}` minus `{other_name}`.")
    L += ["", "## Summary", ""]
    L += ["| case | repetitions | success | outcomes | steps, median | minutes, median | input tokens, median | largest prompt share, median |", "|---|---:|---|---|---:|---:|---:|---:|"]
    for cid, reps in cases.items():
        a = agg(reps)
        L.append(f"| {cid} | {a['_n']} | {success_text(a)} | {status_summary(reps)} | {n0(a['steps']['median'])} | {n1(a['minutes']['median'])} | {n0(a['input']['median'])} | {pct(a['share']['median'])} |")
    for cid, reps in cases.items():
        a = agg(reps)
        first = reps[sorted(reps)[0]][0]
        L += ["", f"## {cid}: {first.get('title') or ''}", ""]
        base = next((r.get("baseline") for rs in reps.values() for r in rs if r.get("baseline")), "")
        L.append(f"- Recorded baseline: {base or 'none recorded'}")
        L.append(f"- Success: {success_text(a)}. Outcomes: {status_summary(reps)}. Runs that delivered a file: {a['_delivered']} of {a['_n']}.")
        L.append(f"- Model calls ended as: {stop_line(reps)}. Events: {event_line(reps)}.")
        o = others.get(cid) if others is not None else None
        if others is not None and o is None:
            L.append(f"- `{other_name}` has no case {cid}; nothing to compare.")
        L.append("")
        if o is None:
            L += ["| metric | median (range) | individual values |", "|---|---|---|"]
            for name_, label, fmt in METRICS:
                L.append(f"| {label} | {cell(a[name_], fmt)} | {values(a[name_], fmt)} |")
        else:
            b = agg(o)
            L.append(f"- {other_name}: success {success_text(b)}; outcomes {status_summary(o)}.")
            L += ["", f"| metric | {name} median (range) | {other_name} median (range) | difference of medians | {name} values | {other_name} values |", "|---|---|---|---|---|---|"]
            for name_, label, fmt in METRICS:
                x, y = a[name_]["median"], b[name_]["median"]
                d = "n/a" if x is None or y is None else (f"{(x - y) * 100:+.1f} points" if name_ in ("share", "repeated_share") else f"{x - y:+,.1f}" + (f" ({(x - y) / y * 100:+.0f}%)" if y else ""))
                L.append(f"| {label} | {cell(a[name_], fmt)} | {cell(b[name_], fmt)} | {d} | {values(a[name_], fmt)} | {values(b[name_], fmt)} |")
        L += [""] + context_block(context_agg(reps), context_agg(o) if o is not None else None, name, other_name)
        multi = [r for rs in reps.values() for r in rs if r.get("turns_total", 1) > 1]
        if multi:
            L += ["", "Per turn (median over repetitions):", "", "| turn | steps | minutes | input tokens | largest prompt share | outcomes |", "|---:|---:|---:|---:|---:|---|"]
            for t in sorted({r["turn"] for r in multi}):
                rs = [r for r in multi if r["turn"] == t]
                m = lambda k: metrics.med_range([r.get(k) for r in rs])["median"]
                L.append(f"| {t} | {n0(m('steps'))} | {n1(m('wall_minutes'))} | {n0(m('input_tokens'))} | {pct(m('max_prompt_share'))} | {', '.join(sorted({(r.get('status') or 'skipped') for r in rs}))} |")
        lg = longest(reps)
        if lg:
            L += ["", f"Prompt tokens per step, the longest run ({lg['run_id']}, repetition {lg['rep']}, turn {lg['turn']}, {len(lg['series'])} steps, every 5th step and the last):", ""]
            L += series_table(lg)
    return "\n".join(L) + "\n"


def write(dir_: Path, other_dir=None, agent=None) -> Path:
    recs, meta = load(dir_)
    if not recs:
        raise ValueError(f"no results in {dir_}")
    orecs = None
    if other_dir is not None:
        orecs, _ = load(other_dir)
        if not orecs:
            raise ValueError(f"no results in {other_dir}")
    text = render(Path(dir_).name, recs, meta, Path(other_dir).name if other_dir else None, orecs, agent)
    p = Path(dir_) / "report.md"
    p.write_text(text, encoding="utf-8")
    return p
