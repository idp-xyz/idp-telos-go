#!/usr/bin/env python3
"""idp-embedding-double: a deterministic stand-in for the platform's embedding service, for the acceptance (EMBED=double). TESTS ONLY.

It answers what idp-cmi calls (internal/embedding/embedding.go): POST {baseUrl}/embeddings with {"model", "input": [texts]} and a Bearer key, answered
{"data": [{"index", "embedding"}], "model"}; the compose and kind health checks call GET /health. idp-cmi uses it as both instances (embedding and
embedding.query); the probe of D59 (the same text to both, cosine at least 0.999) passes because the vector of a text is a pure function of the text.

The vector: 1024 dimensions (what Qwen3-Embedding-0.6B gives and what `cmi-admin vector-index 1024` builds its HNSW indexes for), L2-normalised, made by
feature hashing: every normalised word (Unicode NFKC, lower case, no punctuation, no function words, as idp-cmi's own test double leaves them out) and every
CJK character bigram adds +-1 (the sign is a second hash bit) to the bucket its hash names, with weight (1 + ln(count)) * (length / 4)^2 (a longer word is
rarer, so it weighs more: the cheap stand-in for IDF that lets one unusual word shared with a long passage reach idp-cmi's cosine floor, as a real embedding
does and as a plain bag of words would not: one token in twenty is a cosine of 0.2, and the CMI drops a candidate with a vector under the floor even when a word
matched, D11). Words that two texts share therefore give similarity; texts that share none give about 0. Nothing is semantic: a paraphrase with no shared word is NOT found, which is what the
paths marked realEmbedding in tests/paths.yaml need the real service for. idp-cmi's query instruction ("Instruct: ...\nQuery: ") is dropped before the
text is embedded, so a question and a document that say the same words get the same vector. The model it reports is `idp-embedding-double`: the CMI
stores the model it is configured with beside every vector, so nothing made here can be taken for the real model's.
"""
import hashlib
import json
import math
import os
import re
import sys
import threading
import unicodedata
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "idp-embedding-double"
DIMENSIONS = 1024
MAX_BODY = 8 << 20
_CJK = "぀-ヿ㐀-䶿一-鿿가-힯豈-﫿"
_TOKEN = re.compile(r"([%s]+)|((?:(?![%s])[^\W_])+)" % (_CJK, _CJK))
_CJK_ONE = re.compile(r"[%s]" % _CJK)
_INSTRUCT = re.compile(r"\AInstruct:[^\n]*\nQuery: ")
_STOP = frozenset("a an the and or of to in on at for from by with is are was were be been it its this that these those i you we they he she my your our their his her as do does did have has had not no yes before after every each per only also".split())


def features(text):
    """The weighted features of a text: its words (weight by length) and its CJK character bigrams (weight 1, as a four-letter word)."""
    text = unicodedata.normalize("NFKC", _INSTRUCT.sub("", text, count=1)).lower()
    out = Counter()
    for cjk, word in _TOKEN.findall(text):
        if cjk:
            if len(cjk) == 1:
                out[cjk] += 1
            for i in range(len(cjk) - 1):
                out[cjk[i:i + 2]] += 1
        elif word not in _STOP:
            out[word] += 1
    return out


def weight(feature, n):
    """The weight of a feature that occurs n times: sub-linear in n, quadratic in the length of a word (bounded), 1 for a CJK character or bigram."""
    length = 4 if _CJK_ONE.match(feature) else min(len(feature), 24)
    return (1.0 + math.log(n)) * max(0.1, (length / 4.0) ** 2)


def embed(text):
    v = [0.0] * DIMENSIONS
    feats = features(text) or Counter({"\x00empty": 1})
    for f, n in feats.items():
        h = int.from_bytes(hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest(), "big")
        v[h % DIMENSIONS] += (1.0 if (h >> 63) & 1 else -1.0) * weight(f, n)
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:   # two features of opposite sign in one bucket cancelled: fall back to the empty marker
        return embed("\x00cancelled")
    return [round(x / norm, 7) for x in v]


def count_tokens(text):
    f = features(text)
    return sum(f.values())


