"""Create the GitHub App for R1 (the platform plan, section 5) with GitHub's App Manifest flow, so that the person needs one click.

  python3 -I tools/github_app_manifest.py [--org idp-xyz] [--dir .local/github-app] [--port 8562] [--replace]

It serves on 127.0.0.1:PORT (a browser on Windows reaches WSL's localhost). Open http://127.0.0.1:8562/start: the page posts the manifest to
https://github.com/organizations/<org>/settings/apps/new, where the person clicks "Create GitHub App". GitHub sends the browser back to
/done?code=...&state=...; the state must be the one this run made (it is used once), and the code is converted at
POST https://api.github.com/app-manifests/<code>/conversions (unauthenticated; the environment's proxy applies). The answer holds the App's
Client ID, client secret and private key: they are written to DIR/client-id, DIR/client-secret and DIR/private-key.pem (the directory 700, the
files 600) and printed nowhere; only the App's name, id, slug and html_url are. Existing files are not overwritten without --replace.
It exits 0 after one success, 1 after 10 minutes without one.

The App asks for nothing but "Metadata: read" (GitHub gives every App that), has its webhook off, is private to the organization, and has the two
callback addresses the acceptance uses (the Workspace's own callback page 8560 and the script's 8561: deploy/compose/github_live.sh). Its user
tokens expire (a GitHub App's default): access 8 hours, refresh 6 months, which the bridge refreshes.
"""
import argparse
import html
import hmac
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

FILES = ("client-id", "client-secret", "private-key.pem")
TIMEOUT = 600


def manifest(org_repo_url, port, name):
    return {
        "name": name,
        "url": org_repo_url,
        "redirect_url": f"http://127.0.0.1:{port}/done",
        "callback_urls": ["http://127.0.0.1:8561/oauth/callback", "http://127.0.0.1:8560/oauth/callback"],
        "public": False,
        "request_oauth_on_install": False,
        "setup_on_update": False,
        "hook_attributes": {"active": False, "url": "https://example.invalid/"},
        "default_permissions": {"metadata": "read"},
    }


class App:
    """What one run knows: its state (used once), its manifest, where it writes, and the outcome."""

    def __init__(self, org, directory, port, api, replace):
        self.org, self.dir, self.port, self.api, self.replace = org, directory, port, api, replace
        self.state = secrets.token_urlsafe(24)
        self.state_used = False
        self.name = "idp-telos-bridge-r1-" + secrets.token_hex(3)
        self.manifest = manifest("https://github.com/idp-xyz/idp-mcp-bridge", port, self.name)
        self.result = None   # (name, id, slug, html_url) after a success
        self.error = None

    def start_page(self):
        action = f"https://github.com/organizations/{urllib.parse.quote(self.org)}/settings/apps/new?state={urllib.parse.quote(self.state)}"
        return ("<!doctype html><meta charset=utf-8><title>Create the GitHub App</title>"
                f"<p>Sending you to GitHub to create the App <b>{html.escape(self.name)}</b> in {html.escape(self.org)}. Click <b>Create GitHub App</b> there.</p>"
                f'<form id=f method=post action="{html.escape(action)}"><input type=hidden name=manifest value="{html.escape(json.dumps(self.manifest))}">'
                "<noscript><button>Continue</button></noscript></form><script>document.getElementById('f').submit()</script>")

    def check_state(self, state):
        if self.state_used or not state or not hmac.compare_digest(state, self.state):
            return False
        self.state_used = True
        return True

    def convert(self, code):
        req = urllib.request.Request(f"{self.api}/app-manifests/{urllib.parse.quote(code, safe='')}/conversions", data=b"", method="POST",
                                     headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "idp-telos-github-app-manifest"})
        with urllib.request.urlopen(req, timeout=60) as resp:   # the environment's proxy applies, as on this machine it must
            return json.load(resp)

    def write(self, data):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        values = {"client-id": data["client_id"], "client-secret": data["client_secret"], "private-key.pem": data["pem"]}
        old = os.umask(0o077)
        try:
            for name, value in values.items():
                fd = os.open(os.path.join(self.dir, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(value)
                os.chmod(os.path.join(self.dir, name), 0o600)
        finally:
            os.umask(old)
        self.result = (data.get("name"), data.get("id"), data.get("slug"), data.get("html_url"))


def handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def page(self, status, body):
            b = ("<!doctype html><meta charset=utf-8>" + body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if u.path == "/start":
                return self.page(200, app.start_page())
            if u.path == "/done":
                if not app.check_state(q.get("state", "")) or not q.get("code"):
                    return self.page(400, "<p>This is not the page of a run that is waiting for it (its state does not match, or it was used). Start again at /start.</p>")
                try:
                    app.write(app.convert(q["code"]))
                except Exception as e:   # the message of an HTTP or file error holds no secret
                    app.error = f"{type(e).__name__}: {e}"
                    return self.page(502, "<p>GitHub did not give the App's credentials: see the terminal.</p>")
                return self.page(200, f"<p>Done: the GitHub App <b>{html.escape(str(app.result[0]))}</b> exists and its credentials are on this machine. You can close this tab.</p>")
            self.page(404, "<p>Open /start.</p>")
    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="idp-xyz")
    ap.add_argument("--dir", default=".local/github-app")
    ap.add_argument("--port", type=int, default=8562)
    ap.add_argument("--api", default=os.environ.get("GITHUB_API", "https://api.github.com"), help="tests only")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--timeout", type=float, default=TIMEOUT)
    args = ap.parse_args(argv)
    existing = [f for f in FILES if os.path.exists(os.path.join(args.dir, f))]
    if existing and not args.replace:
        print(f"REFUSED: {args.dir} already has {', '.join(existing)}; --replace overwrites them", file=sys.stderr)
        return 2
    app = App(args.org, args.dir, args.port, args.api, args.replace)
    server = HTTPServer(("127.0.0.1", args.port), handler(app))
    server.timeout = 1
    print(f"Open  http://127.0.0.1:{args.port}/start  in your browser (it sends you to GitHub, to create the App {app.name} in {args.org}).", flush=True)
    deadline = time.time() + args.timeout
    while time.time() < deadline and app.result is None and app.error is None:
        server.handle_request()
    server.server_close()
    if app.result:
        name, app_id, slug, url = app.result
        print(f"Created the GitHub App {name} (id {app_id}, slug {slug}): {url}")
        print(f"Its Client ID, client secret and private key are in {args.dir}/ (not shown)")
        return 0
    print("FAILED: " + (app.error or f"nothing came back within {int(args.timeout)} s"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
