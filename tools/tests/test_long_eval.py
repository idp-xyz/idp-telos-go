"""Tests of tools/long_eval: the parts that need neither Docker, nor the platform, nor a model: reading cases, the success
conditions, the sanity checks of delivered files, the "repeated work" count, the per-step series and totals from rows, the
report's tables, the evaluation loop's resume, and the confirmation handling, against a double of the platform.
Standard library only."""
import argparse
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.long_eval import cases, files, history, live, metrics, report, run


def zipped(**parts) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, data in parts.items():
            z.writestr(name.replace("__", "/"), data)
    return b.getvalue()


def case_doc(**over):
    c = {"id": "L1", "title": "t", "turns": [{"say": "do it", "attach": ["files/a.xlsx"]}], "success": {"delivered": [".xlsx"]}, "baseline": "b"}
    c.update(over)
    return {"agent": "office-assistant", "cases": [c]}


class Cases(unittest.TestCase):
    def test_reads_cases_and_resolves_attachments_next_to_the_file(self):
        agent, cs = cases.parse(case_doc(), Path("/x/long-eval"))
        self.assertEqual(agent, "office-assistant")
        self.assertEqual(cs[0].turns[0].attach, ["/x/long-eval/files/a.xlsx"])
        self.assertEqual(cs[0].success, {"delivered": [".xlsx"]})
        self.assertEqual(cs[0].baseline, "b")

    def test_bad_cases_are_refused_with_a_reason(self):
        for doc, word in [({}, "cases"), (case_doc(id="a b"), "id"), (case_doc(turns=[]), "turns"), (case_doc(turns=[{"say": " "}]), "say"),
                          (case_doc(success={"delivered": [".exe"]}), "unknown file kinds"),
                          (case_doc(success={"delivered": [".pdf"], "delivered_each_turn": [".pdf"]}), "choose one")]:
            with self.subTest(word=word), self.assertRaisesRegex(cases.CaseError, word):
                cases.parse(doc, Path("."))
        two = {"cases": [case_doc()["cases"][0], case_doc()["cases"][0]]}
        with self.assertRaisesRegex(cases.CaseError, "twice"):
            cases.parse(two, Path("."))

    def test_load_selects_cases_and_reports_a_missing_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.json"
            p.write_text(json.dumps(case_doc()))
            _, cs = cases.load(p, ["L1"])
            self.assertEqual(cases.missing_files(cs), [str(Path(d).resolve() / "files" / "a.xlsx")])
            with self.assertRaisesRegex(cases.CaseError, "no such case: L9"):
                cases.load(p, ["L9"])
            with self.assertRaisesRegex(cases.CaseError, "cannot read"):
                cases.load(Path(d) / "none.json")


def rec(status="COMPLETED", names=(), ok=True, **kw):
    return {"status": status, "files": [{"name": n, "sanity": {"ok": ok}} for n in names], **kw}


class Success(unittest.TestCase):
    def test_delivered_is_judged_over_the_whole_case(self):
        spec = {"delivered": [".xlsx"]}
        self.assertTrue(cases.rep_success(spec, 1, [rec(names=["a.xlsx"])]))
        self.assertFalse(cases.rep_success(spec, 1, [rec(names=["a.docx"])]))       # the wrong kind
        self.assertFalse(cases.rep_success(spec, 1, [rec(names=["a.xlsx"], ok=False)]))  # a file that fails its check
        self.assertFalse(cases.rep_success(spec, 1, [rec(status="FAILED", names=["a.xlsx"])]))  # the last turn must have completed
        self.assertFalse(cases.rep_success(spec, 2, [rec(names=["a.xlsx"])]))        # a turn is missing
        self.assertTrue(cases.rep_success(spec, 2, [rec(names=["a.xlsx"]), rec()]))  # delivered in an earlier turn counts

    def test_each_turn_needs_its_own_file(self):
        spec = {"delivered_each_turn": [".xlsx"]}
        self.assertTrue(cases.rep_success(spec, 2, [rec(names=["a.xlsx"]), rec(names=["b.xlsx"])]))
        self.assertFalse(cases.rep_success(spec, 2, [rec(names=["a.xlsx"]), rec()]))
        self.assertFalse(cases.rep_success(spec, 2, [rec(names=["a.xlsx"]), rec(status="FAILED", names=["b.xlsx"])]))
        self.assertTrue(cases.turn_success(spec, "COMPLETED", rec(names=["a.xlsx"])["files"]))
        self.assertFalse(cases.turn_success(spec, "COMPLETED", []))
        self.assertIsNone(cases.turn_success({"delivered": [".xlsx"]}, "COMPLETED", []))

    def test_no_condition_is_none_and_not_a_success(self):
        self.assertIsNone(cases.rep_success({}, 1, [rec()]))


