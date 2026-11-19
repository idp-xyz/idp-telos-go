"""The running platform: Keycloak, the Workspace, the HOST's database and the images. This is the only code that needs the
platform, and none of it is exercised by the unit tests; the README lists what a first live run proves.

Everything a member does goes through the Workspace's public API with the member's own token, as deploy/compose/e2e.sh does
(user_token, the upload of PATH J, ask_agent, the run endpoints of PATH I). The HOST's tables are read with psql in the
platform's postgres container, as e2e.sh does. The model key is never read here: the platform reads it from its own file.
"""
import json
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DIR = ROOT / ".local" / "platform"
# compose's project name (deploy/compose/platform.yaml, "name:"); containers are found by its labels so that this tool does
# not have to repeat the profiles and variables that "docker compose" would need
PROJECT = "idp-telos-platform"
TERMINAL = {"COMPLETED", "FAILED", "CANCELED"}


class PlatformError(RuntimeError):
    pass


class Http:
    """A small client: no proxy (everything is on 127.0.0.1), the platform's own CA for Keycloak, JSON in and out."""

    def __init__(self, cafile=None, timeout=60):
        ctx = ssl.create_default_context(cafile=str(cafile)) if cafile else None
        handlers = [urllib.request.ProxyHandler({})] + ([urllib.request.HTTPSHandler(context=ctx)] if ctx else [])
        self.opener = urllib.request.build_opener(*handlers)
        self.timeout = timeout

    def request(self, method, url, token=None, body=None, content_type=None, headers=None, form=None):
        data = body
        h = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode()
            h["Content-Type"] = "application/json"
        elif content_type:
            h["Content-Type"] = content_type
        if token:
            h["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def json(self, method, url, want=(200,), **kw):
        status, raw = self.request(method, url, **kw)
        if status not in want:
            raise PlatformError(f"{method} {url} -> {status}: {raw[:300].decode('utf-8', 'replace')}")
        return json.loads(raw) if raw.strip() else {}


class Platform:
    def __init__(self, workspace="us-ops", user="bob", app="office", action="office.run", ws="http://127.0.0.1:8560",
                 host="http://127.0.0.1:8450", cmi="http://127.0.0.1:8550", issuer="https://127.0.0.1:7443/realms/idp",
                 platform_dir=PLATFORM_DIR):
        self.workspace, self.user, self.app, self.action = workspace, user, app, action
        self.ws, self.host, self.cmi, self.issuer = ws.rstrip("/"), host.rstrip("/"), cmi.rstrip("/"), issuer
        self.dir = Path(platform_dir)
        self.http = Http(self.dir / "tls" / "ca.crt")
        self.plain = Http()
        self._tok = (None, 0.0)

    # ---- is it up ----
    def check_up(self):
        """Raises PlatformError with what to do when the platform is not running."""
        down = []
        for name, url in (("workspace", self.ws + "/healthz"), ("host", self.host + "/healthz")):
            try:
                status, _ = self.plain.request("GET", url)
                if status != 200:
                    down.append(f"{name} {url} answered {status}")
            except (OSError, urllib.error.URLError) as e:
                down.append(f"{name} {url}: {getattr(e, 'reason', e)}")
        if down:
            raise PlatformError("the platform is not running (" + "; ".join(down) + "). This tool does not start it. Bring it up with: "
                                "MODEL=live KEEP=1 bash deploy/compose/e2e.sh")
        if not (self.dir / "clients.json").is_file():
            raise PlatformError(f"{self.dir / 'clients.json'} is missing: it is written by deploy/compose/e2e.sh and holds the test users' passwords")

    # ---- identity (e2e.sh: user_token, secret_of) ----
    def token(self) -> str:
        """The member's access token; a new one a minute before the old one expires, since a run can take 45 minutes."""
        tok, until = self._tok
        if tok and time.time() < until - 60:
            return tok
        c = json.loads((self.dir / "clients.json").read_text())
        secret = lambda k: c["users"].get(k) or c[k]
        out = self.http.json("POST", self.issuer + "/protocol/openid-connect/token", form={
            "grant_type": "password", "client_id": "e2e-cli", "client_secret": secret("e2e-cli"),
            "username": self.user, "password": secret(self.user)})
        self._tok = (out["access_token"], time.time() + float(out.get("expires_in", 60)))
        return out["access_token"]

    # ---- the member's actions in the Workspace ----
    def _w(self, path):
        return f"{self.ws}/v1/workspaces/{self.workspace}{path}"

    def upload(self, path) -> str:
        """Uploads a file as the member does (e2e.sh PATH J) and returns its id."""
        p = Path(path)
        out = self.http.json("POST", self._w("/files?name=" + urllib.parse.quote(p.name)), want=(201,), token=self.token(),
                             body=p.read_bytes(), content_type="application/octet-stream")
        return out["id"]

    def invoke(self, message, file_ids=(), thread_id=None) -> dict:
        """Asks the agent (e2e.sh ask_agent); returns {"run": id, "thread": id}. A new conversation is a thread id that this
        tool made up: the Workspace may carry a stored work context with a thread of an earlier conversation, and a
        thread id in the request wins over it."""
        inp = {"message": message}
        if file_ids:
            inp["files"] = list(file_ids)
        body = {"app": self.app, "action": self.action, "input": inp}
        if thread_id:
            body["threadId"] = thread_id
        out = self.http.json("POST", self._w("/actions/invoke"), want=(201,), token=self.token(), body=body)
        res = out["result"]
        return {"run": res["id"], "thread": res.get("threadId") or thread_id}

    def run_status(self, run_id) -> dict:
        return self.http.json("GET", self._w(f"/runs/{run_id}"), token=self.token())

    def pending_confirmations(self, run_id) -> list:
        out = self.http.json("GET", self._w(f"/runs/{run_id}/tool-calls"), token=self.token())
        return [{"id": c["toolCallId"], "tool": c["tool"], "arguments": (c.get("arguments") or "")[:300]}
                for c in out.get("toolCalls", []) if c.get("state") == "PENDING_CONFIRMATION"]

    def decide(self, run_id, call_id, decision="confirm"):
        return self.http.json("POST", self._w(f"/runs/{run_id}/tool-calls/{call_id}/decide"), token=self.token(), body={"decision": decision})

    def cancel(self, run_id):
        return self.http.request("POST", self._w(f"/runs/{run_id}/cancel"), token=self.token())

    def delivered_files(self, run_id) -> list:
        """What the run delivered to the member's files (e2e.sh PATH J: files?source=run:ID), downloaded: [{"name", "id",
        "listed_sha256", "data"}]."""
        out = self.http.json("GET", self._w("/files?source=" + urllib.parse.quote("run:" + run_id)), token=self.token())
        files = []
        for f in out.get("files", []):
            status, data = self.http.request("GET", self._w(f"/files/{f['id']}/content"), token=self.token())
            if status != 200:
                raise PlatformError(f"download of {f.get('name')} -> {status}")
            files.append({"name": f["name"], "id": f["id"], "listed_sha256": f.get("sha256"), "data": data})
        return files

    # ---- the HOST's database (e2e.sh: "${C[@]}" exec -T postgres psql -U postgres -d idp_host) ----
    def postgres_container(self) -> str:
        r = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
                            "--filter", "label=com.docker.compose.service=postgres"], capture_output=True, text=True, timeout=30)
        ids = r.stdout.split()
        if r.returncode or not ids:
            raise PlatformError(f"no running postgres container of compose project {PROJECT}: {r.stderr.strip()[:200]}")
        return ids[0]

    def psql(self, sql: str) -> str:
        return psql_in(self.postgres_container(), "idp_host", sql)

    # ---- what the platform is ----
    def meta(self, agent: str, model_url: str, window=None) -> dict:
        """Which model the HOST uses, its window, and the versions of the components; each part on its own, so that a part that
        cannot be found is recorded as such and does not stop the evaluation."""
        m = {"platform": {"workspace": self.ws, "host": self.host, "workspace_name": self.workspace, "app": self.app, "action": self.action}}
        try:
            row = self.psql("SELECT json_build_object('definition', a.definition, 'release', a.current_release_id, 'digest', "
                            "(SELECT digest FROM agent_releases WHERE id = a.current_release_id)) FROM agents a "
                            f"WHERE a.name = '{re.sub(r'[^a-z0-9-]', '', agent)}'").strip()
            a = json.loads(row)
            d = a["definition"]
            m["agent"] = {"name": agent, "model": d.get("model"), "limits": d.get("limits"), "skills": d.get("skills"),
                          "release_digest": a.get("digest")}
        except (PlatformError, ValueError, TypeError, KeyError) as e:
            m["agent"] = {"name": agent, "error": str(e)[:300]}
        found, why = discover_window(model_url, (m["agent"].get("model") or {}).get("model"), self.plain)
        m["window"] = {"value": window or found, "source": "--window" if window else ("provider" if found else "unknown"),
                       "provider_note": why}
        m["versions"] = self.versions()
        return m

    def versions(self) -> dict:
        v = {}
        for name, url in (("idp-host", self.host + "/version"), ("idp-workspace", self.ws + "/version"), ("idp-cmi", self.cmi + "/version")):
            try:
                status, raw = self.plain.request("GET", url)
                v.setdefault(name, {})["reported"] = raw.decode("utf-8", "replace").strip()[:200] if status == 200 else f"HTTP {status}"
            except (OSError, urllib.error.URLError) as e:
                v.setdefault(name, {})["reported"] = f"unreachable: {getattr(e, 'reason', e)}"
        # the images the containers run, and the checkouts they were built from
        try:
            r = subprocess.run(["docker", "ps", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format",
                                "{{.Label \"com.docker.compose.service\"}}|{{.Image}}|{{.ID}}"], capture_output=True, text=True, timeout=30)
            for line in r.stdout.splitlines():
                svc, image, cid = line.split("|")
                i = subprocess.run(["docker", "inspect", "--format", "{{.Image}} {{.Created}}", cid], capture_output=True, text=True, timeout=30)
                v.setdefault("containers", {})[svc] = {"image": image, "image_id_created": i.stdout.strip()}
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            v["containers"] = f"unavailable: {e}"
        for repo in ("idp-host", "idp-cmi", "idp-workspace", "idp-mcp", "idp-sandbox", "idp-web", "idp-eer"):
            v.setdefault(repo, {})["checkout"] = git_head(ROOT / repo)
        v["platform_repo"] = git_head(ROOT)
        try:  # the HOST prints its version when it starts, in case /version is not served
            r = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
                                "--filter", "label=com.docker.compose.service=host"], capture_output=True, text=True, timeout=30)
            if r.stdout.split():
                lg = subprocess.run(["docker", "logs", "--tail", "400", r.stdout.split()[0]], capture_output=True, text=True, timeout=60)
                hits = [l for l in (lg.stdout + lg.stderr).splitlines() if re.search(r"version", l, re.I)][:3]
                v["idp-host"]["log_lines"] = [h[:300] for h in hits]
        except (OSError, subprocess.SubprocessError):
            pass
        return v


