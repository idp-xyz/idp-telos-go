# The PostgreSQL image of the HA cluster (deploy/kubernetes/ha-postgres): the CloudNativePG operand image, PostgreSQL 17 on
# Debian 13, plus pgvector (idp-cmi D38: semantic search runs in the database). CloudNativePG starts and manages the server
# itself (no docker-entrypoint, no gosu); the image only has to carry the binaries and the extension, and barman-cli-cloud: the
# instances archive their WAL to the bucket and take base backups with it (deploy/kubernetes/ha-postgres/pitr.yaml).
# Build: deploy/images/postgres-ha/build.sh
FROM ghcr.io/cloudnative-pg/postgresql:17.11-standard-trixie
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-pgvector barman-cli-cloud \
 && apt-get -y upgrade \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
USER 26:26