class Files(unittest.TestCase):
    def test_office_files_are_zips_with_their_main_part(self):
        self.assertTrue(files.check("a.xlsx", zipped(**{"xl__workbook.xml": "<workbook/>"}))["ok"])
        self.assertTrue(files.check("a.docx", zipped(**{"word__document.xml": "<d/>"}))["ok"])
        self.assertTrue(files.check("a.pptx", zipped(**{"ppt__presentation.xml": "<p/>"}))["ok"])
        self.assertFalse(files.check("a.xlsx", zipped(**{"word__document.xml": "<d/>"}))["ok"])     # a zip, but not a workbook
        self.assertFalse(files.check("a.xlsx", b"not a zip")["ok"])
        self.assertFalse(files.check("a.docx", zipped(**{"word__document.xml": "  "}))["ok"])         # empty main part

    def test_pdf_needs_the_header_and_a_page(self):
        self.assertTrue(files.check("a.pdf", b"%PDF-1.4\n1 0 obj << /Type /Page /Parent 2 0 R >> endobj")["ok"])
        self.assertTrue(files.check("a.PDF", b"%PDF-1.7\n<< /Type/Page >>")["ok"])
        self.assertFalse(files.check("a.pdf", b"%PDF-1.4\n<< /Type /Pages /Count 0 >>")["ok"])   # the tree alone is no page
        self.assertTrue(files.check("a.pdf", b"%PDF-1.7\n<< /Type /Pages /Count 3 >>")["ok"])    # pages in compressed streams
        self.assertFalse(files.check("a.pdf", b"hello /Type /Page")["ok"])

    def test_markdown_is_non_empty_utf8(self):
        self.assertTrue(files.check("a.md", "# 标题\n".encode())["ok"])
        self.assertFalse(files.check("a.md", b" \n")["ok"])
        self.assertFalse(files.check("a.md", b"\xff\xfe\x00")["ok"])

    def test_other_kinds_have_no_check(self):
        self.assertIsNone(files.check("a.txt", b"x")["ok"])


def call(tool, args, state="DONE", **kw):
    return {"tool": tool, "arguments": args, "state": state, **kw}


