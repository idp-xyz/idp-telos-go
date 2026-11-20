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
        self.assertEqual(metrics.repeated_work([]), {"calls": 0, "repeated": 0, "share": 0.0, "top": [], "strict": {"repeated": 0, "share": 0.0, "top": []}})
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


def view(step, estimate, level=0, hint=False, items=None, raw=None, window=131072, **kw):
    v = {"step": step, "windowTokens": window, "markTokens": 78643, "estimateTokens": estimate, "rawEstimateTokens": raw if raw is not None else estimate,
         "factor": 1.0, "level": level, "shortened": len(items or []), "hint": hint}
    if items is None:
        v["itemsAsBefore"] = True
    else:
        v["items"] = items
    v.update(kw)
    return {"seq": step, "name": "idp.host/context.view", "value": v}


def serie(*prompts):
    return metrics.series([{"step": i + 1, "prompt_tokens": p, "completion_tokens": 1, "latency_ms": 1, "stop_reason": "tool_calls", "error_code": None} for i, p in enumerate(prompts)])


class ContextView(unittest.TestCase):
    events = [view(1, 110, items=[]),
              view(2, 220, level=1, items=[["result", "t1", 5000, 1200, 1300], ["arguments", "c1", 3000, 600, 700]]),
              view(3, 330, items=None),                                   # sticky: the same items, level 0
              view(4, 380, level=2, hint=True, items=[["result", "t1", 5000, 600, 700], ["result", "t2", 2000, 600, 650], ["arguments", "c1", 3000, 600, 700]])]

    def test_no_events_means_the_feature_is_off(self):
        self.assertEqual(metrics.context_view([], serie(100)), {"present": False})
        self.assertEqual(metrics.context_view([{"name": "idp.host/file.delivered"}], serie(100)), {"present": False})

    def test_cuts_levels_hints_and_what_was_not_shown(self):
        c = metrics.context_view(self.events, serie(100, 200, 300, 400), 131072)
        self.assertEqual((c["present"], c["steps_with_view"], c["steps_with_new_cut"], c["max_level"], c["steps_with_hint"]), (True, 4, 2, 2, 1))
        self.assertEqual((c["shortened_last"], c["items_last"]), (3, 3))
        self.assertEqual(c["bytes_not_shown"], {"result": (5000 - 700) + (2000 - 650), "arguments": 3000 - 700})
        self.assertEqual(c["warnings"], [])

    def test_items_as_before_carry_the_last_list(self):
        c = metrics.context_view(self.events[:3], serie(100, 200, 300), 131072)
        self.assertEqual(c["bytes_not_shown"], {"result": 3700, "arguments": 2300})   # step 3 shows the items of step 2
        self.assertEqual(c["steps_with_new_cut"], 1)                                  # level 0 at step 3: not a new cut

    def test_the_estimator_error_is_signed_against_the_reported_prompt_tokens(self):
        c = metrics.context_view(self.events, serie(100, 200, 300, 400), 131072)
        e = c["estimate_error"]
        self.assertEqual(e["n"], 4)
        self.assertAlmostEqual(e["median"], (0.10 + 0.10) / 2)       # 110/100, 220/200, 330/300 are +10%; 380/400 is -5%
        self.assertAlmostEqual(e["largest"], 0.10)
        self.assertAlmostEqual(e["min"], -0.05)
        c = metrics.context_view([view(1, 90)], serie(100))
        self.assertAlmostEqual(c["estimate_error"]["largest"], -0.10)
        c = metrics.context_view([view(1, 90)], serie(0))             # a report of 0 is no measurement
        self.assertIsNone(c["estimate_error"])

    def test_a_window_that_differs_from_the_one_told_is_warned_about(self):
        c = metrics.context_view([view(1, 10, window=65536)], serie(10), 131072)
        self.assertEqual(c["window_used"], 65536)
        self.assertIn("65536", c["warnings"][0])
        self.assertEqual(metrics.context_view([view(1, 10, window=65536)], serie(10), None)["warnings"], [])

    def test_the_record_carries_the_context_and_the_per_step_columns(self):
        rows = {"run": {"id": "ws_1", "thread_id": "t", "agent": "a", "status": "COMPLETED", "step_count": 2, "started_at": None, "finished_at": None},
                "steps": [{"step": s, "prompt_tokens": 100 * s, "completion_tokens": 1, "latency_ms": 1, "stop_reason": "end", "error_code": None} for s in (1, 2)],
                "tool_calls": [], "events": self.events[:2], "usage_events": 2}
        r = metrics.build_record(rows, 131072)
        self.assertTrue(r["context"]["present"])
        self.assertNotIn("per_step", r["context"])
        self.assertEqual([(s["estimate"], s["level"], s["hint"]) for s in r["series"]], [(110, 0, False), (220, 1, False)])
        off = metrics.build_record({**rows, "events": []}, 131072)
        self.assertEqual(off["context"], {"present": False})
        self.assertNotIn("estimate", off["series"][0])


