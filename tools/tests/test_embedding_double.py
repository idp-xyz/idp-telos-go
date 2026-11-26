"""Tests of deploy/images/embedding-double/server.py: the deterministic embedding double for acceptance (EMBED=double).

The wire format is what idp-cmi's OpenAI adapter calls (internal/embedding/embedding.go): POST {baseUrl}/embeddings with
{"model", "input": [texts]} and a Bearer key, answered {"data": [{"index", "embedding"}]}; the compose and kind health checks use GET /health.
"""
import importlib.util
import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("embedding_double", ROOT / "deploy/images/embedding-double/server.py")
ed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ed)


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


class VectorTest(unittest.TestCase):
    def test_dimension_is_what_cmi_expects(self):
        self.assertEqual(ed.DIMENSIONS, 1024)   # Qwen3-Embedding-0.6B; the HNSW index of vector-index 1024
        self.assertEqual(len(ed.embed("hello world")), 1024)

    def test_unit_length(self):
        for t in ["hello world", "报价单第一版", "a", "x " * 500, "Mixed 中文 and English 123"]:
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in ed.embed(t))), 1.0, places=5, msg=t)

    def test_deterministic_across_calls(self):
        self.assertEqual(ed.embed("the dock rota"), ed.embed("the dock rota"))

    def test_deterministic_across_processes(self):
        # Python's hash() is salted per process; the double must not use it
        import subprocess, sys
        code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('e',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "print(repr(m.embed('dock rota 报价')[:5]))")
        outs = {subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "deploy/images/embedding-double/server.py")], capture_output=True, text=True,
                               env={"PYTHONHASHSEED": str(i)}).stdout for i in (1, 2, 3)}
        self.assertEqual(len(outs), 1, outs)

    def test_identical_text_identical_vector_for_documents_and_queries(self):
        self.assertEqual(ed.embed("Parcels above 10000 USD need a manager"), ed.embed("Parcels above 10000 USD need a manager"))

    def test_case_and_punctuation_are_normalised(self):
        self.assertEqual(ed.embed("Dock, ROTA!"), ed.embed("dock rota"))

    def test_the_cmi_query_instruction_is_not_part_of_the_text(self):
        # idp-cmi prefixes every query with the instruction (embedding.queryPrefix); documents have none: the same words must match
        prefixed = "Instruct: Given a user question, retrieve the passages that answer it\nQuery: dock rota"
        self.assertEqual(ed.embed(prefixed), ed.embed("dock rota"))

    def test_shared_words_give_similarity_and_unrelated_text_does_not(self):
        doc = ed.embed("Returns policy. A customer may hand back an unwanted purchase within fourteen days of its delivery")
        near = ed.embed("unwanted purchase fourteen days")
        far = ed.embed("Customs desk. Parcels to other countries wait at the border until the duties are paid")
        self.assertGreater(cos(doc, near), 0.45)
        self.assertLess(cos(doc, far), 0.2)
        self.assertGreater(cos(doc, near), cos(doc, far) + 0.3)

    def test_one_unusual_word_shared_with_a_long_passage_reaches_the_cmi_floor(self):
        # idp-cmi drops a candidate that has a vector and a cosine under its floor (0.40 here) even when a word matched: a query that is one marker word must still reach it
        doc = ed.embed("Page one of the dock rota. The zorblatexxxxd marker sits here and the violet harbour lantern sparrowexxxxd is lit at dusk.")
        self.assertGreater(cos(doc, ed.embed("zorblatexxxxd")), 0.4)
        self.assertGreater(cos(doc, ed.embed("know-dig: zorblatexxxxd | violet harbour lantern sparrowexxxxd".replace("know-dig:", ""))), 0.4)

    def test_a_short_common_word_in_common_does_not(self):
        doc = ed.embed("Pickup counter. The hatch where parcels are collected opens at six in the morning and closes at noon on every working day")
        self.assertLess(cos(doc, ed.embed("hatch")), 0.4)
        self.assertLess(cos(doc, ed.embed("opens")), 0.4)
        self.assertLess(cos(doc, ed.embed("hatchlark")), 0.1)   # not in the document at all
        self.assertLess(cos(doc, ed.embed("When can I come to retrieve my deliveries?")), 0.4)

    def test_cjk_bigrams_give_similarity(self):
        a = ed.embed("我不想要了，收到后多少天之内可以退还")
        b = ed.embed("收到后多少天之内可以退还")
        c = ed.embed("海关清关需要缴纳关税")
        self.assertGreater(cos(a, b), 0.5)
        self.assertLess(cos(a, c), 0.2)

    def test_empty_and_symbol_only_text_still_gives_a_unit_vector(self):
        for t in ["", "   ", "!!!"]:
            v = ed.embed(t)
            self.assertEqual(len(v), 1024)
            self.assertAlmostEqual(sum(x * x for x in v), 1.0, places=5)

    def test_the_model_id_is_not_the_real_models(self):
        self.assertEqual(ed.MODEL_ID, "idp-embedding-double")
        self.assertNotIn("qwen", ed.MODEL_ID.lower())

    def test_stop_words_do_not_make_texts_similar(self):
        self.assertLess(cos(ed.embed("the of to in a"), ed.embed("the of to in a is")) , 1.0001)
        self.assertLess(cos(ed.embed("the dock"), ed.embed("the harbour")), 0.2)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ed.make_server("127.0.0.1", 0, api_key="secret-key", max_batch=4)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, method, path, body=None, key="secret-key", raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
        if key:
            r.headers["Authorization"] = "Bearer " + key
        try:
            with urllib.request.urlopen(r, timeout=10) as x:
                return x.status, x.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_health_needs_no_key(self):
        self.assertEqual(self.req("GET", "/health", key=None)[0], 200)

    def test_info_names_the_double(self):
        s, b = self.req("GET", "/info", key=None)
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b)["model_id"], "idp-embedding-double")

    def test_embeddings_wire_format(self):
        s, b = self.req("POST", "/v1/embeddings", {"model": "x", "input": ["dock rota", "customs desk"]})
        self.assertEqual(s, 200)
        d = json.loads(b)
        self.assertEqual([x["index"] for x in d["data"]], [0, 1])
        self.assertEqual(len(d["data"][0]["embedding"]), 1024)
        self.assertEqual(d["data"][0]["embedding"], ed.embed("dock rota"))
        self.assertEqual(d["model"], "idp-embedding-double")

    def test_a_string_input_is_one_text(self):
        s, b = self.req("POST", "/v1/embeddings", {"input": "dock rota"})
        self.assertEqual(s, 200)
        self.assertEqual(len(json.loads(b)["data"]), 1)

    def test_the_same_text_on_two_calls_is_the_same_vector(self):
        a = json.loads(self.req("POST", "/v1/embeddings", {"input": ["alpha beta"]})[1])["data"][0]["embedding"]
        b = json.loads(self.req("POST", "/v1/embeddings", {"input": ["alpha beta"]})[1])["data"][0]["embedding"]
        self.assertEqual(a, b)

    def test_wrong_or_missing_key_is_401(self):
        self.assertEqual(self.req("POST", "/v1/embeddings", {"input": ["a"]}, key="nope")[0], 401)
        self.assertEqual(self.req("POST", "/v1/embeddings", {"input": ["a"]}, key=None)[0], 401)

    def test_a_batch_over_the_limit_is_413(self):
        self.assertEqual(self.req("POST", "/v1/embeddings", {"input": ["a"] * 5})[0], 413)
        self.assertEqual(self.req("POST", "/v1/embeddings", {"input": ["a"] * 4})[0], 200)

    def test_bad_bodies_are_4xx_not_500(self):
        for body in [{"input": []}, {"input": [1, 2]}, {"nothing": 1}, {"input": {"a": 1}}]:
            self.assertIn(self.req("POST", "/v1/embeddings", body)[0], (400, 422), body)
        self.assertEqual(self.req("POST", "/v1/embeddings", raw=b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/v1/embeddings", raw=b"")[0], 400)

    def test_other_dimensions_are_refused(self):
        self.assertEqual(self.req("POST", "/v1/embeddings", {"input": ["a"], "dimensions": 256})[0], 400)
        self.assertEqual(self.req("POST", "/v1/embeddings", {"input": ["a"], "dimensions": 1024})[0], 200)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.req("GET", "/nope", key=None)[0], 404)

    def test_metrics_exist(self):
        s, b = self.req("GET", "/metrics", key=None)
        self.assertEqual(s, 200)
        self.assertIn(b"embedding_double_requests_total", b)

    def test_a_huge_body_is_refused(self):
        self.assertEqual(self.req("POST", "/v1/embeddings", raw=b'{"input": ["' + b"a" * (9 << 20) + b'"]}')[0], 413)


class KeyFileTest(unittest.TestCase):
    def test_key_is_read_from_a_file_and_stripped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k"
            p.write_text("abc\n")
            self.assertEqual(ed.read_key(str(p)), "abc")
            with self.assertRaises(SystemExit):
                ed.read_key(str(Path(d) / "missing"))
            p.write_text("")
            with self.assertRaises(SystemExit):
                ed.read_key(str(p))


if __name__ == "__main__":
    unittest.main()
