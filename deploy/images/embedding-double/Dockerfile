# The embedding double of the acceptance (EMBED=double; tests/ACCEPTANCE.md): a deterministic, instant stand-in for deploy/images/embedding, TESTS ONLY. It answers the
# one call idp-cmi makes (POST /v1/embeddings) with 1024-dimensional unit vectors made by feature hashing, and reports the model `idp-embedding-double`.
# Standard library only, on the mirror's Python image (the one the sandbox's run images use); no download, no network, no writable root. Build: build.sh
ARG BASE=ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim
FROM ${BASE}
COPY --chmod=0555 server.py /app/server.py
LABEL org.opencontainers.image.title="idp-embedding-double" \
      org.opencontainers.image.description="Deterministic embedding double for the platform acceptance (tests only)" \
      io.idp-telos.model="idp-embedding-double"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10700:10700
EXPOSE 8700
ENTRYPOINT ["python", "-I", "/app/server.py"]