def ctx_row(case, rep, on, **kw):
    ev = ContextView.events if on else []
    rows = {"run": {"id": f"ws_{case}{rep}", "thread_id": "t", "agent": "a", "status": "COMPLETED", "step_count": 4, "started_at": None, "finished_at": None},
            "steps": [{"step": s, "prompt_tokens": 100 * s, "completion_tokens": 1, "latency_ms": 1000, "stop_reason": "tool_calls", "error_code": None} for s in (1, 2, 3, 4)],
            "tool_calls": [], "events": ev, "usage_events": 4}
    return metrics.build_record(rows, 131072, key=run.key(case, rep, 1), case=case, title="t", rep=rep, turn=1, turns_total=1, outcome="ended", success_spec={}, files=[], baseline="", **kw)


class ContextReport(unittest.TestCase):
    def test_the_block_and_the_per_step_columns(self):
        text = report.render("new", [ctx_row("L1", 1, True), ctx_row("L1", 2, True)], {})
        self.assertIn("feature on (2 of 2 turns have context.view events)", text)
        self.assertIn("| steps with a new cut (median per turn) | 2 |", text)
        self.assertIn("| deepest level reached | 2 |", text)
        self.assertIn("| bytes not shown, results (median) | 5,650 |", text)
        self.assertIn("| estimate against reported prompt tokens, median error | +10.0% |", text)
        self.assertIn("| step | prompt tokens | share of window | completion tokens | latency s | stop | estimate | level | hint |", text)
        self.assertIn("| 4 | 400 | 0% | 1 | 1.0 | tool_calls | 380 | 2 | H |", text)

    def test_against_shows_on_and_off_side_by_side(self):
        text = report.render("new", [ctx_row("L1", 1, True)], {}, "old", [ctx_row("L1", 1, False)])
        self.assertIn("feature new: on (1 of 1 turns", text)
        self.assertIn("old: off (0 of 1 turns", text)
        self.assertIn("| feature | on | off | |", text)
        self.assertIn("| deepest level reached | 2 | n/a | n/a |", text)

    def test_an_old_result_line_without_the_new_fields_loads_and_reports(self):
        old = row("L1", 1, 1, 3, [100, 200, 300])
        for k in ("context",):
            old.pop(k, None)
        text = report.render("old", [old], {})
        self.assertIn("feature off (0 of 1 turns have context.view events)", text)
        self.assertNotIn("| estimate |", text)
        self.assertIn("| 3 | 300 |", text)

    def test_a_window_warning_reaches_the_report(self):
        r = ctx_row("L1", 1, True)
        r["context"]["warnings"] = ["the HOST used a window of 65536 tokens and the evaluation was told 131072"]
        self.assertIn("WARNING: the HOST used a window of 65536", report.render("n", [r], {}))


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

    def test_a_dump_with_context_view_events_gives_the_same_extraction(self):
        def rows_of(rid):
            return {"run": {"id": rid, "thread_id": "t", "agent": "a", "status": "COMPLETED", "step_count": 1, "started_at": None, "finished_at": None},
                    "steps": [{"step": 1, "model": "m", "prompt_tokens": 100, "completion_tokens": 1, "latency_ms": 5, "stop_reason": "end", "error_code": None}],
                    "tool_calls": [], "events": [view(1, 120, items=[])], "usage_events": 1}
        recs = history.records(self.runs[:1], rows_of, 131072)
        self.assertTrue(recs[0]["context"]["present"])
        self.assertAlmostEqual(recs[0]["context"]["estimate_error"]["median"], 0.2)


def scall(step, tool, args, state="DONE", **kw):
    return call(tool, args, state, step=step, **kw)


