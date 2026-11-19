"""The evaluation loop: for each repetition and each case a new conversation; each turn is uploaded, asked, followed to the
end of its run, and recorded in results.jsonl the moment it ends, so that a stopped evaluation continues under the same name.

The platform is a parameter (live.Platform, or a double in the tests), and so are sleep and the clock."""
import json
import re
import time
import uuid
from pathlib import Path

from . import cases as casesmod
from . import files as filesmod
from . import metrics
from .live import TERMINAL, PlatformError

OUT = Path(__file__).resolve().parents[2] / ".local" / "long-eval"


def key(case: str, rep: int, turn: int) -> str:
    return f"{case}|{rep}|{turn}"


def read_results(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{path} line {n} is not JSON ({e}); a line is written whole, so the file was edited or damaged")
    return out


def append_result(path: Path, rec: dict):
    # one line, flushed, in append mode: a stop between two turns loses nothing that had ended
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()


def poll_interval(elapsed: float) -> float:
    """Gently: a run that has just started changes quickly, a run in its fortieth minute does not."""
    return 5.0 if elapsed < 60 else 15.0 if elapsed < 600 else 30.0


def follow(plat, run_id: str, args, sleep=time.sleep, clock=time.monotonic, log=print) -> dict:
    """Waits for the run to end. Returns {"outcome": "ended" | "timeout" | "awaiting_confirmation", "status", "confirmations": [...]}.
    Nothing is rejected: without --confirm a confirmation is recorded and waited for (args.confirm_wait minutes), then the run is
    left as it is. A run that is still going when args.turn_timeout minutes have passed is canceled, so that the model is not
    kept busy for the next case, and recorded as a timeout."""
    start = clock()
    seen, confirmations = set(), []
    waiting_since = None
    errors = 0
    status = None
    while True:
        elapsed = clock() - start
        try:
            status = plat.run_status(run_id).get("status")
            errors = 0
            if status in TERMINAL:
                return {"outcome": "ended", "status": status, "confirmations": confirmations}
            if status == "AWAITING_INPUT":
                for c in plat.pending_confirmations(run_id):
                    if c["id"] in seen:
                        continue
                    seen.add(c["id"])
                    rec = {"tool_call": c["id"], "tool": c["tool"], "arguments": c["arguments"],
                           "after_minutes": round(elapsed / 60, 2), "decision": None}
                    if args.confirm:
                        plat.decide(run_id, c["id"], "confirm")
                        rec["decision"] = "confirm"
                    log(f"    {run_id}: confirmation asked for {c['tool']}" + (" (confirmed)" if args.confirm else " (not answered: --confirm is off)"))
                    confirmations.append(rec)
                if not args.confirm:
                    waiting_since = clock() if waiting_since is None else waiting_since
                    if clock() - waiting_since > args.confirm_wait * 60:
                        return {"outcome": "awaiting_confirmation", "status": status, "confirmations": confirmations}
                else:
                    waiting_since = None
            else:
                waiting_since = None
        except (PlatformError, OSError) as e:
            # a poll that fails is not a run that failed: the platform may be busy; ten in a row is something else
            errors += 1
            log(f"    poll of {run_id} failed ({errors}): {str(e)[:150]}")
            if errors >= 10:
                raise PlatformError(f"ten polls of run {run_id} failed in a row; the run may still be going. Last: {e}")
        if elapsed > args.turn_timeout * 60:
            plat.cancel(run_id)
            return {"outcome": "timeout", "status": status, "confirmations": confirmations}
        sleep(poll_interval(elapsed))


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)[:120] or "file"


def collect_files(plat, run_id: str, dest: Path) -> list:
    """The delivered files, downloaded, saved under dest, and checked; the bytes themselves are not kept in the record."""
    import hashlib
    out = []
    for f in plat.delivered_files(run_id):
        data = f["data"]
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / safe_name(f["name"])
        n = 1
        while p.exists():  # two files of one name in one run must not overwrite each other
            n += 1
            p = dest / f"{n}-{safe_name(f['name'])}"
        p.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        out.append({"name": f["name"], "size": len(data), "sha256": sha, "listed_sha256_matches": (f.get("listed_sha256") in (None, sha)),
                    "saved": str(p), "sanity": filesmod.check(f["name"], data)})
    return out


