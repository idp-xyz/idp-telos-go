"""The local test origin: the pages of E3 and E4 served from memory on 127.0.0.1, so that no test depends on the
internet. It records every request; /collect is the attacker's endpoint of E3 (it must never be reached)."""
import http.server
import threading
import time
import urllib.parse


class Origin:
    def __init__(self, pages: dict):
        """pages: {path: (content type, body bytes[, {header: value}])} or {path: ("redirect", location)} or
        {path: ("slow", seconds)}."""
        self.pages, self.hits = pages, []
        origin = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                origin.hits.append(self.path)
                kind, body, *extra = origin.pages.get(path, (None, None))
                if kind == "redirect":
                    self.send_response(302)
                    self.send_header("Location", body)
                    self.end_headers()
                    return
                if kind == "slow":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    for _ in range(int(body)):
                        time.sleep(1)
                        try:
                            self.wfile.write(b"<p>.</p>")
                            self.wfile.flush()
                        except OSError:
                            return
                    return
                if kind is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra[0] if extra else {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda request, client_address: None  # a reader that stops at its size limit resets the connection
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.allow = frozenset({f"127.0.0.1:{self.port}"})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def collected(self) -> list[str]:
        return [h for h in self.hits if h.startswith("/collect")]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
