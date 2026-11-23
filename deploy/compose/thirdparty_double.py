"""TEST ONLY: a third party for PATH O (deploy/compose/platform.yaml, profile bridge-test; tests/kind/thirdparty.yaml): an OAuth
authorization server and a protected MCP 2026-07-28 server in one process, which records every request it gets.

What it is, as far as the bridge (idp-mcp-bridge) can tell:
  protected resource   POST /mcp and POST /pre/mcp: tools/list AND tools/call need `Authorization: Bearer <a token it issued for that
                       resource>`; without one it answers 401 with WWW-Authenticate: Bearer resource_metadata="..." (RFC 9728, RFC 6750).
                       One tool, echo (readOnlyHint), which answers "echo from <the user the token was issued to>: <text>".
  metadata             /.well-known/oauth-protected-resource/<path> (RFC 9728) and /.well-known/oauth-authorization-server (RFC 8414):
                       client_id_metadata_document_supported, authorization_response_iss_parameter_supported, S256, a revocation endpoint
  authorization        GET /authorize: consents by itself for the user the `X-Test-User` header names (a real server logs them in), and
                       redirects back with code, state and iss. The client is a client metadata document (client_id is an address; the
                       double FETCHES it, as a server that supports them does, and checks that it names the redirect address and says
                       token_endpoint_auth_method none) or the preregistered client (THIRDPARTY_PREREG_ID, THIRDPARTY_PREREG_SECRET)
  token                POST /token: authorization_code (PKCE S256, redirect_uri, resource, one use) and refresh_token (rotating)
  revocation           POST /revoke (RFC 7009)

Test controls (the acceptance only; /_test/*):
  GET  /_test/config           the switches
  POST /_test/config           {switch: value, ...}: wrong_iss "<iss>" (a wrong `iss` in the authorization response), require_scope "<scope>"
                               (tools/call needs it: a token without it is answered 403 insufficient_scope), expires_in <seconds> (of the
                               access tokens issued from now on; 3600), rotate (refresh tokens rotate; true), inject "<text>" (appended to
                               the echo result: text that tries to instruct the model), auth_error "<code>" (the authorization request is
                               answered with that error). null puts a switch back
  POST /_test/expire           every access token issued so far is expired
  POST /_test/revoke-all       every token issued so far is revoked
  GET  /_test/requests?since=N the requests received after number N: [{n, method, path, query, headers, body, status}] (headers and bodies, as received)
  GET  /_test/secrets          every access token, refresh token, authorization code and client secret ever issued, for the check that none
                               of them is anywhere it must not be
  GET  /_test/tokens           [{kind, user, resource, scope, revoked, expired, value}] (the acceptance reads whose token is whose)

Its own log has one line a request (method, path without the query, status) and never a header, a body or a token. Standard library
only. THIRDPARTY_BASE is the address everyone else reaches it at (the issuer, the resource); THIRDPARTY_CIMD_MAP "from=to[,from=to]"
rewrites the address of a client metadata document before it is fetched (the document's address is the Workspace's public one,
http://127.0.0.1:8560 as a browser sees it, and from here that is `http://workspace:8560`).

    python3 -I thirdparty_double.py [PORT]
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8590
BASE = os.environ.get('THIRDPARTY_BASE', f'http://127.0.0.1:{PORT}').rstrip('/')
CIMD_MAP = [tuple(p.split('=', 1)) for p in os.environ.get('THIRDPARTY_CIMD_MAP', '').split(',') if '=' in p]
PREREG_ID = os.environ.get('THIRDPARTY_PREREG_ID', '')
PREREG_SECRET = os.environ.get('THIRDPARTY_PREREG_SECRET', '')
PREREG_REDIRECT = os.environ.get('THIRDPARTY_PREREG_REDIRECT', '')
RESOURCES = {'/mcp': BASE + '/mcp', '/pre/mcp': BASE + '/pre/mcp'}
BASE_SCOPE = 'echo.read'

lock = threading.Lock()
requests_log = []          # every request, as received
tokens = {}                # value -> {kind, user, client, resource, scope, expires, revoked}
codes = {}                 # value -> {client, redirect, challenge, resource, scope, user, used}
config = {'expires_in': 3600, 'rotate': True}
issued_secrets = []        # everything the double ever issued that must stay secret
noclient = object()


def rnd(prefix):
    v = prefix + secrets.token_hex(12)
    issued_secrets.append(v)
    return v


def fetch_client_document(client_id):
    url = client_id
    for a, b in CIMD_MAP:
        if url.startswith(a):
            url = b + url[len(a):]
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=10) as r:
            if r.status != 200:
                return None
            return json.loads(r.read(65536))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def client_known(client_id, redirect):
    """The client of an authorization request: the preregistered one, or a client metadata document that names this redirect."""
    if PREREG_ID and client_id == PREREG_ID:
        return redirect == PREREG_REDIRECT   # what the operator registered at the server
    if not client_id.startswith('http'):
        return False
    doc = fetch_client_document(client_id)
    return bool(doc) and doc.get('client_id') == client_id and doc.get('token_endpoint_auth_method') == 'none' and redirect in (doc.get('redirect_uris') or [])


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    # ---- plumbing ----
    def send(self, status, body=b'', ctype='application/json', headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        rec = getattr(self, 'rec', None)
        if rec is not None:
            with lock:
                rec['status'] = status
        self.send_response(status)
        if body or status not in (204, 302):
            self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        print(f'{self.command} {self.path.split("?", 1)[0]} {status}', flush=True)

    def record(self, body):
        u = urllib.parse.urlsplit(self.path)
        with lock:
            n = len(requests_log) + 1
            self.rec = {'n': n, 'method': self.command, 'path': u.path, 'query': u.query,
                        'headers': {k: v for k, v in self.headers.items()}, 'body': body.decode('utf-8', 'replace'), 'status': 0}
            requests_log.append(self.rec)

    def handle_any(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        u = urllib.parse.urlsplit(self.path)
        if u.path.startswith('/_test/'):
            return self.control(u, body)
        self.record(body)
        path = u.path
        if path.startswith('/.well-known/oauth-protected-resource'):
            return self.prm(path[len('/.well-known/oauth-protected-resource'):] or '/mcp')
        if path == '/.well-known/oauth-authorization-server':
            return self.as_metadata()
        if path == '/authorize' and self.command == 'GET':
            return self.authorize(urllib.parse.parse_qs(u.query))
        if path == '/token' and self.command == 'POST':
            return self.token(urllib.parse.parse_qs(body.decode()))
        if path == '/revoke' and self.command == 'POST':
            return self.revoke(urllib.parse.parse_qs(body.decode()))
        if path in RESOURCES and self.command == 'POST':
            return self.mcp(path, body)
        self.send(404, {'error': 'not_found'})

    do_GET = do_POST = do_DELETE = do_PUT = handle_any

    # ---- metadata ----
    def prm(self, path):
        if path not in RESOURCES:
            return self.send(404, {'error': 'not_found'})
        self.send(200, {'resource': RESOURCES[path], 'authorization_servers': [BASE], 'bearer_methods_supported': ['header'],
                        'scopes_supported': [BASE_SCOPE, 'echo.write']})

    def as_metadata(self):
        self.send(200, {'issuer': BASE, 'authorization_endpoint': BASE + '/authorize', 'token_endpoint': BASE + '/token',
                        'revocation_endpoint': BASE + '/revoke', 'response_types_supported': ['code'],
                        'grant_types_supported': ['authorization_code', 'refresh_token'], 'code_challenge_methods_supported': ['S256'],
                        'token_endpoint_auth_methods_supported': ['none', 'client_secret_basic'], 'scopes_supported': [BASE_SCOPE, 'echo.write'],
                        'client_id_metadata_document_supported': True, 'authorization_response_iss_parameter_supported': True})

    # ---- authorization ----
    def authorize(self, q):
        one = lambda k: (q.get(k) or [''])[0]
        redirect = one('redirect_uri')
        user = self.headers.get('X-Test-User', '')
        if (one('response_type') != 'code' or one('code_challenge_method') != 'S256' or not one('code_challenge') or not one('state')
                or not user or not client_known(one('client_id'), redirect) or not one('resource')):
            return self.send(400, 'bad authorization request', 'text/plain')
        back = urllib.parse.urlsplit(redirect)
        out = urllib.parse.parse_qsl(back.query)
        out.append(('state', one('state')))
        with lock:
            cfg = dict(config)
        if cfg.get('auth_error'):
            out.append(('error', cfg['auth_error']))
        else:
            code = rnd('tpcode_')
            with lock:
                codes[code] = {'client': one('client_id'), 'redirect': redirect, 'challenge': one('code_challenge'), 'resource': one('resource'),
                               'scope': one('scope'), 'user': user, 'used': False}
            out.append(('code', code))
        out.append(('iss', cfg.get('wrong_iss') or BASE))
        loc = urllib.parse.urlunsplit(back._replace(query=urllib.parse.urlencode(out)))
        self.send(302, b'', headers={'Location': loc})

    def client_of(self, form):
        """Who is authenticating at the token or revocation endpoint: the preregistered client with its secret (Basic, as the bridge does
        it), or a client metadata document client, which is public."""
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Basic '):
            try:
                cid, _, sec = base64.b64decode(auth[6:]).decode().partition(':')
            except ValueError:
                return noclient
            cid, sec = urllib.parse.unquote_plus(cid), urllib.parse.unquote_plus(sec)
            return cid if PREREG_ID and cid == PREREG_ID and secrets.compare_digest(sec, PREREG_SECRET) else noclient
        cid = (form.get('client_id') or [''])[0]
        return cid if cid.startswith('http') and not form.get('client_secret') else noclient

    def issue(self, user, client, resource, scope, cfg):
        at = rnd('tpat_')
        with lock:
            tokens[at] = {'kind': 'access', 'user': user, 'client': client, 'resource': resource, 'scope': scope,
                          'expires': time.time() + float(cfg.get('expires_in') or 3600), 'revoked': False}
        return at

    def token(self, form):
        one = lambda k: (form.get(k) or [''])[0]
        client = self.client_of(form)
        if client is noclient:
            return self.send(401, {'error': 'invalid_client'})
        with lock:
            cfg = dict(config)
        if one('grant_type') == 'authorization_code':
            if not one('resource'):
                return self.send(400, {'error': 'invalid_target'})
            with lock:
                c = codes.get(one('code'))
                if c:
                    was_used, c['used'] = c['used'], True
            sha = base64.urlsafe_b64encode(hashlib.sha256(one('code_verifier').encode()).digest()).rstrip(b'=').decode()
            if (not c or was_used or c['client'] != client or c['redirect'] != one('redirect_uri') or c['challenge'] != sha
                    or c['resource'] != one('resource')):
                return self.send(400, {'error': 'invalid_grant'})
            user, scope, resource = c['user'], c['scope'], c['resource']
            rt_value = None
        elif one('grant_type') == 'refresh_token':
            with lock:
                rt = tokens.get(one('refresh_token'))
            if not rt or rt['kind'] != 'refresh' or rt['revoked'] or rt['client'] != client or (one('resource') and rt['resource'] != one('resource')):
                return self.send(400, {'error': 'invalid_grant'})
            user, scope, resource = rt['user'], rt['scope'], rt['resource']
            rt_value = one('refresh_token')
            if cfg.get('rotate', True):
                with lock:
                    rt['revoked'] = True
                rt_value = None
        else:
            return self.send(400, {'error': 'unsupported_grant_type'})
        at = self.issue(user, client, resource, scope, cfg)
        if rt_value is None:
            rt_value = rnd('tprt_')
            with lock:
                tokens[rt_value] = {'kind': 'refresh', 'user': user, 'client': client, 'resource': resource, 'scope': scope, 'expires': None, 'revoked': False}
        self.send(200, {'access_token': at, 'token_type': 'Bearer', 'expires_in': int(cfg.get('expires_in') or 3600), 'refresh_token': rt_value, 'scope': scope})

    def revoke(self, form):
        if self.client_of(form) is noclient:
            return self.send(401, {'error': 'invalid_client'})
        with lock:
            t = tokens.get((form.get('token') or [''])[0])
            if t:
                t['revoked'] = True
        self.send(200, b'')

    # ---- the protected resource ----
    def unauthorized(self, path, error='', status=401):
        ch = f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource{path}"'
        if error:
            ch += f', error="{error}"'
        self.send(status, b'', headers={'WWW-Authenticate': ch})

    def mcp(self, path, body):
        auth = self.headers.get('Authorization', '')
        tok = auth[7:] if auth.startswith('Bearer ') else ''
        with lock:
            t = tokens.get(tok)
            ok = bool(t) and t['kind'] == 'access' and not t['revoked'] and t['expires'] > time.time() and t['resource'] == RESOURCES[path]
            cfg = dict(config)
        if not ok:
            return self.unauthorized(path, 'invalid_token' if tok else '')
        try:
            req = json.loads(body)
        except ValueError:
            return self.send(400, {'error': 'bad_request'})
        rid, method, params = req.get('id'), req.get('method'), req.get('params') or {}

        def reply(result):
            self.send(200, {'jsonrpc': '2.0', 'id': rid, 'result': result})
        if method == 'tools/list':
            return reply({'resultType': 'complete', 'tools': [{
                'name': 'echo', 'description': 'Returns the text and whose token it came with', 'annotations': {'readOnlyHint': True},
                'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}}}]})
        if method == 'tools/call':
            if params.get('name') != 'echo':
                return self.send(200, {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32602, 'message': 'Unknown tool'}})
            need = cfg.get('require_scope')
            if need and need not in (t['scope'] or '').split():
                return self.send(403, b'', headers={'WWW-Authenticate': f'Bearer error="insufficient_scope", scope="{need}", resource_metadata="{BASE}/.well-known/oauth-protected-resource{path}"'})
            text = f"echo from {t['user']}: {(params.get('arguments') or {}).get('text', '')}"
            if cfg.get('inject'):
                text += ' ' + cfg['inject']
            return reply({'resultType': 'complete', 'content': [{'type': 'text', 'text': text}]})
        self.send(404, {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': 'Method not found'}})

    # ---- test controls ----
    def control(self, u, body):
        path, q = u.path, urllib.parse.parse_qs(u.query)
        if path == '/_test/config':
            if self.command == 'POST':
                with lock:
                    for k, v in json.loads(body or b'{}').items():
                        if v is None:
                            config.pop(k, None)
                        else:
                            config[k] = v
            with lock:
                return self.send(200, dict(config))
        if path == '/_test/expire' and self.command == 'POST':
            with lock:
                for t in tokens.values():
                    if t['kind'] == 'access':
                        t['expires'] = time.time() - 1
            return self.send(200, {'ok': True})
        if path == '/_test/revoke-all' and self.command == 'POST':
            with lock:
                for t in tokens.values():
                    t['revoked'] = True
            return self.send(200, {'ok': True})
        if path == '/_test/requests':
            since = int((q.get('since') or ['0'])[0])
            with lock:
                return self.send(200, [r for r in requests_log if r['n'] > since])
        if path == '/_test/secrets':
            with lock:
                return self.send(200, sorted(set(issued_secrets + ([PREREG_SECRET] if PREREG_SECRET else []))))
        if path == '/_test/tokens':
            with lock:
                now = time.time()
                return self.send(200, [{'kind': t['kind'], 'user': t['user'], 'resource': t['resource'], 'scope': t['scope'], 'revoked': t['revoked'],
                                        'expired': bool(t['expires']) and t['expires'] <= now, 'value': v} for v, t in tokens.items()])
        self.send(404, {'error': 'not_found'})


socketserver.ThreadingTCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer.daemon_threads = True
print(f'thirdparty double listening on :{PORT} as {BASE}', flush=True)
socketserver.ThreadingTCPServer(('0.0.0.0', PORT), H).serve_forever()