def fetch_rows(psql, run_id: str) -> dict:
    out = psql(metrics.run_query(run_id)).strip()
    rows = json.loads(out) if out else {}
    if not rows.get("run"):
        raise PlatformError(f"the HOST's database has no run {run_id}")
    return rows


def run_turn(plat, case, turn_no: int, rep: int, thread: str, args, outdir: Path, window, model, sleep=time.sleep, clock=time.monotonic, log=print) -> dict:
    turn = case.turns[turn_no - 1]
    ids = [plat.upload(p) for p in turn.attach]
    t0 = clock()
    inv = plat.invoke(turn.say, ids, thread_id=thread)
    log(f"  {case.id} rep {rep} turn {turn_no}/{len(case.turns)}: run {inv['run']} started")
    res = follow(plat, inv["run"], args, sleep, clock, log)
    rows = fetch_rows(plat.psql, inv["run"])
    rec = metrics.build_record(rows, window, key=key(case.id, rep, turn_no), case=case.id, title=case.title, rep=rep, turn=turn_no,
                               turns_total=len(case.turns), source="live", model=model, outcome=res["outcome"],
                               confirmations=res["confirmations"], waited_minutes=round((clock() - t0) / 60, 2),
                               success_spec=case.success, baseline=case.baseline, attached=[Path(a).name for a in turn.attach])
    try:
        rec["files"] = collect_files(plat, inv["run"], outdir / "files" / f"{case.id}-r{rep}-t{turn_no}")
    except (PlatformError, OSError) as e:
        rec["files"] = []
        rec["files_error"] = str(e)[:300]
    rec["success"] = casesmod.turn_success(case.success, rec["status"], rec["files"])
    return rec


def skipped(case, turn_no: int, rep: int, thread, why: str) -> dict:
    return {"key": key(case.id, rep, turn_no), "case": case.id, "title": case.title, "rep": rep, "turn": turn_no, "turns_total": len(case.turns),
            "source": "live", "thread_id": thread, "run_id": None, "status": None, "outcome": "skipped", "skipped_because": why,
            "files": [], "series": [], "success": False if case.success else None, "success_spec": case.success, "baseline": case.baseline}


def evaluate(plat, cases, args, outdir: Path, window, model, sleep=time.sleep, clock=time.monotonic, log=print) -> int:
    """Runs what is not yet in results.jsonl; returns the number of turns it ran."""
    path = outdir / "results.jsonl"
    done = {r["key"]: r for r in read_results(path)}
    ran = 0
    for rep in range(1, args.runs + 1):
        for case in cases:
            thread, stop = None, None
            for t in range(1, len(case.turns) + 1):
                k = key(case.id, rep, t)
                if k in done:
                    thread = done[k].get("thread_id") or thread
                    if done[k].get("outcome") in ("timeout", "awaiting_confirmation", "skipped"):
                        stop = f"turn {t} did not end"
                    continue
                if stop:
                    rec = skipped(case, t, rep, thread, stop)
                    append_result(path, rec); done[k] = rec
                    continue
                if thread is None:
                    thread = f"le-{uuid.uuid4().hex[:12]}-{re.sub(r'[^A-Za-z0-9_.-]', '', case.id)}-r{rep}"[:64]
                rec = run_turn(plat, case, t, rep, thread, args, outdir, window, model, sleep, clock, log)
                thread = rec.get("thread_id") or thread
                append_result(path, rec); done[k] = rec; ran += 1
                log(f"  {case.id} rep {rep} turn {t}: {rec['status']} ({rec['outcome']}), {rec['steps']} steps, {rec['wall_minutes']} min, "
                    f"{rec['input_tokens']:,} input tokens, files {[f['name'] for f in rec['files']]}, success {rec['success']}")
                if rec["outcome"] != "ended":
                    stop = f"turn {t} ended as {rec['outcome']}"
    return ran
