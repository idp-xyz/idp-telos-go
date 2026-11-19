"""A running idp-web for a test (ADR-0003 appendix C, task V1: conformance). The harness's checks, which qualified the
design with the Python reference (fetch.py, refs.py), can be run against the service that implements it: this module
starts the binary on free ports with throw-away files, speaks its MCP protocol, and keeps its log.

    with Service(entries=[...], test_origins=["127.0.0.1:8099"], tenant={"enabled": True, "openUrl": True}) as svc:
        reply = svc.call("web_open_url", {"url": "http://127.0.0.1:8099/a.html"})
        reply.text, reply.structured, reply.is_error, reply.audit

The harness sends the unsigned `io.idp.telos/caller` in `_meta`, as the gateway does when it is not set to sign: the
service is started without caller keys. The binary is idp-web/bin/idp-web (`make -C idp-web build`) unless
WEB_EVAL_IDP_WEB or `binary` says otherwise. Standard library only.
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BINARY = ROOT / "idp-web" / "bin" / "idp-web"
PROTOCOL = "2026-07-28"
TENANT = "acme"


def binary_path(binary=None) -> Path:
    return Path(binary or os.environ.get("WEB_EVAL_IDP_WEB") or DEFAULT_BINARY)


def available(binary=None) -> bool:
    p = binary_path(binary)
    return p.is_file() and os.access(p, os.X_OK)


class ServiceError(Exception):
    """The service could not be started, or answered with a protocol error (not a tool error: that is a Reply)."""


@dataclass
class Reply:
    text: str
    structured: dict
    is_error: bool
    audit: dict = field(default_factory=dict)   # the audit line the call produced (decision, reason, url, ...)
    seconds: float = 0.0


def caller(subject: str = "", tenant: str = TENANT) -> dict:
    return {"subject": subject or "eval-" + secrets.token_hex(4), "tenant": tenant, "roles": ["member"]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Service:
    def __init__(self, entries=None, default=None, test_origins=(), tenant=None, binary=None, extra_env=None, ready_timeout=20.0):
        """entries: fixture entries [{"match": str, "results": [{url, title, published, snippet}]}]; default: the results
        when no entry matches; test_origins: "host:port" the reader may contact although they are not public;
        tenant: the policy of the tenant the harness calls as (enabled, openUrl, includeDomains, excludeDomains);
        extra_env: more IDP_WEB_* variables (limits)."""
        self.binary = binary_path(binary)
        self.entries, self.default = list(entries or []), list(default or [])
        self.test_origins = list(test_origins)
        self.tenant = {"enabled": True, **(tenant or {})}
        self.extra_env = dict(extra_env or {})
        self.ready_timeout = ready_timeout
        self.proc = None
        self.dir = None
        self.lines: list = []          # every JSON log line of the process (standard output), in order
        self.raw: list = []            # lines that were not JSON
        self._lock = threading.Lock()
        self.mcp_port = self.ops_port = 0

    # ------------------------------------------------------------------ process

    def start(self):
        if not available(self.binary):
            raise ServiceError(f"no idp-web binary at {self.binary} (make -C idp-web build)")
        self.dir = Path(tempfile.mkdtemp(prefix="idp-web-eval-"))
        (self.dir / "ref.key").write_text(secrets.token_hex(32) + "\n")
        # JSON is YAML: the service reads these files with a YAML decoder, strictly
        (self.dir / "tenants.yaml").write_text(json.dumps({"default": {"enabled": False}, "tenants": {TENANT: self.tenant}}))
        (self.dir / "fixture.yaml").write_text(json.dumps({"entries": self.entries, "default": self.default}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("IDP_WEB_")}
        last = ""
        for _ in range(3):  # a port taken between choosing and listening: choose again
            self.mcp_port, self.ops_port = _free_port(), _free_port()
            env.update({
                "IDP_WEB_LISTEN": f"127.0.0.1:{self.mcp_port}", "IDP_WEB_METRICS": f"127.0.0.1:{self.ops_port}",
                "IDP_WEB_PROVIDER": "fixture", "IDP_WEB_FIXTURE_FILE": str(self.dir / "fixture.yaml"),
                "IDP_WEB_REF_KEY_FILE": str(self.dir / "ref.key"), "IDP_WEB_TENANTS_FILE": str(self.dir / "tenants.yaml"),
                "IDP_WEB_TEST_ORIGINS": ",".join(self.test_origins), **self.extra_env})
            self.err = open(self.dir / "stderr.log", "wb")
            self.proc = subprocess.Popen([str(self.binary)], env=env, stdout=subprocess.PIPE, stderr=self.err, cwd=str(self.dir))
            self.lines, self.raw = [], []
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
            last = self._wait_ready()
            if not last:
                return self
            self._kill()
            if "address already in use" not in last:
                break
        self.stop()
        raise ServiceError("idp-web did not become ready: " + last)

    def _pump(self, proc):
        for raw in proc.stdout:
            text = raw.decode("utf-8", "replace").strip()
            try:
                rec = json.loads(text)
            except json.JSONDecodeError:
                rec = None
            with self._lock:
                (self.lines if isinstance(rec, dict) else self.raw).append(rec if isinstance(rec, dict) else text)

    def _wait_ready(self) -> str:
        """"" when /readyz says ready, else what went wrong."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                time.sleep(0.2)
                return f"exited with status {self.proc.returncode}: {self._stderr()}"
            try:
                with opener.open(f"http://127.0.0.1:{self.ops_port}/readyz", timeout=2) as r:
                    if r.status == 200:
                        return ""
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.05)
        return "/readyz did not answer 200 in time: " + self._stderr()

    def _stderr(self) -> str:
        try:
            self.err.flush()
            return (self.dir / "stderr.log").read_text(errors="replace").strip()[-500:]
        except OSError:
            return ""

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def stop(self):
        self._kill()
        if self.proc and self.proc.stdout:
            self.proc.stdout.close()
        if getattr(self, "err", None):
            self.err.close()
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------------ protocol

    def call(self, tool: str, arguments: dict, who: dict | None = None, timeout: float = 60.0) -> Reply:
        """tools/call as the gateway sends it, with the unsigned caller in _meta."""
        who = who or caller()
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": tool, "arguments": arguments,
            "_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL, "io.modelcontextprotocol/clientCapabilities": {},
                      "io.idp.telos/caller": who}}}
        req = urllib.request.Request(f"http://127.0.0.1:{self.mcp_port}/mcp", json.dumps(body).encode(), {
            "Content-Type": "application/json", "Accept": "application/json", "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": "tools/call", "Mcp-Name": tool})
        with self._lock:
            before = len([l for l in self.lines if l.get("msg") == "audit"])
        start = time.monotonic()
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ServiceError(f"{tool}: HTTP {e.code} {e.read()[:300]!r}") from None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            raise ServiceError(f"{tool}: {e}") from None
        seconds = time.monotonic() - start
        if "error" in out:
            raise ServiceError(f"{tool}: {out['error']}")
        res = out["result"]
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return Reply(text, res.get("structuredContent") or {}, bool(res.get("isError")), self._audit_after(before, tool), seconds)

    def _audit_after(self, before: int, tool: str) -> dict:
        """The audit line the call wrote (the service logs it before it answers; the pipe may lag a moment)."""
        deadline = time.monotonic() + 3.0
        while True:
            with self._lock:
                lines = [l for l in self.lines if l.get("msg") == "audit"]
            if len(lines) > before or time.monotonic() > deadline:
                return lines[before] if len(lines) > before and lines[before].get("tool") == tool else {}
            time.sleep(0.01)

    def audits(self) -> list:
        with self._lock:
            return [l for l in self.lines if l.get("msg") == "audit"]

    def log(self) -> list:
        """All the JSON log lines so far."""
        with self._lock:
            return list(self.lines)
