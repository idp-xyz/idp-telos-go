"""Tests of tools/acceptance.py (the tiered acceptance: path manifest, selection, verification, timings).

Written before the tool. The synthetic manifest below has the shapes of the real one (needs, a mixed real-embedding path, a
shared prefix between ids such as J and J0) so that the adversarial cases do not depend on tests/paths.yaml; the last class
checks the real manifest against the real scripts.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import acceptance as acc  # noqa: E402
sys.path.remove(str(TOOLS))   # tools/platform.py would shadow the standard library's platform for the tests imported after this one


def manifest_data():
    return {
        "version": 1,
        "components": ["x", "y", "z", "w", "emb"],
        "smoke": ["A"],
        "paths": [
            {"id": "A", "pass": ["A"], "components": ["x"], "needs": [], "budgetSeconds": 60},
            {"id": "B", "pass": ["B"], "components": ["y"], "needs": [], "budgetSeconds": 60, "uses": ["bob"]},
            {"id": "C", "pass": ["C"], "components": ["z"], "needs": ["B"], "budgetSeconds": 60},
            {"id": "J0", "pass": ["J0"], "components": ["w"], "needs": [], "budgetSeconds": 60},
            {"id": "J", "pass": ["J"], "components": ["w", "y"], "needs": ["J0"], "budgetSeconds": 60},
            {"id": "O", "pass": ["O1", "O1b", "O2"], "components": ["z"], "needs": [], "budgetSeconds": 90},
            {"id": "P0", "pass": ["P0a", "P0b"], "components": ["x"], "needs": [], "budgetSeconds": 30, "uses": ["bob"], "erases": ["bob"],
             "script": "deploy/compose/path_p0.sh"},
            {"id": "P1", "pass": ["P1a", "P1b", "P1c"], "components": ["x", "emb"], "needs": [], "budgetSeconds": 100,
             "realEmbedding": ["P1b", "P1c"], "realBudgetSeconds": 300, "script": "deploy/compose/path_p1.sh"},
            {"id": "P2", "pass": ["P2a"], "components": ["x"], "needs": ["P1"], "budgetSeconds": 100, "t3": True,
             "files": ["deploy/compose/kb3_docs.py"]},
        ],
    }


def make(data=None):
    return acc.parse_manifest(data if data is not None else manifest_data())


class ManifestTest(unittest.TestCase):
    def bad(self, mutate, pattern):
        d = manifest_data()
        mutate(d)
        with self.assertRaisesRegex(acc.ManifestError, pattern):
            acc.parse_manifest(d)

    def test_valid(self):
        self.assertEqual(make().ids, ["A", "B", "C", "J0", "J", "O", "P0", "P1", "P2"])

    def test_duplicate_id(self):
        self.bad(lambda d: d["paths"].append(dict(d["paths"][0])), "duplicate path id")

    def test_unknown_need(self):
        self.bad(lambda d: d["paths"][2].update(needs=["NOPE"]), "unknown path.*NOPE")

    def test_self_need(self):
        self.bad(lambda d: d["paths"][2].update(needs=["C"]), "needs itself|cycle")

    def test_cycle(self):
        # B needs C and C needs B: both exist, neither is before the other
        self.bad(lambda d: d["paths"][1].update(needs=["C"]), "cycle")

    def test_long_cycle(self):
        def mutate(d):
            d["paths"][0]["needs"] = ["C"]  # A -> C -> B -> A
            d["paths"][1]["needs"] = ["A"]
        self.bad(mutate, "cycle")

    def test_need_on_a_later_path(self):
        self.bad(lambda d: d["paths"][1].update(needs=["J"]), "before")

    def test_empty_pass(self):
        self.bad(lambda d: d["paths"][0].update({"pass": []}), "pass")

    def test_duplicate_pass_id_across_paths(self):
        self.bad(lambda d: d["paths"][1].update({"pass": ["A"]}), "duplicate PASS id")

    def test_real_embedding_must_be_a_subset_of_pass(self):
        self.bad(lambda d: d["paths"][7].update({"realEmbedding": ["P1z"]}), "realEmbedding")

    def test_unknown_component(self):
        self.bad(lambda d: d["paths"][0].update({"components": ["nope"]}), "unknown component")

    def test_budget_must_be_positive_int(self):
        self.bad(lambda d: d["paths"][0].update({"budgetSeconds": 0}), "budgetSeconds")
        self.bad(lambda d: d["paths"][0].update({"budgetSeconds": "60"}), "budgetSeconds")
        self.bad(lambda d: d["paths"][0].pop("budgetSeconds"), "budgetSeconds")

    def test_smoke_must_exist(self):
        self.bad(lambda d: d.update(smoke=["Q"]), "smoke")

    def test_a_subject_erased_then_used_is_refused(self):
        # P0 erases bob; a path after it that uses bob would run against a subject that is gone
        def mutate(d):
            d["paths"].append({"id": "Z", "pass": ["Z"], "components": ["x"], "needs": [], "budgetSeconds": 10, "uses": ["bob"]})
        self.bad(mutate, "bob")

    def test_empty_manifest(self):
        with self.assertRaises(acc.ManifestError):
            acc.parse_manifest({"version": 1, "components": ["x"], "paths": []})
        with self.assertRaises(acc.ManifestError):
            acc.parse_manifest([])

    def test_id_shape(self):
        self.bad(lambda d: d["paths"][0].update({"id": "a b"}), "id")


class ClosureAndSelectTest(unittest.TestCase):
    def setUp(self):
        self.m = make()

    def test_closure_follows_needs_transitively_in_manifest_order(self):
        self.assertEqual(acc.closure(self.m, ["C"]), ["B", "C"])
        self.assertEqual(acc.closure(self.m, ["P2"]), ["P1", "P2"])

    def test_closure_is_ordered_whatever_the_input_order(self):
        self.assertEqual(acc.closure(self.m, ["P2", "J", "B"]), ["B", "J0", "J", "P1", "P2"])

    def test_closure_of_unknown_id(self):
        with self.assertRaises(acc.SelectionError):
            acc.closure(self.m, ["Q"])

    def test_select_intersection_with_smoke_first(self):
        self.assertEqual(acc.select(self.m, ["y"]), ["A", "B", "J0", "J"])  # J has y and needs J0

    def test_select_adds_needs(self):
        self.assertEqual(acc.select(self.m, ["z"]), ["A", "B", "C", "O"])

    def test_select_empty_set_is_the_smoke_path_only(self):
        self.assertEqual(acc.select(self.m, []), ["A"])
        self.assertEqual(acc.select(self.m, set()), ["A"])

    def test_select_unknown_component_is_an_error_not_an_empty_selection(self):
        with self.assertRaisesRegex(acc.SelectionError, "unknown component.*nope"):
            acc.select(self.m, ["nope"])

    def test_select_is_case_sensitive_and_trims(self):
        self.assertEqual(acc.select(self.m, [" y "]), ["A", "B", "J0", "J"])
        with self.assertRaises(acc.SelectionError):
            acc.select(self.m, ["Y"])

    def test_select_order_is_the_manifests_whatever_the_changed_order(self):
        self.assertEqual(acc.select(self.m, ["w", "z", "y"]), acc.select(self.m, ["y", "w", "z"]))
        self.assertEqual(acc.select(self.m, ["w", "z", "y"]), ["A", "B", "C", "J0", "J", "O"])

    def test_select_duplicates(self):
        self.assertEqual(acc.select(self.m, ["y", "y"]), ["A", "B", "J0", "J"])

    def test_select_all(self):
        self.assertEqual(acc.select(self.m, acc.ALL), self.m.ids)

    def test_smoke_is_always_there_even_when_it_is_not_selected_by_components(self):
        self.assertEqual(acc.select(self.m, ["w"])[0], "A")

    def test_t3_is_real_embedding_paths_plus_t3_flagged_plus_needs(self):
        self.assertEqual(acc.select_t3(self.m), ["A", "P1", "P2"])

    def test_explicit_paths(self):
        self.assertEqual(acc.select_paths(self.m, ["C"]), ["A", "B", "C"])
        self.assertEqual(acc.select_paths(self.m, ["C"], smoke=False), ["B", "C"])
        with self.assertRaises(acc.SelectionError):
            acc.select_paths(self.m, ["nope"])


class AutoTest(unittest.TestCase):
    def setUp(self):
        self.m = make()

    def classify(self, files):
        return acc.classify_files(self.m, files)

    def test_a_path_script_selects_its_path(self):
        c = self.classify(["deploy/compose/path_p0.sh"])
        self.assertEqual(c.paths, {"P0"})
        self.assertFalse(c.everything)

    def test_a_mapped_helper_selects_its_path(self):
        self.assertEqual(self.classify(["deploy/compose/kb3_docs.py"]).paths, {"P2"})

    def test_global_files_select_everything(self):
        for f in ["deploy/keycloak/realm.py", "deploy/keycloak/sub/x.json", "deploy/compose/platform.yaml", "deploy/compose/e2e.sh",
                  "tools/acceptance.py", "tests/paths.yaml", "deploy/compose/lib_acceptance.sh"]:
            self.assertTrue(self.classify([f]).everything, f)

    def test_an_unmapped_file_under_deploy_compose_is_everything(self):
        # a helper nobody mapped could belong to any path: the safe answer is all of them
        self.assertTrue(self.classify(["deploy/compose/new_helper.py"]).everything)

    def test_a_path_script_that_is_not_in_the_manifest_is_everything(self):
        self.assertTrue(self.classify(["deploy/compose/path_q9.sh"]).everything)

    def test_things_that_do_not_change_the_compose_acceptance(self):
        for f in ["README.md", "docs/adr/x.md", "tests/kind/full.sh", "deploy/kubernetes/full/kustomization.yaml", ".github/workflows/kind.yml",
                  "tests/ACCEPTANCE.md", "reports/x.json"]:
            c = self.classify([f])
            self.assertFalse(c.everything, f)
            self.assertEqual(c.paths, set(), f)

    def test_no_files(self):
        c = self.classify([])
        self.assertFalse(c.everything)
        self.assertEqual(c.paths, set())

    def test_windows_separators_and_blank_lines(self):
        self.assertEqual(self.classify(["", "deploy\\compose\\path_p0.sh"]).paths, {"P0"})

    def test_bom_diff_names_the_components_whose_commit_moved(self):
        old = "components:\n- name: x\n  commit: aaa\n  image: {reference: x:dev, id: sha256:1}\n- name: y\n  commit: bbb\n  image: {reference: y:dev, id: sha256:2}\n"
        new = "components:\n- name: x\n  commit: aaa\n  image: {reference: x:dev, id: sha256:1}\n- name: y\n  commit: ccc\n  image: {reference: y:dev, id: sha256:2}\n"
        self.assertEqual(acc.bom_changed_components(old, new), {"y"})

    def test_bom_diff_image_only_change_counts(self):
        old = "components:\n- name: x\n  commit: aaa\n  image: {reference: x:dev, id: sha256:1}\n"
        new = "components:\n- name: x\n  commit: aaa\n  image: {reference: x:dev, id: sha256:9}\n"
        self.assertEqual(acc.bom_changed_components(old, new), {"x"})

    def test_bom_diff_added_removed_and_missing_old(self):
        old = "components:\n- name: x\n  commit: aaa\n"
        new = "components:\n- name: y\n  commit: bbb\n"
        self.assertEqual(acc.bom_changed_components(old, new), {"x", "y"})
        self.assertEqual(acc.bom_changed_components(None, new), {"y"})   # no BOM at the base: everything in the new one is new
        self.assertEqual(acc.bom_changed_components(old, old), set())

    def test_bom_diff_comments_and_release_fields_are_not_changes(self):
        old = "# a\nrelease: 0.1.0-dev\ncomponents:\n- name: x\n  commit: aaa\n"
        new = "# b\nrelease: 0.2.0-dev\ncomponents:\n- name: x\n  commit: aaa\n"
        self.assertEqual(acc.bom_changed_components(old, new), set())

    def test_bom_diff_garbage_is_an_error(self):
        with self.assertRaises(acc.SelectionError):
            acc.bom_changed_components("components: [", "components: []")

    def test_auto_selection(self):
        old = "components:\n- name: z\n  commit: aaa\n"
        new = "components:\n- name: z\n  commit: bbb\n"
        self.assertEqual(acc.auto_selection(self.m, ["deploy/compose/path_p0.sh"], old, new), ["A", "B", "C", "O", "P0"])

    def test_auto_selection_everything(self):
        self.assertEqual(acc.auto_selection(self.m, ["deploy/compose/e2e.sh"], None, None), self.m.ids)

    def test_auto_selection_nothing_is_the_smoke_path(self):
        self.assertEqual(acc.auto_selection(self.m, ["README.md"], "components: []\n", "components: []\n"), ["A"])

    def test_a_bom_component_that_the_manifest_does_not_know_selects_nothing(self):
        # a new component in the BOM (idp-eii, say) that no path exercises: only the smoke path runs; the manifest need not list it
        self.assertEqual(acc.auto_selection(self.m, [], "components: []\n", "components:\n- name: brand-new\n  commit: aaa\n"), ["A"])


LOG_OK = """== setup
PASS: PATH A — fine
PASS: PATH B — fine
SKIPPED: PATH C (not selected)
PASS: PATH J0 — x
PASS: PATH O1 — x
PASS: PATH O1b — x
PASS: PATH O2 — x
"""


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.m = make()

    def v(self, log, paths, embed="double"):
        return acc.verify_log(self.m, log, paths, embed=embed)

    def test_every_selected_id_present(self):
        self.assertEqual(self.v(LOG_OK, ["A", "B", "J0", "O"]), [])

    def test_unselected_paths_may_be_absent(self):
        self.assertEqual(self.v("PASS: PATH A — x\n", ["A"]), [])

    def test_a_missing_id_is_reported_by_name(self):
        p = self.v(LOG_OK.replace("PASS: PATH O1b — x\n", ""), ["A", "O"])
        self.assertEqual(len(p), 1)
        self.assertIn("O1b", p[0])

    def test_prefix_ids_do_not_stand_in_for_each_other(self):
        # J0 passing must not count for J, and O1b not for O1
        p = self.v("PASS: PATH A — x\nPASS: PATH J0 — x\n", ["A", "J"])
        self.assertTrue(any(re.search(r"\bJ\b", x) for x in p), p)
        p2 = self.v("PASS: PATH O1b — x\nPASS: PATH O2 — x\n", ["O"])
        self.assertTrue(any(re.search(r"\bO1\b", x) for x in p2), p2)

    def test_pass_must_start_the_line(self):
        self.assertTrue(self.v("echo PASS: PATH A\n", ["A"]))
        self.assertTrue(self.v("  PASS: PATH A\n", ["A"]))

    def test_fail_line_fails_even_when_everything_passed(self):
        p = self.v(LOG_OK + "FAIL: PATH P1 (b): something\n", ["A"])
        self.assertTrue(any("FAIL" in x for x in p))

    def test_blocked_line_fails(self):
        p = self.v(LOG_OK + "BLOCKED: image x missing\n", ["A"])
        self.assertTrue(any("BLOCKED" in x for x in p))

    def test_not_run_and_skipped_lines_are_not_failures(self):
        self.assertEqual(self.v(LOG_OK + "NOT_RUN: PATH C needs uv\nSKIPPED: PATH D (not selected)\n", ["A"]), [])

    def test_a_selected_path_that_printed_skipped_is_a_failure(self):
        p = self.v("PASS: PATH A\nSKIPPED: PATH B (not selected)\n", ["A", "B"])
        self.assertTrue(any("B" in x for x in p))

    def test_real_embedding_ids_in_double_mode_need_their_not_run_line(self):
        base = "PASS: PATH A\nPASS: PATH P1a\n"
        p = self.v(base, ["A", "P1"])
        self.assertTrue(any("P1b" in x for x in p), p)
        ok = base + "NOT_RUN: PATH P1b needs real embeddings (EMBED=double)\nNOT_RUN: PATH P1c needs real embeddings (EMBED=double)\n"
        self.assertEqual(self.v(ok, ["A", "P1"]), [])

    def test_real_embedding_ids_in_real_mode_need_pass(self):
        ok = "PASS: PATH A\nPASS: PATH P1a\nNOT_RUN: PATH P1b needs real embeddings (EMBED=double)\nNOT_RUN: PATH P1c needs real embeddings (EMBED=double)\n"
        self.assertTrue(self.v(ok, ["A", "P1"], embed="real"))
        full = "PASS: PATH A\nPASS: PATH P1a\nPASS: PATH P1b\nPASS: PATH P1c\n"
        self.assertEqual(self.v(full, ["A", "P1"], embed="real"), [])

    def test_empty_selection_is_refused(self):
        with self.assertRaises(acc.SelectionError):
            self.v("PASS: PATH A\n", [])

    def test_unknown_path_is_refused(self):
        with self.assertRaises(acc.SelectionError):
            self.v("PASS: PATH A\n", ["A", "Q"])

    def test_an_empty_log_fails(self):
        self.assertTrue(self.v("", ["A"]))

    def test_unknown_embed_mode(self):
        with self.assertRaises(acc.SelectionError):
            self.v(LOG_OK, ["A"], embed="maybe")

    def test_crlf_log(self):
        self.assertEqual(self.v("PASS: PATH A\r\nPASS: PATH B\r\n", ["A", "B"]), [])

    def test_ansi_prefixed_lines_do_not_pass(self):
        self.assertTrue(self.v("\x1b[32mPASS: PATH A\x1b[0m\n", ["A"]))


class TimingsTest(unittest.TestCase):
    def setUp(self):
        self.m = make()
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Path(self.tmp.name) / "t.json"

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return json.loads(self.f.read_text())

    def test_record_creates_and_merges(self):
        acc.record_timing(self.f, "A", 100.0, 130.5)
        acc.record_timing(self.f, "B", 131.0, 200.0)
        d = self.load()
        self.assertEqual(sorted(d["paths"]), ["A", "B"])
        self.assertAlmostEqual(d["paths"]["A"]["seconds"], 30.5)

    def test_record_survives_a_corrupt_file(self):
        self.f.write_text("{not json")
        acc.record_timing(self.f, "A", 1, 2)
        self.assertIn("A", self.load()["paths"])

    def test_record_rejects_reversed_times(self):
        with self.assertRaises(acc.SelectionError):
            acc.record_timing(self.f, "A", 10, 5)

    def test_report_table_total_and_no_warning_within_budget(self):
        acc.record_timing(self.f, "A", 0, 50)
        acc.record_timing(self.f, "B", 50, 100)
        text, warns = acc.timings_report(self.m, self.load(), mode="compose", embed="double")
        self.assertEqual(warns, [])
        self.assertIn("A", text)
        self.assertIn("total", text.lower())
        self.assertIn("100", text)

    def test_over_budget_warns_and_does_not_raise(self):
        acc.record_timing(self.f, "A", 0, 61)
        _, warns = acc.timings_report(self.m, self.load(), mode="compose", embed="double")
        self.assertEqual(warns, ["WARN: PATH A took 61s (budget 60s)"])

    def test_real_mode_uses_the_real_budget_when_there_is_one(self):
        acc.record_timing(self.f, "P1", 0, 200)
        t = self.load()
        self.assertEqual(acc.timings_report(self.m, t, mode="compose", embed="double")[1], ["WARN: PATH P1 took 200s (budget 100s)"])
        self.assertEqual(acc.timings_report(self.m, t, mode="compose", embed="real")[1], [])

    def test_kind_mode_uses_the_kind_budget_and_skips_paths_without_one(self):
        d = manifest_data()
        d["paths"][0]["kindBudgetSeconds"] = 10
        m = acc.parse_manifest(d)
        acc.record_timing(self.f, "A", 0, 20)
        acc.record_timing(self.f, "B", 20, 9999)  # B has no kind budget: reported, never warned
        _, warns = acc.timings_report(m, self.load(), mode="kind", embed="double")
        self.assertEqual(warns, ["WARN: PATH A took 20s (budget 10s)"])

    def test_unknown_path_in_the_timings_is_listed_not_fatal(self):
        acc.record_timing(self.f, "ZZ", 0, 5)
        text, warns = acc.timings_report(self.m, self.load(), mode="compose", embed="double")
        self.assertIn("ZZ", text)
        self.assertEqual(warns, [])

    def test_empty_timings(self):
        text, warns = acc.timings_report(self.m, {"paths": {}}, mode="compose", embed="double")
        self.assertEqual(warns, [])
        self.assertIn("total", text.lower())

    def test_incomplete_path_is_marked(self):
        acc.record_timing(self.f, "A", 0, 5, status="incomplete")
        text, _ = acc.timings_report(self.m, self.load(), mode="compose", embed="double")
        self.assertIn("incomplete", text)

    def test_table_follows_manifest_order(self):
        acc.record_timing(self.f, "P1", 0, 5)
        acc.record_timing(self.f, "A", 5, 6)
        text, _ = acc.timings_report(self.m, self.load(), mode="compose", embed="double")
        self.assertLess(text.index("\nA "), text.index("\nP1 "))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        import yaml
        self.manifest = self.dir / "paths.yaml"
        self.manifest.write_text(yaml.safe_dump(manifest_data()))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(TOOLS / "acceptance.py"), "--manifest", str(self.manifest), *args],
                              capture_output=True, text=True, cwd=self.dir)

    def test_select_changed_list(self):
        r = self.run_cli("select", "--changed", "y")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["A", "B", "J0", "J"])

    def test_select_all(self):
        self.assertEqual(self.run_cli("select", "--changed", "all").stdout.split(), make().ids)

    def test_select_unknown_component_exits_2(self):
        r = self.run_cli("select", "--changed", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("nope", r.stderr)

    def test_select_empty_changed_is_smoke(self):
        self.assertEqual(self.run_cli("select", "--changed", "").stdout.split(), ["A"])

    def test_select_t3(self):
        self.assertEqual(self.run_cli("select", "--t3").stdout.split(), ["A", "P1", "P2"])

    def test_select_auto_against_a_git_base(self):
        repo = self.dir / "repo"
        (repo / "releases").mkdir(parents=True)
        (repo / "releases/platform-bom.yaml").write_text("components:\n- name: y\n  commit: aaa\n")
        g = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)
        g("init", "-q", "-b", "main")
        g("add", "-A")
        g("commit", "-qm", "base")
        (repo / "releases/platform-bom.yaml").write_text("components:\n- name: y\n  commit: bbb\n")
        (repo / "deploy/compose").mkdir(parents=True)
        (repo / "deploy/compose/path_p0.sh").write_text("echo\n")
        g("add", "-A")
        g("commit", "-qm", "change")
        r = self.run_cli("select", "--changed", "auto", "--base", "HEAD~1", "--root", str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["A", "B", "J0", "J", "P0"])

    def test_select_auto_with_a_base_that_does_not_exist_is_an_error(self):
        repo = self.dir / "repo2"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, capture_output=True)
        r = self.run_cli("select", "--changed", "auto", "--base", "nonexistent-ref", "--root", str(repo))
        self.assertEqual(r.returncode, 2)

    def test_verify_ok_and_failing(self):
        log = self.dir / "e2e.log"
        log.write_text(LOG_OK)
        self.assertEqual(self.run_cli("verify", "--log", str(log), "--paths", "A,B,J0,O").returncode, 0)
        self.assertEqual(self.run_cli("verify", "--log", str(log), "--paths", "A B P0").returncode, 1)
        log.write_text(LOG_OK + "FAIL: x\n")
        r = self.run_cli("verify", "--log", str(log), "--paths", "A")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)

    def test_verify_missing_log_and_empty_paths(self):
        self.assertEqual(self.run_cli("verify", "--log", str(self.dir / "nope.log"), "--paths", "A").returncode, 2)
        (self.dir / "l").write_text("PASS: PATH A\n")
        self.assertEqual(self.run_cli("verify", "--log", str(self.dir / "l"), "--paths", "").returncode, 2)

    def test_verify_paths_all(self):
        (self.dir / "l").write_text("PASS: PATH A\n")
        self.assertEqual(self.run_cli("verify", "--log", str(self.dir / "l"), "--paths", "all").returncode, 1)

    def test_validate(self):
        self.assertEqual(self.run_cli("validate", "--paths", "A B").returncode, 0)
        self.assertEqual(self.run_cli("validate", "--paths", "A,Q").returncode, 2)
        self.assertEqual(self.run_cli("validate", "--paths", "").returncode, 0)   # unset PATHS means all

    def test_record_and_report_never_fail_on_budget(self):
        t = self.dir / "t.json"
        self.assertEqual(self.run_cli("record", "--timings", str(t), "--id", "A", "--start", "0", "--end", "500").returncode, 0)
        r = self.run_cli("report", "--timings", str(t))
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARN: PATH A took 500s (budget 60s)", r.stdout)

    def test_report_without_a_timings_file_is_not_an_error(self):
        self.assertEqual(self.run_cli("report", "--timings", str(self.dir / "none.json")).returncode, 0)

    def test_broken_manifest_exits_2(self):
        self.manifest.write_text("paths: [")
        self.assertEqual(self.run_cli("select", "--changed", "all").returncode, 2)


class ShellGuardTest(unittest.TestCase):
    """deploy/compose/lib_acceptance.sh: want, the skip line, validation of PATHS, timings."""

    def sh(self, script, env=None):
        e = dict(os.environ)
        for k in ("PATHS", "EMBED", "TIMINGS"):
            e.pop(k, None)
        e.update(env or {})
        return subprocess.run(["bash", "-c", f'cd "{ROOT}"; source deploy/compose/lib_acceptance.sh; {script}'], capture_output=True, text=True, env=e)

    def test_unset_paths_wants_everything(self):
        r = self.sh("want A && want P3 && want C && echo yes")
        self.assertEqual(r.stdout.strip(), "yes", r.stderr)

    def test_selected_and_not_selected(self):
        r = self.sh("want A && echo a; want B || echo not-b; want P1 && echo p1", env={"PATHS": "A P1"})
        self.assertEqual(r.stdout.split(), ["a", "not-b", "p1"], r.stderr)

    def test_commas_work_too(self):
        r = self.sh("want B && echo b", env={"PATHS": "A,B"})
        self.assertEqual(r.stdout.strip(), "b", r.stderr)

    def test_no_prefix_confusion(self):
        r = self.sh("want J && echo j; want J0 && echo j0", env={"PATHS": "J0"})
        self.assertEqual(r.stdout.split(), ["j0"], r.stderr)

    def test_unknown_id_in_paths_stops_with_2(self):
        r = self.sh("echo unreachable", env={"PATHS": "A Q9"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("Q9", r.stderr)
        self.assertNotIn("unreachable", r.stdout)

    def test_skip_line_is_distinct_from_not_run(self):
        r = self.sh("path_skip B")
        self.assertEqual(r.stdout.strip(), "SKIPPED: PATH B (not selected)")

    def test_double_not_run_line(self):
        r = self.sh("path_needs_real P1b", env={"EMBED": "double"})
        self.assertEqual(r.stdout.strip(), "NOT_RUN: PATH P1b needs real embeddings (EMBED=double)")

    def test_embed_mode_is_validated(self):
        self.assertEqual(self.sh("echo $EMBED", env={"EMBED": "maybe"}).returncode, 2)
        self.assertEqual(self.sh("echo $EMBED").stdout.strip(), "double")
        self.assertEqual(self.sh("echo $EMBED", env={"EMBED": "real"}).stdout.strip(), "real")

    def test_timings_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            t = str(Path(d) / "t.json")
            r = self.sh("path_start A; path_end A; acceptance_report", env={"TIMINGS": t})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("A", json.loads(Path(t).read_text())["paths"])
            self.assertIn("total", r.stdout.lower())


class RealManifestTest(unittest.TestCase):
    """tests/paths.yaml against the scripts it describes."""

    @classmethod
    def setUpClass(cls):
        cls.m = acc.load_manifest(ROOT / "tests/paths.yaml")
        cls.e2e = (ROOT / "deploy/compose/e2e.sh").read_text(encoding="utf-8")
        cls.kind = (ROOT / "tests/kind/full.sh").read_text(encoding="utf-8")
        cls.scripts = cls.e2e + "".join(p.read_text(encoding="utf-8") for p in (ROOT / "deploy/compose").glob("path_*.sh"))

    def test_every_pass_id_is_printed_by_a_script(self):
        for p in self.m.paths:
            for pid in p.pass_ids:
                self.assertRegex(self.scripts, r'(?m)^\s*echo "PASS: PATH %s[ —-]' % re.escape(pid), f"{p.id}: {pid}")

    def test_every_pass_line_of_the_scripts_is_in_the_manifest(self):
        listed = {pid for p in self.m.paths for pid in p.pass_ids}
        for pid in re.findall(r'(?m)^\s*echo "PASS: PATH ([A-Za-z0-9]+)[ —-]', self.scripts):
            self.assertIn(pid, listed)

    def test_every_path_is_guarded_in_e2e(self):
        for p in self.m.paths:
            self.assertRegex(self.e2e, r"\bwant %s\b" % re.escape(p.id), p.id)

    def test_every_kind_path_is_guarded_in_the_kind_script(self):
        for p in self.m.paths:
            if p.kind:
                self.assertRegex(self.kind, r"\bwant %s\b" % re.escape(p.id), p.id)

    def test_the_workflow_has_no_hard_coded_list_any_more(self):
        wf = (ROOT / ".github/workflows/platform.yml").read_text(encoding="utf-8")
        self.assertNotIn("for p in A B C D E F G H I J0", wf)
        self.assertIn("tools/acceptance.py", wf)

    def test_real_embedding_ids_are_in_p1_only_and_p2_is_t3(self):
        self.assertEqual([p.id for p in self.m.paths if p.real_embedding], ["P1"])
        self.assertTrue(self.m.path("P2").t3)


if __name__ == "__main__":
    unittest.main()