class StrictRepeats(unittest.TestCase):
    """The refined count of repeated work (README): the same tool and arguments as an earlier call that SUCCEEDED, at a later step,
    and not a delivery tool. metrics.repeated_work keeps the original count next to it."""

    def strict(self, calls):
        return metrics.repeated_work(calls)["strict"]["repeated"]

    def test_a_true_repeat_is_counted_in_both(self):
        calls = [scall(1, "sandbox__sandbox_run", '{"cmd":"ls"}'), scall(2, "sandbox__sandbox_run", '{ "cmd": "ls" }'), scall(5, "sandbox__sandbox_run", '{"cmd":"ls"}')]
        r = metrics.repeated_work(calls)
        self.assertEqual((r["repeated"], r["strict"]["repeated"]), (2, 2))
        self.assertEqual(r["strict"]["top"], [{"tool": "sandbox__sandbox_run", "times": 2}])
        self.assertEqual(r["strict"]["share"], round(2 / 3, 4))

    def test_the_export_of_a_revised_file_is_not_a_repeat(self):
        calls = [scall(3, "sandbox__sandbox_export_file", '{"path":"out.xlsx"}'), scall(9, "sandbox__sandbox_export_file", '{"path":"out.xlsx"}')]
        r = metrics.repeated_work(calls)
        self.assertEqual((r["repeated"], r["strict"]["repeated"]), (1, 0))
        # also under another gateway prefix, and a tool that only has the delivery tool's name as a part of its own is not one
        self.assertEqual(self.strict([scall(1, "box__sandbox_export_file", "{}"), scall(2, "box__sandbox_export_file", "{}")]), 0)
        self.assertEqual(self.strict([scall(1, "sandbox__sandbox_export_files", "{}"), scall(2, "sandbox__sandbox_export_files", "{}")]), 1)

    def test_calls_of_a_tool_the_agent_does_not_have_are_not_repeats(self):
        calls = [scall(i, "sandbox_create", "{}", state="DENIED") for i in range(1, 5)] + [scall(5, "sandbox_create", "{}", state="FAILED", is_error=True)]
        r = metrics.repeated_work(calls)
        self.assertEqual((r["repeated"], r["strict"]["repeated"]), (4, 0))

    def test_a_retry_after_a_failure_is_not_a_repeat_but_a_redo_after_the_success_is(self):
        calls = [scall(1, "t", '{"a":1}', state="FAILED"), scall(2, "t", '{"a":1}'), scall(3, "t", '{"a":1}')]
        r = metrics.repeated_work(calls)
        self.assertEqual((r["repeated"], r["strict"]["repeated"]), (2, 1))
        # a call that finished DONE but is flagged as an error did not succeed
        self.assertEqual(self.strict([scall(1, "t", "{}", is_error=True), scall(2, "t", "{}")]), 0)

    def test_two_identical_calls_of_one_step_are_not_a_redo(self):
        calls = [scall(4, "t", '{"a":1}'), scall(4, "t", '{"a":1}')]
        r = metrics.repeated_work(calls)
        self.assertEqual((r["repeated"], r["strict"]["repeated"]), (1, 0))

    def test_calls_without_steps_are_ordered_by_their_place(self):
        self.assertEqual(self.strict([call("t", "{}"), call("t", "{}")]), 1)
        self.assertEqual(self.strict([call("t", "{}", state="FAILED"), call("t", "{}")]), 0)

    def test_a_record_carries_both_and_the_report_shows_both_and_an_old_record_shows_n_a(self):
        calls = [scall(1, "t", "{}"), scall(2, "t", "{}")]
        new = row("L1", 1, 1, 2, [10, 20], calls={"calls": 2, "repeated": 1, "share": 0.5, "top": [], "strict": metrics.strict_repeats(calls)})
        old = row("L1", 1, 1, 2, [10, 20], calls={"calls": 2, "repeated": 1, "share": 0.5, "top": []})
        text = report.render("new", [new], {})
        self.assertIn("| repeated tool calls, share (original count) | 50.0% (50.0% to 50.0%) |", text)
        self.assertIn("| repeated after a success, share (refined count, README) | 50.0% (50.0% to 50.0%) |", text)
        text = report.render("old", [old], {})
        self.assertIn("| repeated after a success, share (refined count, README) | n/a |", text)
        both = report.render("new", [new], {}, "old", [old])
        self.assertIn("repeated after a success, share (refined count, README)", both)


def tstep(step, latency, first=None, prompt=100, **kw):
    s = {"step": step, "prompt": prompt, "completion": 1, "latency_ms": latency, "stop": "tool_calls", "error": None, "first_output_ms": first}
    s.update(kw)
    return s


