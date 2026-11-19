"""Tests of tools/web_eval/e4pdf.py (E4-PDF, ADR-0003 appendix D V3): the matching rules, the split rule, the command
protocol and the limits, the scorer's arithmetic, the held-out guard and the adversarial generator. Standard library
only; no network, no Docker, no real PDFs (the documents of the set live in .local and are never in the repository)."""
import argparse
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from tools.web_eval import e4pdf
from tools.web_eval.e4pdf import Text, norm, passage_found

PY = sys.executable


def stub(dirpath: Path, name: str, body: str) -> str:
    p = dirpath / name
    p.write_text(body)
    return f"{PY} {p}"


class Normalisation(unittest.TestCase):
    def found(self, passage, text, **kw):
        return passage_found(Text(text), {"text": passage, **kw})

    def test_nfkc_ligatures_and_fullwidth(self):
        self.assertTrue(self.found("office file", "the ﬁle of the oﬃce: office file"))
        self.assertTrue(self.found("税号 8471", "税号 ８４７１"))

    def test_soft_hyphen_and_zero_width_are_dropped(self):
        self.assertTrue(self.found("international shipping", "inter­national ship​ping"))

    def test_line_end_hyphenation_is_joined_and_kept_hyphens_match(self):
        self.assertTrue(self.found("cross-border trade", "cross-\nborder trade"))   # split at the hyphen
        self.assertTrue(self.found("cross-border trade", "cross-border trade"))     # nothing to undo
        self.assertTrue(self.found("customs clearance", "cus-\n  toms clearance"))  # split inside a word, indent
        self.assertTrue(self.found("customs clearance", "cus­\ntoms clearance"))
        self.assertFalse(self.found("customs clearance", "cus-\ntoms clear"))       # still has to be all there
        # a hyphen at the end of a line that follows a digit or space is not hyphenation, and does not eat the next line
        self.assertEqual(norm("rate 5-\n10 kg", False), "rate 5 10 kg")  # after a digit: no join, the line break stays a space
        self.assertEqual(norm("A -\nB", False), "A B")

    def test_latin_white_space_is_collapsed_not_removed(self):
        self.assertTrue(self.found("fuel surcharge applies", "fuel\n  surcharge \t applies"))
        self.assertFalse(self.found("fuel surcharge", "fuelsurcharge"))  # words may not be glued together

    def test_cjk_white_space_is_removed_both_ways(self):
        self.assertTrue(self.found("海关总署公告2024年第", "海 关 总 署\n公 告 2024 年 第"))
        self.assertTrue(self.found("海 关 总 署 公 告", "海关总署公告"))
        self.assertFalse(self.found("海关总署公告", "海关总署令"))

    def test_text_not_there_is_not_found(self):
        self.assertFalse(self.found("customs duty", "customs  tariff"))
        self.assertFalse(self.found("", "anything"))

    def test_table_cells_in_order_within_the_window(self):
        row = {"text": "Zone 1 | 0.5 kg | 25.00", "kind": "table", "cells": ["Zone 1", "0.5 kg", "25.00"]}
        self.assertTrue(passage_found(Text("Zone 1   0.5 kg   25.00\nZone 2 1.0 kg 31.00"), row))
        self.assertTrue(passage_found(Text("Zone 1 | 0.5 kg | other column text | 25.00"), row))
        self.assertFalse(passage_found(Text("Zone 1 0.5 kg\nZone 2 1.0 kg 25.00"), row.copy() | {"cells": ["Zone 1", "0.5 kg", "31.00"]}))
        self.assertFalse(passage_found(Text("25.00 0.5 kg Zone 1"), row))           # wrong order
        far = "Zone 1 0.5 kg " + "x" * (e4pdf.TABLE_GAP + 30) + " 25.00"
        self.assertFalse(passage_found(Text(far), row))                              # too far apart: another row
        self.assertTrue(passage_found(Text("Zone 1 9 Zone 1 0.5 kg 25.00"), row))   # first cell occurs twice, the second works

    def test_cjk_table_cells(self):
        row = {"text": "首重 | 续重", "kind": "table", "cells": ["首重", "续重", "12元"]}
        self.assertTrue(passage_found(Text("首 重\n续 重 12 元"), row))