class Metrics(unittest.TestCase):
    def test_repeated_work_counts_calls_after_the_first_and_ignores_key_order(self):
        calls = [call("run", '{"a":1,"b":2}'), call("run", '{ "b": 2, "a": 1 }'), call("run", '{"a":1,"b":2}'), call("run", '{"a":2}'), call("export", '{"a":1,"b":2}')]
        r = metrics.repeated_work(calls)
        self.assertEqual((r["calls"], r["repeated"], r["share"]), (5, 2, 0.4))
        self.assertEqual(r["top"], [{"tool": "run", "times": 3}])
        self.assertEqual(metrics.repeated_work([]), {"calls": 0, "repeated": 0, "share": 0.0, "top": []})
        self.assertEqual(metrics.repeated_work([call("x", "not json"), call("x", "not json")])["repeated"], 1)

    def test_series_and_totals_from_steps(self):
        steps = [{"step": 2, "prompt_tokens": 300, "completion_tokens": 7, "latency_ms": 20, "stop_reason": "length", "error_code": None},
                 {"step": 1, "prompt_tokens": 100, "completion_tokens": 5, "latency_ms": 10, "stop_reason": "tool_calls", "error_code": None},
                 {"step": 3, "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 1, "stop_reason": None, "error_code": "status_400"}]
        ser = metrics.series(steps)
        self.assertEqual([s["step"] for s in ser], [1, 2, 3])
        t = metrics.totals(ser, 1000)
        self.assertEqual((t["input_tokens"], t["output_tokens"], t["max_prompt"], t["max_prompt_step"], t["max_prompt_share"]), (400, 12, 300, 2, 0.3))
        self.assertEqual(t["stop_reasons"], {"length": 1, "tool_calls": 1})
        self.assertEqual(t["error_codes"], {"status_400": 1})
        self.assertIsNone(metrics.totals(ser)["max_prompt_share"])   # no window, no share
        self.assertEqual(metrics.totals([])["max_prompt"], 0)

    def test_events_are_counted_by_name_and_the_watched_ones_are_always_there(self):
        ev = [{"name": "idp.host/model.retry"}, {"name": "idp.host/model.retry"}, {"name": "idp.host/context.resolved"}, {"name": "idp.host/file.delivered"}]
        c = metrics.event_counts(ev, usage_events=9)
        self.assertEqual(c["watched"]["model.retry"], 2)
        self.assertEqual(c["watched"]["tool.denied"], 0)
        self.assertEqual(c["all"]["context.resolved"], 1)
        self.assertEqual(c["all"]["model.usage"], 9)

    def test_time_and_wall_minutes(self):
        self.assertEqual(metrics.wall_minutes("2026-11-17T06:27:00.5+00:00", "2026-11-17T06:54:00+00:00"), 26.99)
        self.assertEqual(metrics.wall_minutes("2026-11-17T06:27:00Z", "2026-11-17T06:57:00.123456+00:00"), 30.0)
        self.assertIsNone(metrics.wall_minutes(None, "2026-11-17T06:57:00+00:00"))

    def test_the_query_refuses_what_is_not_a_run_id(self):
        self.assertIn("ws_abc", metrics.run_query("ws_abc"))
        with self.assertRaises(ValueError):
            metrics.run_query("x'; drop table runs; --")

    def test_a_record_from_rows(self):
        rows = {"run": {"id": "ws_1", "thread_id": "t", "agent": "a", "status": "FAILED", "failure_code": "STEP_LIMIT", "step_count": 2, "tokens_used": 9,
                        "started_at": "2026-11-17T06:00:00+00:00", "finished_at": "2026-11-17T06:03:00+00:00"},
                "steps": [{"step": 1, "prompt_tokens": 10, "completion_tokens": 1, "latency_ms": 5, "stop_reason": "tool_calls", "error_code": None},
                          {"step": 2, "prompt_tokens": 20, "completion_tokens": 2, "latency_ms": 6, "stop_reason": "tool_calls", "error_code": None}],
                "tool_calls": [call("run", "{}"), call("run", "{}", state="FAILED", interrupted=True)],
                "events": [{"seq": 1, "name": "idp.host/file.delivered", "value": {"name": "a.pdf", "size": 3, "sha256": "ab"}}], "usage_events": 2}
        r = metrics.build_record(rows, 100, case="L", rep=1, turn=1)
        self.assertEqual((r["status"], r["failure_code"], r["steps"], r["wall_minutes"], r["input_tokens"], r["max_prompt_share"]), ("FAILED", "STEP_LIMIT", 2, 3.0, 30, 0.2))
        self.assertEqual(r["tool_calls"], {"run": {"DONE": 1, "FAILED": 1}})
        self.assertEqual(r["events"]["interrupted_calls"], 1)
        self.assertEqual(r["repeated"]["repeated"], 1)
        self.assertEqual(r["delivered_events"], [{"name": "a.pdf", "size": 3, "sha256": "ab"}])

    def test_median_and_range(self):
        self.assertEqual(metrics.med_range([3, 1, 2])["median"], 2)
        m = metrics.med_range([None, 4, 8])
        self.assertEqual((m["median"], m["min"], m["max"]), (6, 4, 8))
        self.assertIsNone(metrics.med_range([None])["median"])


class Window(unittest.TestCase):
    def test_a_window_is_found_in_the_usual_places(self):
        self.assertEqual(live.window_from({"default_generation_settings": {"n_ctx": 131072}}), 131072)
        self.assertEqual(live.window_from({"data": [{"id": "m", "meta": {"n_ctx_train": 40960}}]}, "m"), 40960)
        self.assertEqual(live.window_from({"data": [{"id": "x", "max_model_len": 8192}, {"id": "m", "max_model_len": 4096}]}, "m"), 4096)
        self.assertIsNone(live.window_from({"data": [{"id": "m"}]}, "m"))

    def test_a_provider_that_wants_a_key_is_asked_nothing_more(self):
        class Wants:
            def request(self, method, url, **kw):
                return 401, b"{}"
        w, why = live.discover_window("http://x/v1", "m", Wants())
        self.assertIsNone(w)
        self.assertIn("does not hold", why)


def row(case, rep, turn, steps, prompts, status="COMPLETED", minutes=1.0, names=(), total=1, spec=None, window=1000, calls=None, **kw):
    ser = [{"step": i + 1, "prompt": p, "completion": 1, "latency_ms": 1000, "stop": "tool_calls", "error": None} for i, p in enumerate(prompts)]
    t = metrics.totals(ser, window)
    return {"key": run.key(case, rep, turn), "case": case, "title": "title " + case, "rep": rep, "turn": turn, "turns_total": total, "status": status,
            "failure_code": None if status == "COMPLETED" else "STEP_LIMIT", "steps": steps, "wall_minutes": minutes, "series": ser, **t, "window": window,
            "run_id": f"ws_{case}{rep}{turn}", "baseline": "recorded text", "success_spec": spec if spec is not None else {"delivered": [".xlsx"]},
            "files": [{"name": n, "sanity": {"ok": True}} for n in names], "repeated": calls or {"calls": 10, "repeated": 1, "share": 0.1, "top": []},
            "events": {"watched": {k: 0 for k in metrics.WATCHED}, "interrupted_calls": 0}, **kw}


def sample(scale=1):
    r = []
    for rep, (steps, status, names) in enumerate([(10, "COMPLETED", ["a.xlsx"]), (20, "FAILED", []), (30, "COMPLETED", ["a.xlsx"])], 1):
        r.append(row("L1", rep, 1, steps * scale, [100 * i * scale for i in range(1, steps + 1)], status=status, minutes=steps / 2, names=names))
    return r


class Report(unittest.TestCase):
    def test_tables_give_median_range_and_every_value(self):
        text = report.render("base", sample(), {})
        self.assertIn("| L1 | 3 | 2 of 3 |", text)
        self.assertIn("| steps | 20 (10 to 30) | 10, 20, 30 |", text)
        self.assertIn("| minutes | 10.0 (5.0 to 15.0) | 5.0, 10.0, 15.0 |", text)
        self.assertIn("recorded text", text)                       # the case's baseline sits in its section
        self.assertIn("three repetitions", text.replace("With three repetitions per case a significance test is not possible", "three repetitions"))
        self.assertIn("one local model", text)

    def test_the_series_of_the_longest_run_shows_every_fifth_step_and_the_last(self):
        text = report.render("base", sample(), {})
        self.assertIn("the longest run (ws_L131, repetition 3", text)
        steps = [int(l.split("|")[1]) for l in text.splitlines() if l.startswith("| ") and l.split("|")[1].strip().isdigit() and "%" in l]
        self.assertEqual(steps, [1, 5, 10, 15, 20, 25, 30])
        self.assertIn("| 30 | 3,000 | 300% |", text)

    def test_a_multi_turn_case_adds_up_turns_and_shows_each(self):
        spec = {"delivered_each_turn": [".xlsx"]}
        recs = [row("L2", 1, t, 10 * t, [100] * (10 * t), spec=spec, total=2, names=["x.xlsx"]) for t in (1, 2)]
        text = report.render("n", recs, {})
        self.assertIn("| steps | 30 (30 to 30) | 30 |", text)
        self.assertIn("| 2 | 20 | 1.0 |", text)
        self.assertIn("1 of 1", text)

    def test_comparison_shows_both_and_the_difference(self):
        text = report.render("new", sample(), {}, "old", sample(scale=2))
        self.assertIn("Differences are `new` minus `old`", text)
        self.assertIn("| steps | 20 (10 to 30) | 40 (20 to 60) | -20.0 (-50%) |", text)
        self.assertIn("10, 20, 30 | 20, 40, 60 |", text)

    def test_a_case_missing_on_one_side_is_said_so(self):
        other = [dict(r, case="L9") for r in sample()]
        self.assertIn("has no case L1", report.render("new", sample(), {}, "old", other))

    def test_history_without_a_condition_shows_no_success_rate(self):
        text = report.render("h", [row("H01", 1, 1, 2, [5, 6], spec={})], {"source": "history", "dump": "d", "runs": 1, "models": ["m"]})
        self.assertIn("no success condition", text)
        self.assertIn("From a dump", text)

    def test_write_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample()))
            (Path(d) / "meta.json").write_text(json.dumps({"window": {"value": 1000, "source": "--window"}, "agent": {"name": "a"}}))
            p = report.write(Path(d))
            self.assertTrue(p.read_text().startswith("# Long-task evaluation:"))
            with self.assertRaisesRegex(ValueError, "no results"):
                report.write(Path(d) / "none")