def psql_in(container: str, db: str, sql: str) -> str:
    r = subprocess.run(["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", db, "--no-psqlrc", "-tA", "-v", "ON_ERROR_STOP=1"],
                       input=sql, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise PlatformError(f"psql in {container[:12]} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def git_head(repo: Path) -> str:
    try:
        h = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=15)
        d = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=15)
        return (h.stdout.strip() or "unknown") + ("-dirty" if d.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def window_from(doc, model=None):
    """A context window from a provider's answer, if it says one: llama.cpp (/props: default_generation_settings.n_ctx;
    /v1/models: data[].meta.n_ctx or n_ctx_train), vLLM (max_model_len), others (context_length, context_window)."""
    keys = ("n_ctx", "max_model_len", "context_length", "context_window", "max_context_length", "n_ctx_train")

    def walk(x):
        if isinstance(x, dict):
            for k in keys:  # in order of preference: the running context before the model's trained one
                if isinstance(x.get(k), int) and x[k] > 0:
                    return x[k]
            for v in x.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(x, list):
            for v in x:
                r = walk(v)
                if r:
                    return r
        return None

    if isinstance(doc, dict) and isinstance(doc.get("data"), list) and model:
        mine = [d for d in doc["data"] if isinstance(d, dict) and d.get("id") == model]
        if mine:
            return walk(mine[0])
    return walk(doc)


def discover_window(model_url: str, model, http=None):
    """(window or None, note). The tool never holds the model's key, so a provider that wants one cannot be asked: the note
    says so and --window gives the number."""
    http = http or Http()
    base = model_url.rstrip("/")
    notes = []
    for url in (base + "/models", re.sub(r"/v1$", "", base) + "/props"):
        try:
            status, raw = http.request("GET", url)
        except (OSError, urllib.error.URLError) as e:
            notes.append(f"{url}: {getattr(e, 'reason', e)}")
            continue
        if status != 200:
            notes.append(f"{url}: HTTP {status}" + (" (the provider wants a key; this tool does not hold it)" if status in (401, 403) else ""))
            continue
        try:
            w = window_from(json.loads(raw), model)
        except ValueError:
            w = None
        if w:
            return w, f"from {url}"
        notes.append(f"{url}: no window in the answer")
    return None, "; ".join(notes)
