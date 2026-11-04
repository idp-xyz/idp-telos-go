"""Tests of tools/platform.py on a synthetic workspace (no real checkouts needed)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util

spec = importlib.util.spec_from_file_location("telos_platform", Path(__file__).resolve().parents[1] / "platform.py")
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git_repo(path, files):
    for rel, text in files.items():
        write(path / rel, text)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")


SCHEMA_A_100 = '{"$id": "urn:idp:telos:alpha:1.0.0"}\n'
SCHEMA_A_110 = '{"$id": "urn:idp:telos:alpha:1.1.0"}\n'


class PlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = Path(self.tmp.name)
        write(root / "idp-contracts/idp.telos.alpha/1.0.0/schema.json", SCHEMA_A_100)
        write(root / "idp-contracts/idp.telos.alpha/1.1.0/schema.json", SCHEMA_A_110)
        write(root / "idp-contracts/registry.json", json.dumps({"namespaces": {"idp.telos.alpha": {
            "id": "urn:idp:telos:alpha:1.1.0", "version": "1.1.0", "status": "PUBLISHED", "schema": "idp.telos.alpha/1.1.0/schema.json",
            "previous": [{"version": "1.0.0", "schema": "idp.telos.alpha/1.0.0/schema.json"}]}}}))
        write(root / "catalog/components.yaml", """components:
  - {name: alpha, repository: x/alpha, contracts: {produces: idp.telos.alpha, consumes: []}}
  - {name: beta, repository: x/beta, contracts: {produces: null, consumes: [idp.telos.alpha]}}
""")
        write(root / "catalog/integrations.yaml", """integrations:
  - {id: beta-alpha, from: beta, to: alpha, interface: GET /x, contract: idp.telos.alpha, auth: [static], evidence: [a test]}
""")
        write(root / "catalog/ownership.yaml", """ownership:
  - {data: "Alpha thing, Alpha other", owner: alpha}
  - {data: "Beta thing", owner: beta}
""")
        git_repo(root / "alpha", {"api/contracts/schema.json": SCHEMA_A_110})
        git_repo(root / "beta", {"tests/contracts/idp.telos.alpha/1.0.0/schema.json": SCHEMA_A_100, "node_modules/x/contracts/idp.telos.alpha/9.9.9/schema.json": "{}"})

    def tearDown(self):
        self.tmp.cleanup()

    def platform(self):
        return tp.Platform(self.root)

    def test_matrix_judges_pins(self):
        rows = self.platform().matrix()["rows"]
        self.assertEqual(len(rows), 1)  # node_modules ignored, alpha's own api/contracts is not a pin
        self.assertEqual(rows[0]["status"], "COMPATIBLE")
        self.assertIn("behind (1.0.0 -> 1.1.0", rows[0]["note"])

    def test_a_modified_copy_is_incompatible(self):
        write(self.root / "beta/tests/contracts/idp.telos.alpha/1.0.0/schema.json", '{"$id": "urn:idp:telos:alpha:1.0.0", "edited": true}\n')
        self.assertEqual(self.platform().matrix()["rows"][0]["status"], "INCOMPATIBLE")

    def test_a_pin_newer_than_registered_or_unknown_is_incompatible(self):
        write(self.root / "beta/tests/contracts/idp.telos.alpha/1.2.0/schema.json", "{}")
        notes = {r["pinned"]: r for r in self.platform().matrix()["rows"]}
        self.assertEqual(notes["1.2.0"]["status"], "INCOMPATIBLE")

    def test_unpinned_consumption_is_reported(self):
        (self.root / "beta/tests/contracts/idp.telos.alpha/1.0.0/schema.json").unlink()
        rows = self.platform().matrix()["rows"]
        self.assertEqual(rows[0]["status"], "UNPINNED")

    def test_check_passes_then_catches_drift(self):
        p = self.platform()
        bom, matrix = p.bom(docker=False), p.matrix()
        self.assertEqual(p.check(bom, matrix), ([], []))
        self.assertEqual(bom["components"][0]["contract"], {"namespace": "idp.telos.alpha", "version": "1.1.0"})
        # a component moved without regenerating the BOM
        write(self.root / "alpha/README.md", "x")
        subprocess.run(["git", "-C", str(self.root / "alpha"), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root / "alpha"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "move"], check=True)
        problems, _ = p.check(bom, matrix)
        self.assertTrue(any("BOM pins" in x for x in problems), problems)
        # a stale matrix, a second owner, an integration to nowhere
        stale = {"rule": matrix["rule"], "rows": []}
        write(self.root / "catalog/ownership.yaml", 'ownership:\n  - {data: "Alpha thing", owner: alpha}\n  - {data: "Alpha thing", owner: beta}\n')
        write(self.root / "catalog/integrations.yaml", "integrations:\n  - {id: x, from: beta, to: gamma, interface: y, contract: idp.telos.zeta, auth: [], evidence: []}\n")
        problems, _ = tp.Platform(self.root).check(tp.Platform(self.root).bom(docker=False), stale)
        text = "\n".join(problems)
        for want in ("out of date", "two owners", "gamma is not a catalogued", "idp.telos.zeta is not registered", "no evidence"):
            self.assertIn(want, text)

    def test_missing_checkout_is_not_run(self):
        p = self.platform()
        bom, matrix = p.bom(docker=False), p.matrix()
        import shutil
        shutil.rmtree(self.root / "beta")
        problems, not_run = tp.Platform(self.root).check(bom, matrix)
        self.assertEqual(problems, [])
        self.assertTrue(any("beta" in x and not x.endswith("(allowed)") for x in not_run), not_run)
        problems, not_run = tp.Platform(self.root).check(bom, matrix, allow_missing=["beta"])
        self.assertEqual(problems, [])
        self.assertTrue(not_run and all(x.endswith("(allowed)") for x in not_run), not_run)
        # the rows that can still be computed are still compared
        stale = {"rule": matrix["rule"], "rows": [r for r in matrix["rows"] if r["consumer"] != "beta"] + [{"consumer": "alpha", "status": "COMPATIBLE"}]}
        problems, _ = tp.Platform(self.root).check(bom, stale, allow_missing=["beta"])
        self.assertTrue(any("out of date" in x for x in problems), problems)


if __name__ == "__main__":
    unittest.main()
