"""From the HOST's rows of one run to one result record. Pure functions, so that the same code reads a live platform and a
dump, and the tests need neither."""
import json
import math
import re
from datetime import datetime
from statistics import median

# the custom events the proposal's section 4 asks about; every other idp.host event is counted too, under its own name
WATCHED = ("model.retry", "tool.denied", "tool.interrupted", "context.withheld", "guard.flagged", "file.delivered")
PREFIX = "idp.host/"

# the single query that gets everything a record needs about one run; the run id is checked before it is put in
RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def run_query(run_id: str) -> str:
    if not RUN_ID.match(run_id):
        raise ValueError(f"not a run id: {run_id!r}")
    return f"""SELECT json_build_object(
 'run', (SELECT row_to_json(x) FROM (SELECT r.id, r.thread_id, r.status, r.failure_code, r.step_count, r.tokens_used,
          r.created_at, r.started_at, r.finished_at, r.deadline_at, a.name AS agent
          FROM runs r JOIN agents a ON a.id = r.agent_id WHERE r.id = '{run_id}') x),
 'steps', (SELECT coalesce(json_agg(s.j ORDER BY (s.j->>'step')::int), '[]'::json) FROM
          (SELECT to_jsonb(r) - 'tenant_id' - 'run_id' AS j FROM run_steps r WHERE r.run_id = '{run_id}') s),
 'tool_calls', (SELECT coalesce(json_agg(row_to_json(t) ORDER BY t.created_at, t.step, t.id), '[]'::json) FROM
          (SELECT step, tool, arguments, state, is_error, created_at, id,
                  coalesce(left(result, 60) LIKE '%interrupted:%', false) AS interrupted
           FROM tool_calls WHERE run_id = '{run_id}') t),
 'events', (SELECT coalesce(json_agg(row_to_json(e) ORDER BY e.seq), '[]'::json) FROM
          (SELECT seq, payload->>'name' AS name, payload->'value' AS value FROM run_events
           WHERE run_id = '{run_id}' AND type = 'CUSTOM' AND payload->>'name' <> 'idp.host/model.usage') e),
 'usage_events', (SELECT count(*) FROM run_events WHERE run_id = '{run_id}' AND type = 'CUSTOM'
                  AND payload->>'name' = 'idp.host/model.usage'))"""


# `steps` is read as the whole row (to_jsonb), not as a list of named columns: a database or a dump from before idp-host D54 has no
# timing columns, and a query that names a column that is not there fails. A column that is not there is simply a key that is not
# in the row, and the series below reads it as "not known".