class TimingMetrics(unittest.TestCase):
    def test_percentile_is_by_nearest_rank(self):
        self.assertEqual(metrics.percentile(list(range(1, 11)), 0.9), 9)
        self.assertEqual(metrics.percentile([5], 0.9), 5)
        self.assertIsNone(metrics.percentile([None, None], 0.9))

    def test_first_output_and_its_share_of_the_step_over_the_steps_where_it_is_known(self):
        steps = [tstep(1, 10000, 2000), tstep(2, 10000, 4000), tstep(3, 8000, 8000 * 0.5), tstep(4, 9000, None)]
        t = metrics.timing_stats(steps)
        f = t["first_output"]
        self.assertEqual((t["steps"], f["n"], f["median"], f["p90"]), (4, 3, 4000, 4000))
        self.assertEqual((f["share_median"], f["share_p90"]), (0.4, 0.5))

    def test_reuse_uses_the_servers_numbers_where_it_has_them_and_the_usage_otherwise(self):
        steps = [tstep(1, 1, prompt=1000, server_cache_n=900, server_prompt_n=100, server_prompt_ms=300.0),
                 tstep(2, 1, prompt=1000, cached=400),            # only usage.cached_tokens: processed is what is left
                 tstep(3, 1, prompt=1000)]                       # says nothing: not in the sums at all
        u = metrics.timing_stats(steps)["reuse"]
        self.assertEqual((u["n"], u["reused"], u["processed"], u["prompt"], u["share"]), (2, 1300, 700, 2000, 0.65))
        self.assertEqual(metrics.timing_stats(steps)["server_prompt"]["n"], 1)

    def test_nothing_known_gives_none_everywhere(self):
        t = metrics.timing_stats([tstep(1, 5000), {"step": 2, "prompt": 1, "completion": 1, "latency_ms": 1}])
        self.assertEqual((t["first_output"]["n"], t["first_output"]["median"], t["reuse"]["share"], t["server_prompt"]["median"]), (0, None, None, None))

    def test_the_series_reads_the_new_columns_and_an_old_row_gives_none(self):
        new = {"step": 1, "prompt_tokens": 9, "completion_tokens": 1, "latency_ms": 5000, "first_output_ms": 1200.5, "first_text_ms": 1300.0, "after_first_ms": 3700.0,
               "cached_tokens": 8, "cache_creation_tokens": None, "server_prompt_n": 1, "server_prompt_ms": 30.9, "server_cache_n": 8, "server_predicted_n": 5, "server_predicted_ms": 60.0}
        old = {"step": 2, "prompt_tokens": 9, "completion_tokens": 1, "latency_ms": 5000}
        a, b = metrics.series([old, new])[0], metrics.series([old, new])[1]  # in step order: new first
        self.assertEqual((a["first_output_ms"], a["cached"], a["server_cache_n"], a["server_prompt_ms"]), (1200.5, 8, 8, 30.9))
        self.assertTrue(all(b[k] is None for k in ("first_output_ms", "first_text_ms", "after_first_ms", "cached", "server_prompt_ms")))

    def test_the_query_reads_the_whole_step_row_so_that_an_old_schema_still_works(self):
        q = metrics.run_query("run_1")
        self.assertIn("to_jsonb(r)", q)
        self.assertNotIn("first_output_ms", q)


class TimingReport(unittest.TestCase):
    def recs(self, with_timing=True, first=2000, cache=900):
        out = []
        for rep in (1, 2, 3):
            r = row("L1", rep, 1, 2, [100, 200], total=2)
            r2 = row("L1", rep, 2, 2, [100, 200], total=2)
            if with_timing:
                for rr in (r, r2):
                    for s in rr["series"]:
                        s.update(latency_ms=10000, first_output_ms=first * rep, server_cache_n=cache, server_prompt_n=1000 - cache, server_prompt_ms=250.0)
                        s["prompt"] = 1000
            out += [r, r2]
        return out

    def test_a_report_per_turn_gives_median_p90_share_reuse_and_server_time(self):
        text = report.render("new", self.recs(), {})
        # 6 steps of each turn: first output 2, 2, 4, 4, 6, 6 s of 10 s steps
        self.assertIn("Time before the first output and reuse of the prompt", text)
        self.assertIn("| 1 | 6/6 | 4.0 (6.0) |", text)
        self.assertIn("5,400 of 6,000 (90%); processed 600", text)
        self.assertIn("| 250 (250) |", text)
        self.assertIn("| 2 | 6/6 |", text)

    def test_against_puts_the_two_side_by_side(self):
        text = report.render("new", self.recs(), {}, "old", self.recs(first=1000, cache=0))
        head = [l for l in text.splitlines() if l.startswith("| turn | steps with a first-output time (new)")]
        self.assertEqual(len(head), 1)
        self.assertIn("(old)", head[0])
        row1 = [l for l in text.splitlines() if l.startswith("| 1 | 6/6 | 6/6 | 4.0 (6.0) | 2.0 (3.0) |")]
        self.assertEqual(len(row1), 1, text)
        self.assertIn("0 of 6,000 (0%); processed 6,000", row1[0])

    def test_old_results_without_the_fields_load_and_say_so(self):
        text = report.render("old", self.recs(with_timing=False), {})
        self.assertIn("Time to first output and prompt reuse: not recorded", text)
        # one side has them and the other does not: the missing side is n/a, nothing breaks
        text = report.render("new", self.recs(), {}, "old", self.recs(with_timing=False))
        row1 = [l for l in text.splitlines() if l.startswith("| 1 | 6/6 | 0/6 | 4.0 (6.0) | n/a |")]
        self.assertEqual(len(row1), 1, text)

    def test_a_non_streamed_step_is_not_counted_as_a_first_output(self):
        r = row("L1", 1, 1, 1, [100])
        r["series"][0].update(first_output_ms=None)
        text = report.render("x", [r], {})
        self.assertIn("not recorded", text)


if __name__ == "__main__":
    unittest.main()
