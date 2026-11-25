"""Tests of deploy/compose/browser_check.py (PATH M6's reader of the products' records) and of the BOM entry for a component with two images."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "deploy/compose/browser_check.py"


def run(stdin, *args):
    p = subprocess.run([sys.executable, "-I", str(CHECK), *args], input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def lines(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\nnot json at all\n"


def egress(render, host, port, decision, reason):
    return {"msg": "egress", "render": render, "host": host, "port": port, "decision": decision, "reason": reason}


class BrowserCheckTest(unittest.TestCase):
    def test_audit_reads_the_last_line_of_the_url(self):
        log = lines({"msg": "audit", "tool": "web_fetch", "url": "http://o/js.html", "decision": "failed", "reason": "render_failed", "rendered": False, "render": "render_failed"},
                    {"msg": "audit", "tool": "web_fetch", "url": "http://o/js.html", "decision": "allowed", "reason": "ok", "rendered": True, "render": "ok", "renderMs": 800})
        code, out, err = run(log, "audit", "http://o/js.html", "decision=allowed", "reason=ok", "rendered=true", "render=ok")
        self.assertEqual(code, 0, err)
        self.assertIn("renderMs 800", out)
        code, _, err = run(log, "audit", "http://o/js.html", "render=blocked_by_site")
        self.assertEqual(code, 1)
        self.assertIn("FAIL: PATH M6", err)
        self.assertEqual(run(log, "audit", "http://o/other.html", "reason=ok")[0], 1)

    def test_no_audit_demands_plain_reads(self):
        plain = lines({"msg": "audit", "tool": "web_fetch", "url": "http://o/t.pdf", "rendered": False, "render": ""})
        self.assertEqual(run(plain, "no-audit", "http://o/t.pdf")[0], 0)
        rendered = lines({"msg": "audit", "tool": "web_fetch", "url": "http://o/t.pdf", "rendered": True, "render": "ok"})
        self.assertEqual(run(rendered, "no-audit", "http://o/t.pdf")[0], 1)
        self.assertEqual(run("", "no-audit", "http://o/t.pdf")[0], 1)

    def test_ssrf_wants_every_target_asked_and_denied(self):
        log = lines(egress("r1", "o", 80, "allow", "ok"), egress("r1", "169.254.169.254", 80, "deny", "not_public"), egress("r1", "keycloak", 7080, "deny", "port"))
        self.assertEqual(run(log, "ssrf", "169.254.169.254:80", "keycloak:7080")[0], 0)
        self.assertEqual(run(log, "ssrf", "169.254.169.254:80", "canary:80")[0], 1)   # never asked: the test would prove nothing
        leak = lines(egress("r1", "169.254.169.254", 80, "allow", "ok"))
        self.assertEqual(run(leak, "ssrf", "169.254.169.254:80")[0], 1)

    def test_storm_is_cut_at_the_budget(self):
        ok = lines(*[egress("r2", "o", 80, "allow", "ok") for _ in range(300)] + [egress("r2", "o", 80, "deny", "limit_requests")] * 5)
        self.assertEqual(run(ok, "storm", "300")[0], 0)
        over = lines(*[egress("r2", "o", 80, "allow", "ok") for _ in range(301)])
        self.assertEqual(run(over, "storm", "300")[0], 1)
        # the SSRF render has more lines (refusals) than the storm render has allowed requests: the storm is the render with the most allowed ones
        mixed = lines(*[egress("r2", "o", 80, "allow", "ok") for _ in range(36)] + [egress("r3", "x%d" % i, 80, "deny", "not_public") for i in range(65)] + [egress("r3", "o", 80, "allow", "ok")])
        self.assertEqual(run(mixed, "storm", "300")[0], 0)
        few = lines(*[egress("r2", "o", 80, "allow", "ok") for _ in range(12)])
        self.assertEqual(run(few, "storm", "300")[0], 0)   # a keep-alive connection carries many requests: the count at the proxy varies
        self.assertEqual(run("", "storm", "300")[0], 1)

    def test_canary_and_metric(self):
        self.assertEqual(run("log line\n", "canary")[0], 0)
        self.assertEqual(run('{"path": "/c1"}\n', "canary")[0], 1)
        metrics = 'idp_web_renders_total{outcome="ok"} 3\nidp_web_renders_total{outcome="render_failed"} 1\nother 9\n'
        self.assertEqual(run(metrics, "metric", "idp_web_renders_total", "outcome=ok")[1].strip(), "3")
        self.assertEqual(run("", "metric", "idp_web_renders_total", "outcome=ok")[1].strip(), "0")

    def test_render_lines_counts_finished_renders(self):
        code, out, _ = run(lines({"msg": "render", "requests": 12, "blocked": 0}, {"msg": "render", "requests": 3001, "blocked": 2701}), "render-lines")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"renders": 2, "most_requests": 3001, "blocked": 2701})
        log = lines({"msg": "render", "requests": 12, "blocked": 0}, {"msg": "render", "requests": 3001, "blocked": 2701})
        self.assertEqual(run(log, "render-lines", "3000", "2000")[0], 0)
        self.assertEqual(run(lines({"msg": "render", "requests": 12, "blocked": 0}), "render-lines", "3000", "2000")[0], 1)   # no storm happened


class BomImagesTest(unittest.TestCase):
    def test_a_component_with_two_images_lists_both(self):
        spec_path = ROOT / "tools/platform.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("telos_platform_bom", spec_path)
        tp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tp)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "catalog").mkdir()
            (root / "idp-contracts").mkdir()
            (root / "idp-contracts/registry.json").write_text('{"namespaces": {}}')
            (root / "catalog/components.yaml").write_text("components:\n  - {name: br, repository: x/br, platform: true, image: idp-browser, extra_images: [idp-browser-proxy], contracts: {produces: null, consumes: []}}\n")
            (root / "catalog/integrations.yaml").write_text("integrations: []\n")
            (root / "catalog/ownership.yaml").write_text("ownership: []\n")
            repo = root / "br"
            repo.mkdir()
            for a in (("init", "-q", "-b", "main"), ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i")):
                subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
            with mock.patch.object(tp.Platform, "image_digest", staticmethod(lambda ref: "sha256:" + ref.split(":")[0].replace("-", "")[:8])):
                bom = tp.Platform(root).bom()
        e = bom["components"][0]
        self.assertEqual(e["image"]["reference"], "idp-browser:dev")
        self.assertEqual([x["reference"] for x in e["extra_images"]], ["idp-browser-proxy:dev"])


if __name__ == "__main__":
    unittest.main()