def read_key(path):
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError as e:
        sys.exit(f"idp-embedding-double: cannot read the API key file {path}: {e}")
    if not key:
        sys.exit(f"idp-embedding-double: no API key in {path}")
    return key


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = Counter()

    def inc(self, outcome, n=1):
        with self.lock:
            self.counts[outcome] += n

    def text(self):
        with self.lock:
            lines = ["# HELP embedding_double_requests_total Requests by outcome.", "# TYPE embedding_double_requests_total counter"]
            lines += [f'embedding_double_requests_total{{outcome="{k}"}} {v}' for k, v in sorted(self.counts.items())]
        return ("\n".join(lines) + "\n").encode()


def make_server(host, port, api_key, max_batch=64, max_tokens=4096):
    metrics = Metrics()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "idp-embedding-double"

        def log_message(self, fmt, *args):   # no per-request log: a request carries texts
            pass

        def send(self, status, body, ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def err(self, status, message, kind):
            metrics.inc(kind)
            self.send(status, {"error": message, "error_type": kind})

        def authorised(self):
            return (self.headers.get("Authorization") or "") == "Bearer " + api_key

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return self.send(200, b"ok", "text/plain")
            if path == "/info":
                return self.send(200, {"model_id": MODEL_ID, "model_type": {"embedding": {"pooling": "double"}}, "max_client_batch_size": max_batch,
                                       "max_batch_tokens": max_tokens, "dimensions": DIMENSIONS, "version": "double"})
            if path == "/metrics":
                return self.send(200, metrics.text(), "text/plain; version=0.0.4")
            self.send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:   # drain (bounded) so that the client gets the answer and not a reset
                left = length
                while left > 0 and length <= 64 << 20:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                return self.err(413, "body too large", "too_large")
            raw = self.rfile.read(length) if length else b""
            if path not in ("/v1/embeddings", "/embeddings", "/embed"):
                return self.send(404, {"error": "not found"})
            if not self.authorised():
                return self.err(401, "invalid or missing API key", "unauthorised")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.err(400, "the body is not JSON", "bad_request")
            if not isinstance(body, dict):
                return self.err(400, "the body must be an object", "bad_request")
            texts = body.get("input", body.get("inputs"))
            if isinstance(texts, str):
                texts = [texts]
            if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
                return self.err(422, "input must be a string or a non-empty list of strings", "bad_request")
            dims = body.get("dimensions")
            if dims is not None and dims != DIMENSIONS:
                return self.err(400, f"this model has {DIMENSIONS} dimensions", "bad_request")
            if len(texts) > max_batch:
                return self.err(413, f"batch size {len(texts)} over the limit {max_batch}", "too_large")
            if any(count_tokens(t) > max_tokens for t in texts):
                return self.err(413, f"a text over {max_tokens} tokens", "too_large")
            vecs = [embed(t) for t in texts]
            metrics.inc("ok")
            if path == "/embed":
                return self.send(200, vecs)
            self.send(200, {"object": "list", "model": MODEL_ID, "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
                            "usage": {"prompt_tokens": sum(count_tokens(t) for t in texts), "total_tokens": sum(count_tokens(t) for t in texts)}})

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def main(argv):
    port = int(os.environ.get("IDP_EMBEDDING_PORT", "8700"))
    if "--healthcheck" in argv:   # for the compose and kind health checks: the image has no curl
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                return 0 if r.status == 200 else 1
        except OSError:
            return 1
    key = read_key(os.environ.get("EMBEDDING_API_KEY_FILE", "/run/secrets/embedding/api-key"))
    batch, tokens = int(os.environ.get("IDP_EMBEDDING_MAX_CLIENT_BATCH", "64")), int(os.environ.get("IDP_EMBEDDING_MAX_BATCH_TOKENS", "4096"))
    httpd = make_server("0.0.0.0", port, key, batch, tokens)
    print(f"idp-embedding-double: model {MODEL_ID}, {DIMENSIONS} dimensions, port {port}, up to {batch} texts of {tokens} tokens per request (TESTS ONLY)", file=sys.stderr, flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
