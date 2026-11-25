# The platform's embedding service (knowledge-base plan KB2; idp-cmi ADR-0001 D11, D37): Hugging Face text-embeddings-inference (CPU build) serving
# Qwen3-Embedding-0.6B, the model idp-cmi recommends (ADR section 17). The weights are in the image at a pinned revision (MODEL_REVISION) and every
# file is checked against model.sha256 here; nothing is downloaded when it runs, and it needs no network at all. Build: deploy/images/embedding/build.sh
#   TEI  Apache-2.0 (the base image's label org.opencontainers.image.licenses); the model Apache-2.0 (its model card). See README.md.
ARG BASE=ghcr.io/idp-xyz/mirror:ghcr.io__huggingface__text-embeddings-inference__cpu-1.9.4@sha256:2538ea1c9640d3763b15af668039d24172d063b42337b0c27796fc2be180c78d
FROM ${BASE}
# build.sh stages the model files with their final modes (files 0444, directories 0555) so that this COPY is the only layer that holds the 1.2 GB
COPY model/ /model/
COPY model.sha256 /tmp/model.sha256
# the build fails if a single byte differs from what was reviewed; nothing in /model is writable and no file is left over
RUN cd /model && sha256sum -c /tmp/model.sha256 && test "$(find /model -type f | wc -l)" = "$(wc -l < /tmp/model.sha256)" && rm /tmp/model.sha256
COPY --chmod=0555 start.sh /usr/local/bin/idp-embedding
LABEL org.opencontainers.image.title="idp-embedding" \
      org.opencontainers.image.description="Qwen3-Embedding-0.6B on text-embeddings-inference (CPU); weights at a pinned revision, no runtime download" \
      org.opencontainers.image.licenses="Apache-2.0" \
      io.idp-telos.model="Qwen/Qwen3-Embedding-0.6B" \
      io.idp-telos.model-revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
ENV HUGGINGFACE_HUB_CACHE=/tmp HF_HUB_OFFLINE=1
USER 10700:10700
EXPOSE 8700 9700
ENTRYPOINT ["/usr/local/bin/idp-embedding"]