def parse_time(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    # fromisoformat of older Pythons wants exactly 3 or 6 fractional digits; PostgreSQL may print fewer
    s = re.sub(r"\.(\d{1,6})(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), s)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def wall_minutes(started, finished):
    a, b = parse_time(started), parse_time(finished)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 60, 2)


def series(steps: list) -> list:
    """The per-step series, one dict per model call in step order."""
    return [{"step": s["step"], "prompt": s["prompt_tokens"], "completion": s["completion_tokens"], "latency_ms": s["latency_ms"],
             "stop": s.get("stop_reason"), "error": s.get("error_code"),
             # idp-host D54, None = not known (an older HOST, a non-streamed answer, a provider that does not say)
             "first_output_ms": s.get("first_output_ms"), "first_text_ms": s.get("first_text_ms"), "after_first_ms": s.get("after_first_ms"),
             "cached": s.get("cached_tokens"), "cache_creation": s.get("cache_creation_tokens"),
             "server_prompt_n": s.get("server_prompt_n"), "server_prompt_ms": s.get("server_prompt_ms"), "server_cache_n": s.get("server_cache_n"),
             "server_predicted_n": s.get("server_predicted_n"), "server_predicted_ms": s.get("server_predicted_ms")}
            for s in sorted(steps, key=lambda s: s["step"])]


def percentile(values, q):
    """The q-quantile (0 to 1) by nearest rank: the smallest value that at least a share q of the values are not above. None for
    no values. Three or ten steps are too few for interpolation to mean anything."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = max(1, math.ceil(round(q * len(v), 9)))
    return v[min(k, len(v)) - 1]


def timing_stats(steps: list) -> dict:
    """What the steps (series items, any number of runs pooled) say about the wait before the first output and about the reuse of
    the prompt (idp-host D54). Every number is over the steps for which it is known, and says over how many.
    first_output: median and p90 of the time from the request being sent to the first output of any kind, in ms, and the same
    for its share of the step's latency. reuse: prompt tokens reused against processed, over the steps where the provider said
    how many were reused (llama.cpp's timings.cache_n, else usage cached_tokens); processed is timings.prompt_n where there is
    one, else the prompt minus the reused. server_prompt: the provider's own prompt-processing time (timings.prompt_ms), and its
    share of the step's latency."""
    n = len(steps)
    fo = [s.get("first_output_ms") for s in steps if s.get("first_output_ms") is not None]
    fo_share = [s["first_output_ms"] / s["latency_ms"] for s in steps if s.get("first_output_ms") is not None and s.get("latency_ms")]
    reused = processed = prompt = known = 0
    for s in steps:
        r = s.get("server_cache_n")
        if r is None:
            r = s.get("cached")
        if r is None:
            continue
        known += 1
        reused += r
        prompt += s.get("prompt") or 0
        p = s.get("server_prompt_n")
        processed += p if p is not None else max(0, (s.get("prompt") or 0) - r)
    sp = [s.get("server_prompt_ms") for s in steps if s.get("server_prompt_ms") is not None]
    sp_share = [s["server_prompt_ms"] / s["latency_ms"] for s in steps if s.get("server_prompt_ms") is not None and s.get("latency_ms")]
    return {"steps": n,
            "first_output": {"n": len(fo), "median": median(fo) if fo else None, "p90": percentile(fo, 0.9),
                             "share_median": median(fo_share) if fo_share else None, "share_p90": percentile(fo_share, 0.9)},
            "reuse": {"n": known, "reused": reused, "processed": processed, "prompt": prompt, "share": (reused / prompt) if prompt else None},
            "server_prompt": {"n": len(sp), "median": median(sp) if sp else None, "p90": percentile(sp, 0.9),
                              "share_median": median(sp_share) if sp_share else None}}


def totals(ser: list, window=None) -> dict:
    """Input and output tokens in all, the largest prompt (and where), and how the calls ended."""
    stops, errors = {}, {}
    for s in ser:
        if s["stop"]:
            stops[s["stop"]] = stops.get(s["stop"], 0) + 1
        if s["error"]:
            errors[s["error"]] = errors.get(s["error"], 0) + 1
    big = max(ser, key=lambda s: s["prompt"], default=None)
    out = {"input_tokens": sum(s["prompt"] for s in ser), "output_tokens": sum(s["completion"] for s in ser),
           "max_prompt": big["prompt"] if big else 0, "max_prompt_step": big["step"] if big else None,
           "model_ms": sum(s["latency_ms"] for s in ser), "stop_reasons": stops, "error_codes": errors}
    out["max_prompt_share"] = round(out["max_prompt"] / window, 4) if window else None
    return out


def canonical_args(text: str) -> str:
    """The arguments as one canonical string, so that two calls that differ only in key order or spacing are the same call."""
    try:
        return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return text if isinstance(text, str) else repr(text)


# Delivery tools whose effect depends on a file that may have changed between two calls with the same arguments: exporting
# "report.xlsx" again after the file was revised is the same call and a different delivery. They are left out of the strict
# count of repeated work (see the README). Matched on the tool's own name, the part after the gateway prefix ("sandbox__"). To
# exclude another tool, add it here and name it in the README, in the section on repeated work.
DELIVERY_TOOLS = ("sandbox_export_file",)

# a tool call whose state is this, and which is not flagged as an error, succeeded (idp-host's tool call states)
SUCCEEDED_STATE = "DONE"


def _own_name(tool: str) -> str:
    return tool.rsplit("__", 1)[-1]


def _succeeded(c: dict) -> bool:
    return c.get("state") == SUCCEEDED_STATE and not c.get("is_error")


def strict_repeats(calls: list) -> dict:
    """Calls that redo work the run had already done: the same tool and canonical arguments as an EARLIER call that SUCCEEDED, made
    at a LATER step (so the model had that result before it asked again; two identical calls of one step are not a redo), and not
    a delivery tool (DELIVERY_TOOLS). Calls of a tool the agent does not have, calls refused or failed, and a call repeated after a
    failure are therefore not counted: the first one gave the model nothing to remember. Needs the calls in the order they were
    made; a call with no step is ordered by its place in the list."""
    use_step = all(isinstance(c.get("step"), int) for c in calls)
    first_ok, rep, per = {}, 0, {}
    for i, c in enumerate(calls):
        if _own_name(c["tool"]) in DELIVERY_TOOLS:
            continue
        k = (c["tool"], canonical_args(c.get("arguments", "")))
        at = c["step"] if use_step else i
        if k in first_ok and first_ok[k] < at:
            rep += 1
            per[k] = per.get(k, 0) + 1
        if _succeeded(c) and k not in first_ok:
            first_ok[k] = at
    top = sorted(((n, t) for (t, _), n in per.items()), reverse=True)[:5]
    return {"repeated": rep, "share": round(rep / len(calls), 4) if calls else 0.0, "top": [{"tool": t, "times": n} for n, t in top]}


def repeated_work(calls: list) -> dict:
    """Identical tool calls (same tool, same canonical arguments) made more than once in a run. "repeated" counts every call
    after the first of its kind, so three identical calls are two repeats; share = repeated / all calls. A failed or denied
    call counts too: the model asked for the same thing again either way. This is the original count, kept as it was so that
    earlier reports stay comparable; "strict" is the refined one (strict_repeats), which says what the original was meant to
    say: a model that redoes work because it lost what it had done."""
    seen = {}
    for c in calls:
        k = (c["tool"], canonical_args(c.get("arguments", "")))
        seen[k] = seen.get(k, 0) + 1
    rep = sum(n - 1 for n in seen.values())
    top = sorted(((n, t) for (t, _), n in seen.items() if n > 1), reverse=True)[:5]
    return {"calls": len(calls), "repeated": rep, "share": round(rep / len(calls), 4) if calls else 0.0,
            "top": [{"tool": t, "times": n} for n, t in top], "strict": strict_repeats(calls)}


def tool_counts(calls: list) -> dict:
    out = {}
    for c in calls:
        by = out.setdefault(c["tool"], {})
        by[c["state"]] = by.get(c["state"], 0) + 1
    return out


def event_counts(events: list, usage_events: int = 0) -> dict:
    """{"watched": counts of the events that matter (0 when absent), "all": every idp.host event by name}. tool.interrupted
    is in the list because the proposal names it; the HOST records an interrupted call as a FAILED tool call and not as an
    event, so "interrupted_calls" counts those from the tool calls instead (see the README)."""
    allc = {}
    for e in events:
        n = (e.get("name") or "")
        n = n[len(PREFIX):] if n.startswith(PREFIX) else n
        allc[n] = allc.get(n, 0) + 1
    if usage_events:
        allc["model.usage"] = usage_events
    return {"watched": {w: allc.get(w, 0) for w in WATCHED},
            "all": dict(sorted(allc.items()))}


def delivered_events(events: list) -> list:
    out = []
    for e in events:
        if e.get("name") == PREFIX + "file.delivered" and isinstance(e.get("value"), dict):
            v = e["value"]
            out.append({"name": v.get("name"), "size": v.get("size"), "sha256": v.get("sha256")})
    return out


def view_events(events: list) -> list:
    """The payloads of the idp.host/context.view events (idp-host D52), in step order; none when the HOST's model has no window
    configured, which is how a run without the feature looks."""
    out = []
    for e in events:
        if e.get("name") == PREFIX + "context.view" and isinstance(e.get("value"), dict):
            out.append(e["value"])
    return sorted(out, key=lambda v: v.get("step", 0))


def _signed_error(estimate, reported):
    return (estimate - reported) / reported if reported and estimate is not None else None


def context_view(events: list, ser: list, window=None) -> dict:
    """What the HOST says it showed the model, from the context.view events, read next to the provider's reported prompt
    tokens. {"present": False} when there are no such events (the feature is off for this model, or an older HOST).

    level is the deepest level a step had to reach and is 0 on a step that needed no new cut, because shortening is sticky:
    a cut stays cut at the steps after it. So "steps with a new cut" counts steps with level > 0 and is not the number of steps
    that were shown shortened; the items shortened at the last step say how much was cut by the end. A step whose payload has
    itemsAsBefore shows the items of the step before it. Bytes not shown = the item's bytes minus what was shown, by kind
    (result or arguments). An estimate is the HOST's own count before the call, so the error against the provider's count is
    the estimator's error on this run, signed: positive is an estimate above the provider's."""
    views = view_events(events)
    if not views:
        return {"present": False}
    reported = {s["step"]: s["prompt"] for s in ser}
    items, per_step, errs, raw_errs, warnings = [], [], [], [], []
    for v in views:
        if isinstance(v.get("items"), list):
            items = v["items"]
        err = _signed_error(v.get("estimateTokens"), reported.get(v.get("step")))
        rerr = _signed_error(v.get("rawEstimateTokens"), reported.get(v.get("step")))
        if err is not None:
            errs.append(err)
        if rerr is not None:
            raw_errs.append(rerr)
        per_step.append({"step": v.get("step"), "estimate": v.get("estimateTokens"), "raw": v.get("rawEstimateTokens"), "factor": v.get("factor"),
                         "level": v.get("level", 0), "hint": bool(v.get("hint")), "shortened": v.get("shortened", 0), "error": err})
    unshown = {"result": 0, "arguments": 0}
    for it in items:
        if isinstance(it, list) and len(it) >= 5 and it[0] in unshown:
            unshown[it[0]] += max(0, (it[2] or 0) - (it[4] or 0))
    used = views[-1].get("windowTokens")
    if window and used and used != window:
        warnings.append(f"the HOST used a window of {used} tokens and the evaluation was told {window}; shares of the window are against the latter")

    def stats(xs):
        if not xs:
            return None
        v = sorted(xs)
        return {"n": len(v), "median": median(v), "largest": max(v, key=abs), "min": v[0], "max": v[-1]}
    return {"present": True, "steps_with_view": len(views), "window_used": used, "mark_tokens": views[-1].get("markTokens"),
            "steps_with_new_cut": sum(1 for p in per_step if p["level"] > 0), "max_level": max((p["level"] for p in per_step), default=0),
            "shortened_last": views[-1].get("shortened", 0), "items_last": len(items), "bytes_not_shown": unshown,
            "steps_with_hint": sum(1 for p in per_step if p["hint"]), "estimate_error": stats(errs), "raw_estimate_error": stats(raw_errs),
            "per_step": per_step, "warnings": warnings}


def build_record(rows: dict, window=None, **extra) -> dict:
    """One result record from the rows of run_query. extra: case, rep, turn and the like, put in as they are."""
    run = rows.get("run") or {}
    ser = series(rows.get("steps", []))
    calls = rows.get("tool_calls", [])
    ev = event_counts(rows.get("events", []), rows.get("usage_events", 0))
    ev["interrupted_calls"] = sum(1 for c in calls if c.get("interrupted"))
    rec = {**extra,
           "run_id": run.get("id"), "thread_id": run.get("thread_id"), "agent": run.get("agent"),
           "status": run.get("status"), "failure_code": run.get("failure_code"),
           "steps": run.get("step_count"), "wall_minutes": wall_minutes(run.get("started_at"), run.get("finished_at")),
           "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
           "tokens_used": run.get("tokens_used"),
           "series": ser, **totals(ser, window), "window": window,
           "tool_calls": tool_counts(calls), "repeated": repeated_work(calls),
           "events": ev, "delivered_events": delivered_events(rows.get("events", [])),
           "context": context_view(rows.get("events", []), ser, window)}
    # the estimate, level and hint of a step sit beside its prompt tokens, where the per-step table reads them
    by_step = {p["step"]: p for p in rec["context"].pop("per_step", [])}
    for s in ser:
        p = by_step.get(s["step"])
        if p:
            s.update({"estimate": p["estimate"], "level": p["level"], "hint": p["hint"]})
    return rec


def med_range(values) -> dict:
    """median, min, max of the values that exist (None is skipped); None for all when there are none."""
    v = [x for x in values if x is not None]
    if not v:
        return {"median": None, "min": None, "max": None, "values": []}
    return {"median": median(v), "min": min(v), "max": max(v), "values": list(values)}
