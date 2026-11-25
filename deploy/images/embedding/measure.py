#!/usr/bin/env python3
"""Measure the embedding service the way idp-cmi uses it (knowledge-base plan KB2, section 10): chunk-sized texts (1500 bytes, what cmi.KnowledgeChunkBytes cuts), ingest requests of
BATCH texts, single queries with the instruction prefix. Standard library only; the service is any /v1/embeddings URL. Every run records `uptime` before and after: a number
taken while the host was busy with something else is not clean (the load average is in the output).

  python3 measure.py URL KEY --mode ingest|query|mixed [--batch 64] [--seconds 60] [--query-url URL2 --query-key KEY2] [--container NAME] [--text-from FILE...]

  ingest   only the bulk ingest runs for --seconds: chunks per second
  query    only queries, one every 1.5 s, for --seconds: latency alone
  mixed    the ingest runs in one thread and, in another, one query every 1.5 s (to --query-url when given: a separate instance): query latency during the bulk ingest and
           the ingest throughput that goes with it
With --container NAME (docker), the container's memory (docker stats) is sampled during the run.
"""
import argparse, json, statistics, subprocess, sys, threading, time, urllib.request

PREFIX = "Instruct: Given a user question, retrieve the passages that answer it\nQuery: "
QUESTIONS = ["how long do I have to return a parcel", "what is the surcharge for oversized items", "Who approves the travel budget?",
             "东西收到以后多久之内还能退", "运费怎么算", "where is the depot's pickup counter", "can a manager revoke a document", "what happens to my files when I leave"]


def post(url, key, inputs, timeout=600):
    req = urllib.request.Request(url.rstrip("/") + "/embeddings", json.dumps({"model": "qwen3-embedding-0.6b", "input": inputs}).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def chunks(files, n):
    raw = "\n".join(open(f, encoding="utf-8", errors="replace").read() for f in files).encode()
    out, i = [], 0
    while len(out) < n:
        out.append(raw[i % max(1, len(raw) - 1500):][:1500].decode("utf-8", "ignore") + f" [{len(out)}]")   # distinct texts
        i += 1337
    return out


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def uptime():
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def sample_rss(name, box, stop):
    while not stop.is_set():
        o = subprocess.run(["docker", "stats", "--no-stream", name, "--format", "{{.MemUsage}}"], capture_output=True, text=True).stdout.split("/")[0].strip()
        try:
            box.append(float(o[:-3]) * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024}[o[-3:]])
        except (ValueError, KeyError):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("key")
    ap.add_argument("--mode", choices=["ingest", "query", "mixed"], required=True)
    ap.add_argument("--batch", type=int, default=64); ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--query-url"); ap.add_argument("--query-key")
    ap.add_argument("--text-from", nargs="+", default=["README.md"]); ap.add_argument("--container")
    a = ap.parse_args()
    qurl, qkey = a.query_url or a.url, a.query_key or a.key
    texts = chunks(a.text_from, 1024)
    post(a.url, a.key, texts[:8]); post(qurl, qkey, [PREFIX + QUESTIONS[0]])    # warm-up
    res = {"mode": a.mode, "batch": a.batch, "seconds": a.seconds, "separateQueryInstance": bool(a.query_url), "uptimeBefore": uptime()}
    stop, box = threading.Event(), []
    if a.container:
        threading.Thread(target=sample_rss, args=(a.container, box, stop), daemon=True).start()
    done = {"n": 0}

    def ingest():
        i = 0
        while not stop.is_set():
            batch = [texts[(i + k) % len(texts)] + f" #{i}" for k in range(a.batch)]
            post(a.url, a.key, batch)
            done["n"] += len(batch); i += a.batch

    lat = []

    def queries():
        i = 0
        while not stop.is_set():
            t0 = time.time()
            post(qurl, qkey, [PREFIX + QUESTIONS[i % len(QUESTIONS)]])
            lat.append((time.time() - t0) * 1000); i += 1
            time.sleep(1.5)

    threads = []
    if a.mode in ("ingest", "mixed"):
        threads.append(threading.Thread(target=ingest))
    if a.mode in ("query", "mixed"):
        threads.append(threading.Thread(target=queries))
    t0 = time.time()
    for t in threads:
        t.start()
    time.sleep(a.seconds)
    stop.set()
    for t in threads:
        t.join(timeout=300)
    took = time.time() - t0
    if a.mode != "query":
        res["ingestChunksPerSecond"] = round(done["n"] / took, 2); res["ingestedChunks"] = done["n"]
    if lat:
        res["queries"] = len(lat); res["queryMs"] = {"p50": round(pct(lat, 50)), "p95": round(pct(lat, 95)), "max": round(max(lat))}
    if box:
        res["containerMemMiB"] = {"median": round(statistics.median(box)), "max": round(max(box))}
    res["uptimeAfter"] = uptime()
    print(json.dumps(res))


if __name__ == "__main__":
    sys.exit(main())