class Fake:
    """A double of live.Platform: runs end at once, with the rows a HOST would hold."""

    def __init__(self, statuses=None, fail_at=None):
        self.n = 0
        self.invoked = []
        self.uploads = []
        self.statuses = statuses or {}
        self.canceled = []
        self.decisions = []
        self.fail_at = fail_at
        self.threads = {}

    def upload(self, path):
        self.uploads.append(path)
        return f"file{len(self.uploads)}"

    def invoke(self, message, file_ids=(), thread_id=None):
        if self.fail_at == len(self.invoked) + 1:
            raise live.PlatformError("POST invoke -> 500")
        self.n += 1
        rid = f"ws_{self.n}"
        self.invoked.append({"run": rid, "message": message, "files": list(file_ids), "thread": thread_id})
        self.threads[rid] = thread_id
        return {"run": rid, "thread": thread_id}

    def run_status(self, rid):
        seq = self.statuses.get(rid)
        if seq:
            return {"status": seq.pop(0) if len(seq) > 1 else seq[0]}
        return {"status": "COMPLETED"}

    def pending_confirmations(self, rid):
        return [{"id": "c1", "tool": "sandbox__sandbox_run", "arguments": "{}"}]

    def decide(self, rid, cid, decision="confirm"):
        self.decisions.append((rid, cid, decision))

    def cancel(self, rid):
        self.canceled.append(rid)

    def psql(self, sql):
        rid = sql.split("r.id = '")[1].split("'")[0]
        return json.dumps({"run": {"id": rid, "thread_id": self.threads[rid], "agent": "a", "status": "COMPLETED", "failure_code": None, "step_count": 1, "tokens_used": 5,
                                   "started_at": "2026-11-17T06:00:00+00:00", "finished_at": "2026-11-17T06:01:00+00:00"},
                           "steps": [{"step": 1, "prompt_tokens": 50, "completion_tokens": 1, "latency_ms": 9, "stop_reason": "end", "error_code": None}],
                           "tool_calls": [], "events": [], "usage_events": 1})

    def delivered_files(self, rid):
        return [{"name": "out.xlsx", "id": "f", "listed_sha256": None, "data": zipped(**{"xl__workbook.xml": "<workbook/>"})}]


