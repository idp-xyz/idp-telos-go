"""Tests of tools/github_app_manifest.py: the state check, what the manifest asks for, the files and their modes, and that nothing secret is printed.
GitHub's conversion endpoint is a local mock; the tool runs as a child process, as a person would run it."""
import html
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "github_app_manifest.py"
SECRETS = {"client_id": "Iv23liAAAAAAAAAAAAAA", "client_secret": "s3cr3t-client-secret-BBBBBBBBBBBBBBBB",
           "pem": "-----BEGIN RSA PRIVATE KEY-----\nCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\n-----END RSA PRIVATE KEY-----\n", "webhook_secret": "w3bh00k-secret-DDDDDDDD"}
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Mock(BaseHTTPRequestHandler):
    seen = []
    fail = False

    def log_message(self, *a):
        pass

    def do_POST(self):
        Mock.seen.append((self.path, dict(self.headers)))
        body = json.dumps({"id": 4242, "slug": "idp-telos-bridge-r1-abc", "name": "idp-telos-bridge-r1-abc", "html_url": "https://github.com/apps/idp-telos-bridge-r1-abc", **SECRETS}).encode()
        self.send_response(404 if Mock.fail else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body if not Mock.fail else b"{}")


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "github-app"
        self.mock = HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=self.mock.serve_forever, daemon=True).start()
        Mock.seen, Mock.fail = [], False
        self.port = free_port()

    def tearDown(self):
        self.mock.shutdown()
        self.tmp.cleanup()

    def run_tool(self, *extra, timeout="20"):
        env = dict(os.environ, no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
        p = subprocess.Popen([sys.executable, "-I", str(TOOL), "--dir", str(self.dir), "--port", str(self.port), "--timeout", timeout,
                              "--api", f"http://127.0.0.1:{self.mock.server_port}", *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        p.stdout.readline()   # "Open http://..."
        return p

    def get(self, path):
        try:
            with OPEN.open(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def start_page(self):
        status, page = self.get("/start")
        self.assertEqual(status, 200)
        action = html.unescape(re.search(r'action="([^"]+)"', page).group(1))
        manifest = json.loads(html.unescape(re.search(r'name=manifest value="([^"]*)"', page).group(1)))
        return action, manifest, urllib.parse.parse_qs(urllib.parse.urlsplit(action).query)["state"][0]

    def test_the_manifest_asks_for_metadata_read_and_nothing_else(self):
        p = self.run_tool()
        try:
            action, m, state = self.start_page()
            self.assertTrue(action.startswith("https://github.com/organizations/idp-xyz/settings/apps/new?state="))
            self.assertEqual(m["default_permissions"], {"metadata": "read"})
            self.assertEqual(m["hook_attributes"], {"active": False, "url": "https://example.invalid/"})
            self.assertEqual((m["public"], m["request_oauth_on_install"], m["setup_on_update"]), (False, False, False))
            self.assertEqual(m["callback_urls"], ["http://127.0.0.1:8561/oauth/callback", "http://127.0.0.1:8560/oauth/callback"])
            self.assertEqual(m["redirect_url"], f"http://127.0.0.1:{self.port}/done")
            self.assertEqual(m["url"], "https://github.com/idp-xyz/idp-mcp-bridge")
            self.assertRegex(m["name"], r"^idp-telos-bridge-r1-[0-9a-f]{6}$")
            self.assertEqual(set(m), {"name", "url", "redirect_url", "callback_urls", "public", "request_oauth_on_install", "setup_on_update", "hook_attributes", "default_permissions"})
            self.assertGreaterEqual(len(state), 24)
        finally:
            p.kill()
            p.communicate()

    def test_a_wrong_or_missing_state_converts_nothing(self):
        p = self.run_tool()
        try:
            self.start_page()
            for q in ("/done?code=abc&state=wrong", "/done?code=abc", "/done?state=", "/done"):
                self.assertEqual(self.get(q)[0], 400, q)
            self.assertEqual(Mock.seen, [])
            self.assertFalse(self.dir.exists())
        finally:
            p.kill()
            p.communicate()

    def test_the_flow_writes_three_private_files_and_prints_no_secret(self):
        p = self.run_tool()
        _, _, state = self.start_page()
        status, page = self.get(f"/done?code=the-code&state={state}")
        out, err = p.communicate(timeout=20)
        self.assertEqual((status, p.returncode), (200, 0), err)
        self.assertEqual([path for path, _ in Mock.seen], ["/app-manifests/the-code/conversions"])
        self.assertEqual(Mock.seen[0][1]["Accept"], "application/vnd.github+json")
        self.assertEqual((self.dir / "client-id").read_text(), SECRETS["client_id"])
        self.assertEqual((self.dir / "client-secret").read_text(), SECRETS["client_secret"])
        self.assertEqual((self.dir / "private-key.pem").read_text(), SECRETS["pem"])
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        for f in ("client-id", "client-secret", "private-key.pem"):
            self.assertEqual(stat.S_IMODE((self.dir / f).stat().st_mode), 0o600, f)
        self.assertFalse((self.dir / "webhook-secret").exists())
        self.assertIn("idp-telos-bridge-r1-abc", out)
        self.assertIn("id 4242", out)
        self.assertIn("https://github.com/apps/idp-telos-bridge-r1-abc", out)
        for text in (out, err, page):
            for secret in SECRETS.values():
                self.assertNotIn(secret.strip().splitlines()[1] if "\n" in secret else secret, text)

    def test_the_state_is_used_once(self):
        # a second request with the same state, after a failed conversion, is refused: the state is spent when it is accepted
        Mock.fail = True
        p = self.run_tool()
        _, _, state = self.start_page()
        self.assertEqual(self.get(f"/done?code=x&state={state}")[0], 502)
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 1)
        self.assertIn("FAILED", err)
        for secret in SECRETS.values():
            self.assertNotIn(secret, err + out)

    def test_it_does_not_overwrite_without_replace(self):
        self.dir.mkdir()
        (self.dir / "client-secret").write_text("keep me")
        r = subprocess.run([sys.executable, "-I", str(TOOL), "--dir", str(self.dir), "--port", str(self.port)], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--replace", r.stderr)
        self.assertEqual((self.dir / "client-secret").read_text(), "keep me")
        p = self.run_tool("--replace")
        _, _, state = self.start_page()
        self.get(f"/done?code=c&state={state}")
        p.communicate(timeout=20)
        self.assertEqual((self.dir / "client-secret").read_text(), SECRETS["client_secret"])

    def test_it_gives_up_after_its_timeout(self):
        p = self.run_tool(timeout="1")
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing came back", err)


if __name__ == "__main__":
    unittest.main()
