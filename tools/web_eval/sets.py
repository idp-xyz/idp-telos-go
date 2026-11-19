"""The question sets of ADR-0003 section 7.1.

E1  public benchmarks: a fixed sample of SimpleQA (short facts) and BrowseComp (hard multi-hop browsing), downloaded
    from OpenAI's simple-evals bucket (MIT) into .local/web-eval/cache. BrowseComp is published encrypted so that it
    stays out of training data: it is decrypted in memory only, and neither set is written into this repository.
E2  the TELOS set: tools/web_eval/data/e2.jsonl, questions an operations, compliance, engineering or finance team
    would ask, each with its time anchor, gold answer, acceptable forms and the sources that state it.
Every item: {id, set, question, answer, aliases, lang, category, ...}.
"""
import base64
import csv
import hashlib
import io
import json
import random
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = ROOT / ".local" / "web-eval" / "cache"
SIMPLEQA = "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv"
BROWSECOMP = "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
SAMPLE_SEED = 20261118


def _download(url: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[1]
    if not path.exists():
        with urllib.request.urlopen(url, timeout=120) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8")


def _decrypt(cipher_b64: str, password: str) -> str:
    """simple-evals' scheme: XOR with SHA-256(password) repeated."""
    data = base64.b64decode(cipher_b64)
    key = hashlib.sha256(password.encode()).digest()
    key = key * (len(data) // len(key)) + key[: len(data) % len(key)]
    return bytes(a ^ b for a, b in zip(data, key)).decode()


def e1(simpleqa: int = 200, browsecomp: int = 100) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(_download(SIMPLEQA))))
    rng = random.Random(SAMPLE_SEED)
    picked = sorted(rng.sample(range(len(rows)), min(simpleqa, len(rows))))
    items = [{"id": f"e1-sqa-{i:04d}", "set": "e1", "category": "simpleqa", "lang": "en", "question": rows[i]["problem"],
              "answer": rows[i]["answer"], "aliases": []} for i in picked]
    rows = list(csv.DictReader(io.StringIO(_download(BROWSECOMP))))
    picked = sorted(rng.sample(range(len(rows)), min(browsecomp, len(rows))))
    for i in picked:
        r = rows[i]
        items.append({"id": f"e1-bc-{i:04d}", "set": "e1", "category": "browsecomp", "lang": "en",
                      "question": _decrypt(r["problem"], r["canary"]), "answer": _decrypt(r["answer"], r["canary"]), "aliases": []})
    return items


def e2(path: Path = HERE / "data" / "e2.jsonl") -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            d["set"] = "e2"
            items.append(d)
    return items
