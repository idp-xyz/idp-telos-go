# Highly available PostgreSQL

The platform's databases (idp-cmi, idp-host, idp-workspace, idp-mcp, idp-eer) are PostgreSQL. A single instance is a single point of
failure and a maintenance outage; this is the HA option. Two ways to get it, in this order of preference:

1. **A managed PostgreSQL with a multi-zone standby** (Amazon RDS or Aurora, Google Cloud SQL, Azure Database for PostgreSQL), where
   the platform runs in a cloud that has one. It gives failover, backups and point-in-time recovery without you operating them.
   Requirements: PostgreSQL 17, the `vector` extension (idp-cmi D38; without it the CMI works, slower), TLS with a certificate the
   components can verify (`sslmode=verify-full`, the CA in `PGSSLROOTCERT`), a failover endpoint (one DNS name that follows the
   primary), automated backups with PITR on, and a role that may `CREATE ROLE` and `CREATE EXTENSION` for the migrations.
2. **CloudNativePG on Kubernetes** (this directory), where there is no managed service or the data has to stay in the cluster.

## What is here

| File | Content |
|---|---|
| `cluster.yaml` | the `Cluster`: 3 instances, **synchronous** replication (a commit is acknowledged only after one standby has it; with no standby the primary blocks rather than acknowledge a write only it has), quorum-checked promotion, TLS from secrets, TLS 1.2 or later, scram-sha-256, `pg_hba` that rejects cleartext TCP, anti-affinity |
| `pitr.yaml` | continuous WAL archiving and a nightly base backup to the object store (merge patch of the `Cluster` plus a `ScheduledBackup`), 7 days retained, `archive_timeout` 60 s |
| `platform.yaml` | the Service `postgresql` (always the primary: the name every component already connects to), the NetworkPolicy of the instances, and default-deny plus three rules for the operator's namespace (DNS; the API server and the instances out; only the API server's webhook calls on 9443 in) |
| `../../images/postgres-ha/` | the PostgreSQL image: the CloudNativePG operand plus pgvector |
| `../../../tests/kind/ha-postgres.sh` | the cluster on its own on kind: kill the primary under a writer |
| `../../../tests/kind/failover-drill.sh` | the whole platform on the cluster (`PG_HA=1 bash tests/kind/full.sh`): kill the primary while the CMI takes writes |
| `../../../tests/kind/pitr-drill.sh` | point-in-time recovery: a DELETE that replication copied everywhere is undone by a new cluster recovered to the instant before it |
| `../../../tools/ha_check.py` | static check of these manifests (CI): the properties above cannot be weakened unnoticed |

## What was measured (kind, one node, 2026-11-10)

| | result |
|---|---|
| failover (primary Pod force-deleted under a writer) | the primary role moves to a standby; writes are refused for **26 s** (standalone) and **28.6 s** (through the CMI), then acknowledged again |
| acknowledged writes lost | **0** of 51 (standalone) and of 35 (through the CMI) |
| the old primary | comes back as a standby by itself |
| the CMI during the switch | its Pods did not restart (the connection pool reconnects); a retried write with the same idempotency key and body is accepted once, with another body it is a 409 |
| TLS | every client connection of the platform is encrypted (TLS 1.3); a cleartext TCP connection is rejected by `pg_hba` |
| the rest of the platform on the cluster | paths A to H pass; the encrypted backup and the restore drill pass against the cluster |

The 26 to 28 s is mostly the liveness check noticing the loss plus the promotion; it can be tuned (`failoverDelay`, probe settings) at the
price of more false failovers. Applications see it as a short window of failed writes: they must retry (the HOST's memory writes
already do, with backoff).

## Point-in-time recovery (measured on kind, 2026-11-10)

Replication protects against a lost instance, not against a mistaken statement (it is replicated too). Every instance archives its WAL
to the object store as a segment fills and at least every 60 s (`archive_timeout`: the recovery point objective of a quiet database),
and a base backup is taken nightly from a standby. `tests/kind/pitr-drill.sh`: 50 rows, the instant T, 20 more rows, `DELETE` of all
of them; a new cluster `postgresql-restore` bootstrapped from the object store with `recoveryTarget.targetTime = T` holds exactly the
50 rows (none of the 20, no DELETE), in **35–40 s** for a 1 MB database; the time grows with the size of the base backup and the WAL to
replay. The recovered cluster is a new one: point the applications at it (or copy the data back) once it is checked.

The operator's namespace is default-deny too: it may resolve names, reach the API server (one address) and every CloudNativePG instance on
5432 and 8000, and be called by the API server on 9443. Calico enforces it on kind: when the rule for instances was first written for
`cnpg.io/cluster: postgresql` only, a cluster recovered next to it showed `Instance Status Extraction Error` until the rule named every
cluster, so the policy is exercised, not just present. The webhook source is the API server's own address (the control plane node
on kind); on a managed cluster use the control plane's range.

## What this does not give you

- **Spreading over nodes and zones.** kind has one node; the anti-affinity is `preferred` so the test runs. In production use
  `required` and a topology key of the zone (`topology.kubernetes.io/zone`) so that losing a node or a zone leaves a standby, and run
  CloudNativePG's `PodDisruptionBudget`s through a real node drain.
- **Encryption of the WAL archive and of the base backups.** They go to the object store as gzip, readable by whoever can read the
  bucket. The `pg_dump` backups are age-encrypted (deploy/images/pg-backup); the archive is not. Production uses server-side
  encryption (SSE-KMS, a bucket key) and a bucket the instances can write but not delete; here MinIO has no KMS.
- **The Barman Cloud plugin.** `pitr.yaml` uses the in-tree `barmanObjectStore` of CloudNativePG 1.30, which the operator marks
  deprecated and removes in **1.31.0**. The operator is pinned at 1.30.1; moving to 1.31 means the plugin (it needs cert-manager)
  and a change of `pitr.yaml` and of the recovery's `externalClusters` only.
- **A supported upgrade path.** Operator, PostgreSQL minor and major upgrades have their own procedures (rolling update with a switchover
  of the primary is the default); rehearse them before relying on them.
- **Capacity.** Three instances triple the storage and need sized resources, connection limits (`max_connections`, a pooler such as
  PgBouncer through CloudNativePG's `Pooler`) and monitoring (the operator exports Prometheus metrics; alerts on replication lag, a
  missing standby, a blocked primary).

## Run it

    # the images are loaded into the Docker daemon with crane (the daemon cannot always pull; see deploy/kubernetes/RUNBOOK.md)
    bash tests/kind/ha-postgres.sh             # the cluster on its own, the failover drill
    PG_HA=1 bash tests/kind/full.sh            # the whole platform on it
    bash tests/kind/failover-drill.sh          # kill the primary while the CMI takes writes
    bash tests/kind/backup-drill.sh            # the encrypted backup and the restore, against the cluster
