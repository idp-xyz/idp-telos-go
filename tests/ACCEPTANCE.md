# Platform acceptance in tiers

The acceptance used to be one thing: every batch ran all of `deploy/compose/e2e.sh` (paths A to O, M6, P0 to P3, C: about 28 minutes, 17 of them P2g waiting for the CPU
embedding of the real `idp-embedding` image to drain) and then all of `tests/kind/full.sh` (an hour or more, peaking near 10.8 GiB). Most batches touch one component and none
of the manifests. So the acceptance is now four tiers and kind, and you run the one the change calls for.

| Tier | When | What | Target |
|---|---|---|---|
| T0 components | every component commit | unit, integration and contract tests, in each component repository | minutes |
| T1 feature check | every feature batch (local, and pull-request CI) | only the paths the change affects, embedding double | at most 15 min |
| T2 pre-BOM | before a BOM; CI on main when the BOM or an acceptance file changed | every path, embedding double | at most 20 min |
| T3 real embeddings | CI on BOM changes and nightly (`acceptance-real-embeddings.yml`) | the paths that need semantic similarity, plus P2, with the real `idp-embedding` image | not blocking local work |
| Kind | when manifests, network policies, the sandbox runtime or the kind scripts change; nightly | `tests/kind/full.sh` (with `EMBED=double`) | not on every push |

`tests/paths.yaml` is the list of paths: each path's PASS ids, components, `needs`, budget and which of its steps need real embeddings. `tools/acceptance.py` reads it. The
scripts take two switches: `PATHS` (which paths) and `EMBED` (`double` by default, or `real`). Setup always runs; a path that is not selected prints
`SKIPPED: PATH X (not selected)` (not `NOT_RUN`, which says a path could not run here: no `uv`, `MODEL=live`, or `EMBED=double` for a step that needs real embeddings).

## What to run

Before a feature push (T1). Name what you changed, or let the tool read the diff:

```bash
PATHS="$(python3 -I tools/acceptance.py select --changed idp-cmi)"        # components, comma separated: idp-cmi,idp-host
PATHS="$(python3 -I tools/acceptance.py select --changed auto)"           # this repository's working tree against origin/main (--base REF)
PATHS="$PATHS" bash deploy/compose/e2e.sh 2>&1 | tee .local/e2e.log
python3 -I tools/acceptance.py verify --log .local/e2e.log --paths "$PATHS"
```

`select` prints the paths in the manifest's order: those whose components meet the changed set, every path they `need` (an E that F and G read), and PATH A, which always runs.
`--changed auto` takes the components whose commit or image moved in `releases/platform-bom.yaml`, the path scripts and helpers changed (`path_p3.sh` selects P3), and selects
**every** path when `deploy/keycloak/**`, `deploy/compose/platform.yaml`, `e2e.sh`, `lib_acceptance.sh`, `tools/acceptance.py` or `tests/paths.yaml` changed, or any other
file under `deploy/compose/` that no path claims. Docs, kind files, workflows and other tools select nothing by themselves. A component repository that is not yet in the BOM is not seen by
`auto`: name it with `--changed`. `PATHS` may also be written by hand (`PATHS="A P2"`; commas work); an id that is not in the manifest stops the script with exit 2.

Before a BOM (T2), every path, still with the double:

```bash
bash deploy/compose/e2e.sh 2>&1 | tee .local/e2e.log
python3 -I tools/acceptance.py verify --log .local/e2e.log --paths all
```

`verify` fails on any `FAIL:` or `BLOCKED:` line and on any PASS id of a selected path that is missing (`^PASS: PATH <id>` followed by a non-alphanumeric: `J0` does not stand in for `J`). Paths
that were not selected may be absent. With the double the steps that need real embeddings must say `NOT_RUN: PATH <id> needs real embeddings (EMBED=double)`.

T3 (CI does this; run it yourself only when you are changing the embedding image or the P1 and P2 scripts, and expect the 17 minutes of P2g back):

```bash
PATHS="$(python3 -I tools/acceptance.py select --t3)" EMBED=real bash deploy/compose/e2e.sh 2>&1 | tee .local/e2e-real.log
python3 -I tools/acceptance.py verify --log .local/e2e-real.log --paths "$(python3 -I tools/acceptance.py select --t3)" --embed real
```

Kind. Run `tests/kind/full.sh` when `deploy/kubernetes/**`, a NetworkPolicy, the sandbox's runtime (gVisor: `tests/kind/gvisor-node.sh`) or `tests/kind/**` changed, or before a BOM
that moved a component whose own manifests changed (idp-sandbox, idp-browser, idp-web, idp-mcp-bridge ship them). Not otherwise. `.github/workflows/kind.yml` runs on those paths and
nightly. `PATHS` and `EMBED` work as in compose (kind has its own order, `kindOrder` in the manifest; `EMBED=double` starts no text-embeddings-inference pod). Use a cluster of your own for it if
another one is in use (`KIND_CLUSTER`, `KIND_BACKUP_TARGET`, `KIND_BACKUP_PORT`, `KIND_KB_S3_NAME`, `KIND_KB_S3_PORT`), and never run compose and kind at the same time on one machine.

Do not wait on real embeddings in any other tier. A path that needs them belongs in T3.

## The embedding double

