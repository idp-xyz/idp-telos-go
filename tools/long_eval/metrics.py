"""From the HOST's rows of one run to one result record. Pure functions, so that the same code reads a live platform and a
dump, and the tests need neither."""
import json
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
 'steps', (SELECT coalesce(json_agg(row_to_json(s) ORDER BY s.step), '[]'::json) FROM
          (SELECT step, model, prompt_tokens, completion_tokens, latency_ms, stop_reason, error_code
           FROM run_steps WHERE run_id = '{run_id}') s),
 'tool_calls', (SELECT coalesce(json_agg(row_to_json(t) ORDER BY t.created_at, t.step, t.id), '[]'::json) FROM
          (SELECT step, tool, arguments, state, is_error, created_at, id,
                  coalesce(left(result, 60) LIKE '%interrupted:%', false) AS interrupted
           FROM tool_calls WHERE run_id = '{run_id}') t),
 'events', (SELECT coalesce(json_agg(row_to_json(e) ORDER BY e.seq), '[]'::json) FROM
          (SELECT seq, payload->>'name' AS name, payload->'value' AS value FROM run_events
           WHERE run_id = '{run_id}' AND type = 'CUSTOM' AND payload->>'name' <> 'idp.host/model.usage') e),
 'usage_events', (SELECT count(*) FROM run_events WHERE run_id = '{run_id}' AND type = 'CUSTOM'
                  AND payload->>'name' = 'idp.host/model.usage'))"""


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
             "stop": s.get("stop_reason"), "error": s.get("error_code")} for s in sorted(steps, key=lambda s: s["step"])]


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


def repeated_work(calls: list) -> dict:
    """Identical tool calls (same tool, same canonical arguments) made more than once in a run. "repeated" counts every call
    after the first of its kind, so three identical calls are two repeats; share = repeated / all calls. A failed or denied
    call counts too: the model asked for the same thing again either way."""
    seen = {}
    for c in calls:
        k = (c["tool"], canonical_args(c.get("arguments", "")))
        seen[k] = seen.get(k, 0) + 1
    rep = sum(n - 1 for n in seen.values())
    top = sorted(((n, t) for (t, _), n in seen.items() if n > 1), reverse=True)[:5]
    return {"calls": len(calls), "repeated": rep, "share": round(rep / len(calls), 4) if calls else 0.0,
            "top": [{"tool": t, "times": n} for n, t in top]}


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
           "events": ev, "delivered_events": delivered_events(rows.get("events", []))}
    return rec


def med_range(values) -> dict:
    """median, min, max of the values that exist (None is skipped); None for all when there are none."""
    v = [x for x in values if x is not None]
    if not v:
        return {"median": None, "min": None, "max": None, "values": []}
    return {"median": median(v), "min": min(v), "max": max(v), "values": list(values)}