class Split(unittest.TestCase):
    LANGS = {f"zh-{i:02d}": "zh" for i in range(15)} | {f"en-{i:02d}": "en" for i in range(15)}

    def test_ten_of_thirty_stratified(self):
        sp = e4pdf.split_rule(self.LANGS)
        held = {i for i, v in sp.items() if v == "held"}
        self.assertEqual(len(held), 10)
        self.assertEqual(sum(1 for i in held if self.LANGS[i] == "zh"), 5)
        self.assertEqual(sum(1 for i in held if self.LANGS[i] == "en"), 5)

    def test_it_depends_on_the_id_and_language_only(self):
        a = e4pdf.split_rule(self.LANGS)
        shuffled = dict(reversed(list(self.LANGS.items())))
        self.assertEqual(a, e4pdf.split_rule(shuffled))
        by_hash = sorted((i for i, l in self.LANGS.items() if l == "zh"), key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        self.assertEqual({i for i in by_hash[:5]}, {i for i, v in a.items() if v == "held" and i.startswith("zh")})

    def test_sizes_for_other_counts(self):
        for n, k in ((1, 0), (2, 1), (3, 1), (4, 1), (5, 2), (14, 5), (15, 5), (16, 5), (17, 6)):
            sp = e4pdf.split_rule({f"d{i}": "en" for i in range(n)})
            self.assertEqual(sum(v == "held" for v in sp.values()), k, n)


class Protocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_stub(self, body, data=b"%PDF-1.4 x", **kw):
        return e4pdf.run_command(stub(self.d, "s.py", body), data, **kw)

    def test_stdin_to_stdout_with_exit_zero(self):
        r = self.run_stub("import sys; d=sys.stdin.buffer.read(); sys.stdout.write('got %d bytes 税号' % len(d))", b"x" * 1000)
        self.assertEqual((r["status"], r["text"]), ("text", "got 1000 bytes 税号"))

    def test_large_input_does_not_deadlock(self):
        r = self.run_stub("import sys; d=sys.stdin.buffer.read(); sys.stdout.write('y'*2000000)", b"x" * 4_000_000)
        self.assertEqual(len(r["text"]), 2_000_000)

    def test_a_reason_word_on_stderr(self):
        for w in e4pdf.REASONS:
            r = self.run_stub(f"import sys; print('{w}: because', file=sys.stderr); sys.exit(3)")
            self.assertEqual((r["status"], r["reason"]), ("reason", w))

    def test_nonzero_without_a_reason_word_is_a_protocol_failure(self):
        r = self.run_stub("import sys; print('panic: nil pointer', file=sys.stderr); sys.exit(2)")
        self.assertEqual(r["status"], "bad_exit")
        r = self.run_stub("import sys; sys.exit(1)")
        self.assertEqual(r["status"], "bad_exit")
        r = self.run_stub("import sys; print('malformedx', file=sys.stderr); sys.exit(1)")  # a word, not a prefix of one
        self.assertEqual(r["status"], "bad_exit")

    def test_the_reason_may_follow_warnings(self):
        r = self.run_stub("import sys; print('WARNING: odd xref', file=sys.stderr); print('malformed: no header', file=sys.stderr); sys.exit(1)")
        self.assertEqual((r["status"], r["reason"]), ("reason", "malformed"))
        r = self.run_stub("import sys; print('WARNING: odd xref', file=sys.stderr); sys.exit(1)")
        self.assertEqual(r["status"], "bad_exit")

    def test_the_reason_may_follow_blank_lines_and_ignores_stdout(self):
        r = self.run_stub("import sys; print('some text'); print('', file=sys.stderr); print('encrypted', file=sys.stderr); sys.exit(1)")
        self.assertEqual((r["status"], r["reason"], r["text"]), ("reason", "encrypted", ""))

    def test_a_crash_by_signal(self):
        r = self.run_stub("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)")
        self.assertEqual(r["status"], "crashed")

    def test_a_command_that_cannot_start(self):
        self.assertEqual(e4pdf.run_command("/nonexistent/reader", b"x")["status"], "cannot_start")

    def test_wall_clock_limit_kills_a_sleeper_and_its_child(self):
        marker = self.d / "child-alive"
        body = ("import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', 'import time,pathlib; time.sleep(1.5); pathlib.Path(r\"{marker}\").write_text(\"x\")'])\n"
                "time.sleep(60)\n")
        r = self.run_stub(body, time_limit=0.6)
        self.assertEqual(r["status"], "timeout")
        self.assertLess(r["seconds"], 3)
        import time
        time.sleep(2)
        self.assertFalse(marker.exists(), "the child of the command survived the kill")

    def test_memory_limit_kills_an_allocator(self):
        r = self.run_stub("import time\nb = bytearray(600 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\ntime.sleep(30)",
                          mem_limit_mib=200, as_limit_mib=0)
        self.assertEqual(r["status"], "memory")
        self.assertGreater(r["peak_mib"], 200)
        self.assertLess(r["seconds"], 10)

    def test_peak_memory_is_measured_for_a_command_that_finishes(self):
        r = self.run_stub("b = bytearray(120 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\nprint('done')", mem_limit_mib=1000)
        self.assertEqual(r["status"], "text")
        self.assertGreater(r["peak_mib"], 110)
        self.assertLess(r["peak_mib"], 400)

    def test_address_space_limit_is_a_safety_net(self):
        r = self.run_stub("b = bytearray(900 * 1024 * 1024)\nprint('allocated')", mem_limit_mib=5000, as_limit_mib=400)
        self.assertNotEqual(r["status"], "text")  # MemoryError: a traceback, a non-zero exit

    def test_endless_output_is_cut(self):
        old = e4pdf.OUT_CAP
        e4pdf.OUT_CAP = 1 << 20
        try:
            r = self.run_stub("import sys\nwhile True: sys.stdout.write('z' * 65536); sys.stdout.flush()", time_limit=10)
        finally:
            e4pdf.OUT_CAP = old
        self.assertEqual(r["status"], "output_too_large")
        self.assertLess(r["seconds"], 8)


class Scoring(unittest.TestCase):
    def doc(self, **kw):
        return {"id": "d1", "lang": "en", "scanned": False, "passages": [
            {"text": "alpha beta gamma delta", "page": 1, "kind": "text", "ref": "both"},
            {"text": "epsilon zeta eta theta", "page": 2, "kind": "text", "ref": "one"},
            {"text": "iota kappa lambda mu nu", "page": 3, "kind": "text", "ref": "none", "reread": True},
            {"text": "omicron pi rho sigma tau", "page": 250, "kind": "text", "ref": "both"}], **kw}

    def res(self, text="", status="text", reason="", seconds=0.1, peak=30.0):
        return {"status": status, "text": text, "reason": reason, "detail": "", "seconds": seconds, "peak_mib": peak, "exit": 0}

    def test_kept_none_and_beyond_are_counted_apart(self):
        r = e4pdf.score_document(self.doc(), self.res("alpha beta gamma delta ... iota kappa lambda mu nu ... omicron pi rho sigma tau"), 5, 256)
        self.assertEqual((r["kept"], r["of"]), (1, 2))
        self.assertEqual((r["kept_none"], r["of_none"]), (1, 1))
        self.assertEqual((r["beyond_read"], r["of_beyond"]), (1, 1))
        self.assertFalse(r["all_kept"])
        r = e4pdf.score_document(self.doc(), self.res("alpha beta gamma delta epsilon zeta eta theta"), 5, 256)
        self.assertTrue(r["all_kept"])

    def test_a_reason_instead_of_text_keeps_nothing(self):
        r = e4pdf.score_document(self.doc(), self.res(status="reason", reason="malformed"), 5, 256)
        self.assertEqual((r["kept"], r["of"], r["all_kept"]), (0, 2, False))

    def test_scanned_expects_no_text_layer(self):
        d = {"id": "s", "lang": "zh", "scanned": True, "passages": []}
        self.assertTrue(e4pdf.score_document(d, self.res(status="reason", reason="no_text_layer"), 5, 256)["correct_scan"])
        self.assertFalse(e4pdf.score_document(d, self.res(status="reason", reason="malformed"), 5, 256)["correct_scan"])
        self.assertFalse(e4pdf.score_document(d, self.res(text="some text"), 5, 256)["correct_scan"])
        self.assertFalse(e4pdf.score_document(d, self.res(text=""), 5, 256)["correct_scan"])

    def test_limits_are_flagged(self):
        r = e4pdf.score_document(self.doc(), self.res("alpha beta gamma delta", seconds=6.0, peak=300.0), 5, 256)
        self.assertTrue(r["over_time"] and r["over_mem"])
        r = e4pdf.score_document(self.doc(), self.res(status="timeout", seconds=5.0), 5, 256)
        self.assertTrue(r["over_time"])

    def test_totals(self):
        rows = [e4pdf.score_document(self.doc(), self.res("alpha beta gamma delta epsilon zeta eta theta"), 5, 256),
                e4pdf.score_document(self.doc(), self.res("alpha beta gamma delta"), 5, 256),
                e4pdf.score_document({"id": "s", "lang": "zh", "scanned": True, "passages": []}, self.res(status="reason", reason="no_text_layer"), 5, 256)]
        t = e4pdf.totals(rows)
        self.assertEqual((t["kept"], t["of"], t["all_kept"], t["scans_correct"], t["scans"]), (3, 4, 1, 1, 1))
        self.assertAlmostEqual(t["pct"], 75.0)
        self.assertIn("NOT met", e4pdf.format_totals(t, "x", 5, 256))


class HeldOut(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        ann = []
        for lang in ("zh", "en"):
            for i in range(6):
                did = f"{lang}-{i}"
                (self.root / "docs" / f"{did}.pdf").write_bytes(b"%PDF-1.4 " + did.encode())
                ann.append({"id": did, "lang": lang, "scanned": False, "passages": [{"text": f"text of {did} here", "page": 1, "kind": "text", "ref": "both"}]})
        (self.root / "ann.jsonl").write_text("".join(json.dumps(a) + "\n" for a in ann))
        self.cmd = stub(self.root, "echo.py", "import sys; sys.stdin.buffer.read(); print('text of ' + 'zz-0' + ' here')")
        # a reader that prints the document's own passage: it reads the id from the 'PDF'
        self.cmd = stub(self.root, "echo2.py", "import sys; d=sys.stdin.buffer.read().decode().split()[-1]; print('text of ' + d + ' here')")

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kw):
        base = dict(root=str(self.root), cmd=self.cmd, time_limit=5.0, mem_limit=256.0, as_limit=0, held_out=False, adversarial=None,
                    docs=None, include_none=False, json=None)
        return argparse.Namespace(**(base | kw))

    def run_score(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            e4pdf.score_set(self.args(**kw))
        return buf.getvalue()

    def test_default_run_never_touches_held_out_documents(self):
        sp = e4pdf.split_rule({f"{l}-{i}": l for l in ("zh", "en") for i in range(6)})
        held = [i for i, v in sp.items() if v == "held"]
        out = self.run_score()
        self.assertEqual(len(held), 4)
        for h in held:
            self.assertNotIn(h, out)
        self.assertNotIn("HELD-OUT", out)
        self.assertFalse((self.root / "held-out-runs.log").exists())
        self.assertIn("8 documents", out)

    def test_held_out_needs_the_flag_says_what_it_is_for_and_is_logged(self):
        out = self.run_score(held_out=True)
        self.assertIn("single final confirmation", out)
        self.assertIn("4 documents", out)
        log = (self.root / "held-out-runs.log").read_text()
        self.assertEqual(len(log.splitlines()), 1)
        self.run_score(held_out=True)
        self.assertEqual(len((self.root / "held-out-runs.log").read_text().splitlines()), 2)

    def test_per_language_totals_and_the_split_on_file_is_checked(self):
        out = self.run_score()
        self.assertIn("development zh", out)
        self.assertIn("development en", out)
        self.assertIn("100.0%", out)
        (self.root / "split.json").write_text(json.dumps({"assignment": {"zh-0": "dev"}}))
        with self.assertRaises(SystemExit):
            self.run_score()


def small_args(**kw):
    return argparse.Namespace(**({"bomb_mib": 8, "depth": 300, "pages": 500} | kw))


class Adversarial(unittest.TestCase):
    def files(self):
        return {name: make(small_args()) for name, make in e4pdf.ADVERSARIAL.items()}

    def test_every_file_is_produced_and_none_is_large(self):
        files = self.files()
        self.assertGreaterEqual(len(files), 11)
        for name in ("empty.pdf", "html-as-pdf.pdf", "garbage-after-header.pdf", "truncated.pdf", "xref-missing.pdf", "xref-wrong.pdf",
                     "flate-bomb.pdf", "pages-deep.pdf", "pages-cycle.pdf", "pages-many.pdf", "resources-circular.pdf", "encrypted.pdf"):
            self.assertIn(name, files)
        for name, data in files.items():
            self.assertLess(len(data), 5 << 20, name)

    def test_the_command_writes_the_files(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            e4pdf.adversarial(argparse.Namespace(out=d, bomb_mib=2, depth=50, pages=50))
            self.assertEqual({p.name for p in Path(d).glob("*.pdf")}, set(e4pdf.ADVERSARIAL))

    def test_the_base_file_is_well_formed(self):
        data = e4pdf._simple()
        xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
        self.assertTrue(data[xref:].startswith(b"xref"))
        for n, off in enumerate(re.findall(rb"(\d{10}) 00000 n", data), start=1):
            self.assertTrue(data[int(off):].startswith(b"%d 0 obj" % n))

    def test_zero_bytes_html_and_garbage(self):
        f = self.files()
        self.assertEqual(f["empty.pdf"], b"")
        self.assertTrue(f["html-as-pdf.pdf"].lower().startswith(b"<!doctype html"))
        g = f["garbage-after-header.pdf"]
        self.assertTrue(g.startswith(b"%PDF-"))
        self.assertGreater(len(g), 100_000)
        self.assertNotIn(b" obj", g)
        self.assertEqual(g, e4pdf.make_garbage_after_header())  # deterministic

    def test_truncated_has_no_end_and_is_shorter_than_the_whole(self):
        f = self.files()["truncated.pdf"]
        self.assertTrue(f.startswith(b"%PDF-"))
        self.assertNotIn(b"%%EOF", f)
        self.assertNotIn(b"startxref", f)
        self.assertNotIn(b"endstream", f.split(b"stream", 1)[1])

    def test_missing_and_wrong_xref(self):
        f = self.files()
        self.assertNotIn(b"xref", f["xref-missing.pdf"])
        self.assertIn(b"obj", f["xref-missing.pdf"])
        w = f["xref-wrong.pdf"]
        self.assertIn(b"startxref\n99999999", w)
        offs = [int(o) for o in re.findall(rb"(\d{10}) 00000 n", w)]
        self.assertTrue(all(not w[o:].startswith(b"%d 0 obj" % n) for n, o in enumerate(offs, start=1)))

    def test_the_bomb_is_small_and_inflates_a_hundred_times_over(self):
        for mib in (8, 64):
            data = e4pdf.make_bomb(mib)
            body = data.split(b"stream\n", 1)[1].rsplit(b"\nendstream", 1)[0]
            d, total = zlib.decompressobj(), 0
            for i in range(0, len(body), 4096):
                total += len(d.decompress(body[i:i + 4096]))
            self.assertEqual(total, mib << 20)
            self.assertLess(len(data) * 100, total)
            self.assertIn(b"/Filter /FlateDecode", data)

    def test_the_default_bomb_inflates_to_hundreds_of_megabytes(self):
        self.assertGreaterEqual(e4pdf.DEFAULT_BOMB_MIB, 256)
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            e4pdf.main(["adversarial", "--out", d, "--bomb-mib", "1", "--depth", "10", "--pages", "10"])
            self.assertTrue((Path(d) / "flate-bomb.pdf").exists())

    def test_deep_nesting_cycle_and_many_pages(self):
        deep = e4pdf.make_deep_pages(300)
        self.assertEqual(len(re.findall(rb"/Type /Pages", deep)), 300)
        for n in (2, 150, 301):  # each node's kid is the next object: a chain 300 deep
            m = re.search(rb"\n%d 0 obj\n<< /Type /Pages? /Kids \[(\d+) 0 R\]" % n, deep)
            self.assertTrue(m and int(m.group(1)) == n + 1, n)
        cyc = e4pdf.make_page_cycle()
        self.assertIn(b"/Kids [2 0 R 3 0 R]", cyc)        # object 2 lists itself
        self.assertIn(b"/Kids [2 0 R] /Count 1 /Parent 2 0 R", cyc)  # and a child that lists it back
        many = e4pdf.make_many_pages(60000)
        self.assertEqual(many.count(b"3 0 R"), 60000 + 0)
        self.assertIn(b"/Count 60000", many)
        self.assertLess(len(many), 1 << 20)

    def test_circular_resources(self):
        c = e4pdf.make_circular_resources()
        self.assertIn(b"/Self 5 0 R", c)                   # the dictionary that holds it
        self.assertIn(b"/Resources 5 0 R", c)              # a font and a form pointing back at the page's resources
        self.assertRegex(c.decode("latin-1"), r"7 0 obj\n<< /Length \d+ /Type /XObject /Subtype /Form[^>]*>>\nstream\n/X0 Do")  # a form that draws itself

    def test_encrypted_marker(self):
        e = self.files()["encrypted.pdf"]
        self.assertIn(b"/Encrypt 5 0 R", e)
        self.assertIn(b"/Filter /Standard", e)

    def test_stream_length_xref_size_and_corrupt_flate(self):
        f = self.files()
        self.assertIn(b"/Length 2147483000", f["stream-length-lie.pdf"])
        self.assertIn(b"/Size 2000000000", f["xref-huge-size.pdf"])
        body = f["flate-corrupt.pdf"].split(b"stream\n", 1)[1].rsplit(b"\nendstream", 1)[0]
        with self.assertRaises(zlib.error):
            zlib.decompress(body)

    def test_no_adversarial_file_carries_text_to_print(self):
        for name, data in self.files().items():
            self.assertNotRegex(data[:100000], rb"\) ?Tj", name)


class AdversarialScoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "in").mkdir()
        for n, data in (("a.pdf", b""), ("b.pdf", b"<html>"), ("c.pdf", b"%PDF-1.4")):
            (self.d / "in" / n).write_bytes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def run_adv(self, body, **kw):
        args = argparse.Namespace(adversarial=str(self.d / "in"), cmd=stub(self.d, "s.py", body), time_limit=kw.get("time_limit", 1.5),
                                  mem_limit=kw.get("mem_limit", 256.0), as_limit=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = e4pdf.score_adversarial(args)
        return code, buf.getvalue()

    def test_a_reader_that_always_refuses_with_a_reason_passes(self):
        code, out = self.run_adv("import sys; sys.stdin.buffer.read(); print('malformed: no', file=sys.stderr); sys.exit(1)")
        self.assertEqual(code, 0, out)
        self.assertIn("3/3", out)

    def test_text_a_hang_a_crash_and_no_reason_each_fail(self):
        for body, word in (("print('some text')", "printed text"), ("import time; time.sleep(30)", "hang"),
                           ("import sys; sys.exit(2)", "without a reason"),
                           ("import os, signal; os.kill(os.getpid(), signal.SIGABRT)", "crashed")):
            code, out = self.run_adv(body)
            self.assertEqual(code, 1, body)
            self.assertIn(word, out)

    def test_memory_blow_up_fails(self):
        code, out = self.run_adv("import time\nb = bytearray(500 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\ntime.sleep(20)", mem_limit=150)
        self.assertEqual(code, 1)
        self.assertIn("memory", out)


if __name__ == "__main__":
    unittest.main()