`deploy/images/embedding-double` (Python standard library on the mirror's `python:3.12-slim`) is both embedding instances when `EMBED=double`. idp-cmi calls one thing on it,
`POST {baseUrl}/embeddings` with `{"model", "input": [texts]}` and a Bearer key from a file, and reads `data[].index` and `data[].embedding`; the health checks call `GET /health`. The same
text always gives the same 1024-dimensional unit vector (what Qwen3-Embedding-0.6B gives and what `vector-index 1024` builds its HNSW indexes for): feature hashing of the normalised words
(NFKC, lower case, no function words), weighted by length so that one unusual word shared with a long passage still reaches idp-cmi's cosine floor, plus CJK character bigrams. idp-cmi's
query instruction is dropped first, so a question and a document with the same words get one vector, and the D59 probe (the same text to both instances, cosine at least 0.999) passes. The
CMI is configured with the model id `idp-embedding-double`, which it stores beside every vector: nothing made with the double can be taken for the real model's. Nothing is semantic: a
paraphrase with no shared word is not found, which is why `realEmbedding` marks P1 b to e (the paraphrases and the Chinese question) as T3's. Where the real model matches by meaning and the
double by words, the seeded text says the words too (PATH C's handbook document). The double's tests are `tools/tests/test_embedding_double.py`.

## Budgets

`budgetSeconds` of each path is in `tests/paths.yaml` (compose with the double; `realBudgetSeconds` with `EMBED=real`; `kindBudgetSeconds` on kind). Each run records every selected path's start
and end in `$TIMINGS` (default `.local/platform/timings.json`, `.local/kind-full/timings.json` on kind) and ends with the table of seconds against budgets and the total, and
`WARN: PATH X took Ns (budget Ms)` for any path over its budget. A warning does not fail the run: it asks you to look at why before the budget is raised.
`python3 -I tools/acceptance.py report --timings FILE [--mode kind] [--embed real]` prints it again. The budgets are the measured runs below plus headroom (about 2.5 times and a quarter of a minute: CI runners are slower than this machine).

Measured on 2026-11-26, this machine (20 cores, shared with the owner's other work), images already built from the current `main` of every component:

| Run | Wall clock | Of which the paths |
|---|---|---|
| T1, `--changed idp-cmi` (A E F G H N P0 P1 P2 P3 C), double | 5 min 41 s | 4 min 23 s (P1 99 s, P2 91 s, P0 34 s, P3 30 s, the others a few seconds) |
| T2, every path, double | 8 min 1 s | 6 min 23 s (O 73 s, P1 90 s, P2 91 s, P0 33 s, P3 30 s, M6 26 s, the other 19 paths 2 to 6 s each) |
| T3, A P1 P2, real image | 25 min 37 s | P1 205 s, P2 1201 s (P2g's drain 1103 s: the CPU embedding of the chunks) |
| before: every path, real image | about 28 min | P2g's drain about 17 min of it |

Setup (compose up, Keycloak, the registry and gateways, the CMI's databases) is about 80 s of every run. A cold Docker cache adds the image builds.

Kind (`SANDBOX_RUNTIME_CLASS=gvisor EMBED=double bash tests/kind/full.sh`, a throwaway single-node cluster with Calico and gVisor, images already built): 18 min 19 s for the 22 paths (M6 259 s, P2 292 s, O 107 s, P1 110 s, the others under 40 s),
against an hour or more with the real embedding pods. The node container peaked at 3.50 GiB (`docker stats`, which leaves out the page cache; 6.29 GiB by the cgroup's `memory.peak`, which counts it), sampled every 15 s; the real
pods are what made it 10.8 GiB (they request 5.5 GiB and may use 7: measured 2.7 and 2.3 GiB in use on compose, more while they warm up). The double's two pods are limited to 192 MiB each.

## Adding a path

1. In the script that runs it (`deploy/compose/e2e.sh`, and `tests/kind/full.sh` if kind runs it) wrap its block in `if want X; then path_start X` ... `path_end X; else path_skip X; fi`,
   in the order of `tests/paths.yaml`. Everything the path defines for a later path (a function, an agent, an app) stays in a block that runs when either is selected, or moves into the
   common setup, or the later path `needs` this one.
2. Add the entry to `tests/paths.yaml` in the same order: `id`, `pass` (the PASS ids it prints, each as `echo "PASS: PATH <id> ..."`), `components` (what it exercises),
   `needs` (paths whose state it reads: a variable, an agent, a count; not an ordering preference), `budgetSeconds` (measure it; add headroom), `kind: true` and `kindOrder` if kind runs it,
   `script` and `files` if it has its own script or helpers, `uses` and `erases` for the member accounts it uses and erases (P0 erases bob and P2 erases carol, so nothing that uses them may come after).
3. A step that needs semantic similarity (a paraphrase with no shared word, a question in another language) is listed under `realEmbedding` and guarded with
   `path_needs_real <PASS id> || ...` so that it prints the NOT_RUN line with the double.
4. `python3 -m unittest tools.tests.test_acceptance` checks the manifest against the scripts: every PASS id is printed by a script and no script prints one the manifest lacks, every path has its guard, and the
   `needs` and `erases` rules hold. CI's `verify` takes its list from the manifest, so there is nothing else to update.

## Not done yet

Paths on separate tenants, run in parallel, would cut T2 further: the paths that share state (bob's workspace, the web quota, the erasures of P0 and P2) would each get a tenant of their own. It
needs the realm, the gateways and the registry to be set up per tenant, and it is not part of this change.
