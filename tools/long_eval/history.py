"""The same per-run numbers from a pg_dump of an earlier platform: the dump is restored into a throwaway PostgreSQL container
(no network, no published port, removed at the end), every run is read with the query the live evaluation uses, and the
records are written in the same results format, so that one report reads both.

The cases of that time are not known. Runs are grouped by the first user message of the run (thread_messages), and each group
is a case "H01", "H02" … in the order of its first run. With --cases, a group whose message is exactly the one turn of a
single-turn case of the case file takes that case's id and baseline text, so that the recorded history and a new measurement
of the same case line up in a comparison. A dump holds no file content, so no success condition is evaluated: success is None,
and the report shows how the runs ended and which delivered a file."""
import json
import subprocess
import time
import uuid
from pathlib import Path

from . import metrics
from .live import PlatformError, psql_in

IMAGE = "public.ecr.aws/docker/library/postgres:17-alpine"
DEFAULT_WINDOW = 131072  # the model's window in the proposal's section 1.1; the dump itself does not say

LIST = """SELECT coalesce(json_agg(row_to_json(x) ORDER BY x.created_at, x.id), '[]'::json) FROM (
  SELECT r.id, r.thread_id, r.created_at, a.name AS agent,
         (SELECT m.message->>'content' FROM thread_messages m
           WHERE m.thread_id = r.thread_id AND m.run_id = r.id AND m.message->>'role' = 'user' ORDER BY m.seq LIMIT 1) AS first_user
  FROM runs r JOIN agents a ON a.id = r.agent_id) x"""


def docker(*args, input=None, timeout=300):
    return subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout)


class Throwaway:
    """A PostgreSQL container that exists for the length of the with block."""

    def __init__(self, image=IMAGE):
        self.image = image
        self.name = f"long-eval-history-{uuid.uuid4().hex[:10]}"
        self.password = uuid.uuid4().hex

    def __enter__(self):
        if docker("image", "inspect", self.image).returncode:
            raise PlatformError(f"the image {self.image} is not here, and this tool does not pull it: pull it first (tools/pull_image.sh)")
        r = docker("run", "-d", "--rm", "--pull", "never", "--network", "none", "--name", self.name,
                   "-e", "POSTGRES_PASSWORD=" + self.password, self.image)
        if r.returncode:
            raise PlatformError("cannot start the throwaway PostgreSQL: " + r.stderr.decode()[:300])
        # the image's entry point starts a temporary server for its set-up and restarts it: wait for a query to succeed twice
        ok = 0
        for _ in range(120):
            if docker("exec", self.name, "psql", "-U", "postgres", "-d", "postgres", "-tAc", "select 1").returncode == 0 and \
               docker("exec", self.name, "pg_isready", "-U", "postgres").returncode == 0:
                ok += 1
                if ok >= 3:
                    return self
            else:
                ok = 0
            time.sleep(1)
        raise PlatformError("the throwaway PostgreSQL did not come up")

    def __exit__(self, *exc):
        docker("rm", "-f", "-v", self.name)
        return False

    def restore(self, dump: Path, db="h"):
        docker("exec", self.name, "psql", "-U", "postgres", "-qc", f"CREATE DATABASE {db}")
        r = docker("exec", "-i", self.name, "pg_restore", "-U", "postgres", "-d", db, "--no-owner", "--no-privileges", input=Path(dump).read_bytes(), timeout=900)
        # pg_restore reports harmless errors (a role that does not exist here) and still restores the rest
        if r.stderr.strip():
            print("pg_restore said:", r.stderr.decode("utf-8", "replace")[:500])
        chk = docker("exec", self.name, "psql", "-U", "postgres", "-d", db, "-tAc", "select count(*) from runs")
        if chk.returncode:
            raise PlatformError("the dump has no runs table after the restore: " + r.stderr.decode("utf-8", "replace")[:300])
        return db

    def psql(self, db, sql):
        return psql_in(self.name, db, sql)


def group(runs: list, mapping=None) -> list:
    """[(case id, title, baseline, run dict, rep, turn)] in the order of the runs. rep counts within the case; turn is the
    run's place in its thread. mapping: {first message: (case id, baseline)}."""
    ids, nrep, pos, out = {}, {}, {}, []
    for r in runs:
        msg = (r.get("first_user") or "").strip() or "(no user message)"
        if msg not in ids:
            ids[msg] = mapping[msg][0] if mapping and msg in mapping else f"H{len([i for i in ids.values() if i.startswith('H') and i[1:].isdigit()]) + 1:02d}"
        cid = ids[msg]
        nrep[cid] = nrep.get(cid, 0) + 1
        pos[r["thread_id"]] = pos.get(r["thread_id"], 0) + 1
        base = mapping[msg][1] if mapping and msg in mapping else ""
        out.append((cid, msg[:60].replace("\n", " "), base, r, nrep[cid], pos[r["thread_id"]]))
    return out


def mapping_from_cases(path) -> dict:
    from . import cases as casesmod
    _, cases = casesmod.load(path)
    return {c.turns[0].say.strip(): (c.id, c.baseline) for c in cases if len(c.turns) == 1}


def records(listing: list, rows_of, window, mapping=None, dump_name="") -> list:
    """The result records; rows_of(run id) returns the rows of metrics.run_query."""
    out = []
    for cid, title, base, r, rep, turn in group(listing, mapping):
        rows = rows_of(r["id"])
        rec = metrics.build_record(rows, window, key=f"{cid}|{rep}|{turn}", case=cid, title=title, rep=rep, turn=turn, turns_total=1,
                                   source="history", model=(rows["steps"][0]["model"] if rows.get("steps") else None), outcome="ended",
                                   confirmations=[], success=None, success_spec={}, baseline=base, dump=dump_name)
        rec["files"] = [{**d, "saved": None, "sanity": {"ok": None, "detail": "not checked: a dump holds no file content"}} for d in rec["delivered_events"]]
        out.append(rec)
    return out


def run(dump: Path, out: Path, window=None, cases_file=None, image=IMAGE, log=print) -> list:
    dump = Path(dump)
    if not dump.is_file():
        raise PlatformError(f"no such dump: {dump}")
    mapping = mapping_from_cases(cases_file) if cases_file else None
    with Throwaway(image) as pg:
        db = pg.restore(dump)
        listing = json.loads(pg.psql(db, LIST).strip())
        log(f"restored {dump.name}: {len(listing)} runs")
        recs = records(listing, lambda rid: json.loads(pg.psql(db, metrics.run_query(rid)).strip()), window or DEFAULT_WINDOW, mapping, dump.name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    models = sorted({r["model"] for r in recs if r["model"]})
    (out / "meta.json").write_text(json.dumps({
        "source": "history", "dump": str(dump), "restored_into": image, "runs": len(recs), "models": models,
        "window": {"value": window or DEFAULT_WINDOW, "source": "--window" if window else "assumed: the window recorded in the proposal's section 1.1 (the dump does not hold it)"},
        "note": "cases are unknown; runs are grouped by their first user message. No success condition: a dump holds no file content."},
        indent=1, ensure_ascii=False), encoding="utf-8")
    return recs