def args(**kw):
    d = dict(runs=1, confirm=False, confirm_wait=5, turn_timeout=60)
    d.update(kw)
    return argparse.Namespace(**d)


def two_cases():
    doc = {"cases": [
        {"id": "A", "turns": [{"say": "one", "attach": []}], "success": {"delivered": [".xlsx"]}},
        {"id": "B", "turns": [{"say": "first"}, {"say": "second"}], "success": {"delivered_each_turn": [".xlsx"]}}]}
    return cases.parse(doc, Path("."))[1]


class Loop(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.out = Path(self.t.name)
        self.sleep = lambda s: None

    def tearDown(self):
        self.t.cleanup()

    def test_a_conversation_is_new_for_each_repetition_and_continues_through_its_turns(self):
        p = Fake()
        ran = run.evaluate(p, two_cases(), args(runs=2), self.out, 1000, "m", sleep=self.sleep, log=lambda s: None)
        self.assertEqual(ran, 6)
        th = [(i["message"], i["thread"]) for i in p.invoked]
        self.assertEqual(len({t for _, t in th}), 4)                      # A and B in each of two repetitions
        self.assertEqual(th[1][1], th[2][1])                              # B's turns share a thread
        self.assertNotEqual(th[1][1], th[4][1])                           # and the next repetition has another
        recs = run.read_results(self.out / "results.jsonl")
        self.assertEqual([r["key"] for r in recs][:3], ["A|1|1", "B|1|1", "B|1|2"])
        self.assertTrue(all(r["success"] for r in recs if r["case"] == "B"))
        self.assertTrue((self.out / "files" / "A-r1-t1" / "out.xlsx").is_file())
        self.assertEqual(recs[0]["files"][0]["sanity"]["ok"], True)
        self.assertEqual(recs[0]["max_prompt_share"], 0.05)

    def test_a_stopped_evaluation_continues_and_skips_what_is_done(self):
        p = Fake(fail_at=2)                                               # the platform fails when B is asked
        with self.assertRaises(live.PlatformError):
            run.evaluate(p, two_cases(), args(), self.out, 1000, "m", sleep=self.sleep, log=lambda s: None)
        self.assertEqual([r["key"] for r in run.read_results(self.out / "results.jsonl")], ["A|1|1"])   # written as soon as the turn ended
        q = Fake()
        ran = run.evaluate(q, two_cases(), args(), self.out, 1000, "m", sleep=self.sleep, log=lambda s: None)
        self.assertEqual(ran, 2)                                          # only B's two turns
        self.assertEqual([i["message"] for i in q.invoked], ["first", "second"])
        self.assertEqual(run.evaluate(Fake(), two_cases(), args(), self.out, 1000, "m", sleep=self.sleep, log=lambda s: None), 0)

    def test_resume_in_the_middle_of_a_conversation_keeps_its_thread(self):
        p = Fake(fail_at=3)                                               # B's second turn fails
        with self.assertRaises(live.PlatformError):
            run.evaluate(p, two_cases(), args(), self.out, 1000, "m", sleep=self.sleep, log=lambda s: None)
        first_thread = p.invoked[1]["thread"]
        q = Fake()
        run.evaluate(q, two_cases(), args(), self.out, 1000, "m", sleep=self.sleep, log=lambda s: None)
        self.assertEqual(len(q.invoked), 1)
        self.assertEqual(q.invoked[0]["thread"], first_thread)

    def test_attachments_are_uploaded_and_named_in_the_request(self):
        cs = cases.parse({"cases": [{"id": "A", "turns": [{"say": "s", "attach": ["f.md"]}]}]}, Path("/base"))[1]
        p = Fake()
        run.evaluate(p, cs, args(), self.out, None, "m", sleep=self.sleep, log=lambda s: None)
        self.assertEqual(p.uploads, ["/base/f.md"])
        self.assertEqual(p.invoked[0]["files"], ["file1"])

    def test_a_confirmation_is_recorded_and_not_rejected_unless_confirm_is_on(self):
        for confirm in (False, True):
            p = Fake(statuses={"ws_1": ["AWAITING_INPUT", "AWAITING_INPUT", "COMPLETED"]})
            clock = iter(range(0, 10000, 10)).__next__
            res = run.follow(p, "ws_1", args(confirm=confirm), sleep=self.sleep, clock=clock, log=lambda s: None)
            self.assertEqual(res["outcome"], "ended")
            self.assertEqual(len(res["confirmations"]), 1)               # asked twice by the double, recorded once
            self.assertEqual(p.decisions, [("ws_1", "c1", "confirm")] if confirm else [])
            self.assertEqual(res["confirmations"][0]["decision"], "confirm" if confirm else None)

    def test_an_unanswered_confirmation_times_out_with_a_record_and_the_rest_of_the_case_is_skipped(self):
        p = Fake(statuses={"ws_2": ["AWAITING_INPUT"]})                  # ws_2 is B's first turn
        t = iter(range(0, 100000, 100)).__next__
        run.evaluate(p, two_cases(), args(confirm_wait=1), self.out, 1000, "m", sleep=self.sleep, clock=t, log=lambda s: None)
        recs = {r["key"]: r for r in run.read_results(self.out / "results.jsonl")}
        self.assertEqual(recs["B|1|1"]["outcome"], "awaiting_confirmation")
        self.assertEqual(recs["B|1|1"]["confirmations"][0]["tool"], "sandbox__sandbox_run")
        self.assertEqual(recs["B|1|2"]["outcome"], "skipped")
        self.assertFalse(recs["B|1|2"]["success"])
        self.assertEqual(p.canceled, [])                                  # nothing rejected, nothing canceled

    def test_a_run_that_goes_on_too_long_is_canceled_and_recorded_as_a_timeout(self):
        p = Fake(statuses={"ws_1": ["RUNNING"]})
        t = iter(range(0, 100000, 600)).__next__
        res = run.follow(p, "ws_1", args(turn_timeout=60), sleep=self.sleep, clock=t, log=lambda s: None)
        self.assertEqual((res["outcome"], res["status"]), ("timeout", "RUNNING"))
        self.assertEqual(p.canceled, ["ws_1"])

    def test_ten_failed_polls_stop_the_evaluation(self):
        class Down(Fake):
            def run_status(self, rid):
                raise live.PlatformError("502")
        with self.assertRaisesRegex(live.PlatformError, "ten polls"):
            run.follow(Down(), "ws_1", args(), sleep=self.sleep, clock=lambda: 0, log=lambda s: None)

    def test_polling_slows_down(self):
        self.assertLess(run.poll_interval(10), run.poll_interval(100))
        self.assertLess(run.poll_interval(100), run.poll_interval(3000))

    def test_a_damaged_results_file_is_refused(self):
        (self.out / "results.jsonl").write_text('{"key": "a"}\n{broken\n')
        with self.assertRaisesRegex(ValueError, "line 2"):
            run.read_results(self.out / "results.jsonl")


class History(unittest.TestCase):
    runs = [{"id": "r1", "thread_id": "t1", "first_user": "make a workbook", "agent": "a"},
            {"id": "r2", "thread_id": "t1", "first_user": "continue", "agent": "a"},
            {"id": "r3", "thread_id": "t2", "first_user": "make a workbook", "agent": "a"},
            {"id": "r4", "thread_id": "t3", "first_user": None, "agent": "a"}]

    def test_runs_are_grouped_by_their_first_message(self):
        g = history.group(self.runs)
        self.assertEqual([(c, rep, turn) for c, _, _, _, rep, turn in g], [("H01", 1, 1), ("H02", 1, 2), ("H01", 2, 1), ("H03", 1, 1)])

    def test_a_single_turn_case_lends_its_id_and_baseline(self):
        g = history.group(self.runs, {"make a workbook": ("L1", "recorded")})
        self.assertEqual([(c, b) for c, _, b, *_ in g][:3], [("L1", "recorded"), ("H01", ""), ("L1", "recorded")])

    def test_records_are_in_the_results_format_and_read_by_the_report(self):
        def rows_of(rid):
            return {"run": {"id": rid, "thread_id": "t", "agent": "a", "status": "COMPLETED", "step_count": 1, "started_at": None, "finished_at": None},
                    "steps": [{"step": 1, "model": "m", "prompt_tokens": 40, "completion_tokens": 2, "latency_ms": 5, "stop_reason": "end", "error_code": None}],
                    "tool_calls": [], "events": [{"seq": 1, "name": "idp.host/file.delivered", "value": {"name": "a.pdf", "size": 1, "sha256": "x"}}], "usage_events": 1}
        recs = history.records(self.runs, rows_of, 100)
        self.assertEqual((recs[0]["key"], recs[0]["source"], recs[0]["model"], recs[0]["max_prompt_share"]), ("H01|1|1", "history", "m", 0.4))
        self.assertEqual(recs[0]["files"][0]["name"], "a.pdf")
        self.assertIsNone(recs[0]["success"])
        text = report.render("h", recs, {})
        self.assertIn("## H01:", text)
        self.assertIn("Runs that delivered a file: 2 of 2", text)


if __name__ == "__main__":
    unittest.main()
