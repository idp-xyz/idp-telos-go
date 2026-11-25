#!/bin/sh
# Start text-embeddings-inference on the weights in /model. The API key is read from a FILE (the platform keeps it in a Secret), not from the command line.
# The settings are the ones idp-cmi's evaluation measured (tests/eval/embedding-server.sh): batches of up to 64 texts, 4096 tokens per batch. The instance that only embeds
# questions (idp-cmi embedding.query) takes smaller ones: IDP_EMBEDDING_MAX_BATCH_TOKENS and IDP_EMBEDDING_MAX_CLIENT_BATCH. The warm-up pass runs a full batch, so the
# batch size sets the warm-up time and the peak memory; a text longer than the batch's tokens is refused (413).
set -eu
keyfile="${EMBEDDING_API_KEY_FILE:-/run/secrets/embedding/api-key}"
[ -s "$keyfile" ] || { echo "idp-embedding: no API key in $keyfile (EMBEDDING_API_KEY_FILE)" >&2; exit 2; }
API_KEY="$(cat "$keyfile")"; export API_KEY
# threads: the CPU limit the container runs under, not the host's cores (a 4-CPU quota on a 20-core host with 20 MKL threads is throttled to a crawl: the model took six
# minutes to warm up). RAYON_NUM_THREADS (the tokenizer, the batcher) and the MKL / OpenMP pools follow IDP_EMBEDDING_THREADS, 4 by default.
t="${IDP_EMBEDDING_THREADS:-4}"; RAYON_NUM_THREADS="$t"; OMP_NUM_THREADS="$t"; MKL_NUM_THREADS="$t"; export RAYON_NUM_THREADS OMP_NUM_THREADS MKL_NUM_THREADS
exec text-embeddings-router --model-id /model --hostname 0.0.0.0 --port 8700 --prometheus-port 9700 \
  --max-client-batch-size "${IDP_EMBEDDING_MAX_CLIENT_BATCH:-64}" --max-batch-tokens "${IDP_EMBEDDING_MAX_BATCH_TOKENS:-4096}" "$@"
