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
| `cluster.yaml` | the `Cluster`: 3 instances, **synchronous** replication (a commit is acknowledged only after one standby has it; with no standby the primary blocks rather than acknowledge a write only it has), quorum-checked promotion, TLS from secrets, TLS 1.2 or later, scram-sha-256, `pg_hba` that rejects cleartext TCP, anti-affinity, rolling updates that switch the primary over instead of restarting it |
| `pitr.yaml` | continuous WAL archiving and a nightly base backup to the object store through the **Barman Cloud plugin**: the `ObjectStore` (7 days retained), a merge patch of the `Cluster` (the plugin is the WAL archiver, `archive_timeout` 60 s) and the `ScheduledBackup` |
| `platform.yaml` | the Service `postgresql` (always the primary: the name every component already connects to), the NetworkPolicy of the instances, default-deny for the operator's namespace with one policy per Deployment (the operator: DNS, the API server, the instances and the plugin out, the API server's webhook calls on 9443 in; the plugin: the API server out, the operator on 9090 in), and default-deny for cert-manager's (the API server out, its webhook on 10250 in) |
| `../../images/postgres-ha/` | the PostgreSQL image: the CloudNativePG operand plus pgvector, no backup tool (the plugin's sidecar has it) |
| `../../../tools/ha_plugin_scope.py` | the plugin's release manifest limited to the namespace `databases` (see below) |
| `../../../tests/kind/ha-postgres.sh` | the cluster on its own on kind: kill the primary under a writer |
| `../../../tests/kind/failover-drill.sh` | the whole platform on the cluster (`PG_HA=1 bash tests/kind/full.sh`): kill the primary while the CMI takes writes; `ROLLOUT=1`: a rolling restart of the cluster instead |
| `../../../tests/kind/pitr-drill.sh` | point-in-time recovery: a DELETE that replication copied everywhere is undone by a new cluster recovered to the instant before it |
| `../../../tools/ha_check.py` | static check of these manifests (CI): the properties above cannot be weakened unnoticed (`tools/tests/test_ha.py` weakens them 21 ways and expects each to fail) |

## What was measured (kind, one node, 2026-11-10)

| | result |
|---|---|
| failover (primary Pod force-deleted under a writer) | the primary role moves to a standby; writes are refused for **26 s** (standalone) and **28.6–29.0 s** (through the CMI), then acknowledged again |
| acknowledged writes lost | **0** of 51 (standalone) and of 35 (through the CMI) |
| the old primary | comes back as a standby by itself |
| the CMI during the switch | its Pods did not restart (the connection pool reconnects); a retried write with the same idempotency key and body is accepted once, with another body it is a 409 |
| a rolling update (all 3 instances recreated, `primaryUpdateMethod: switchover`) | 67–72 s; new connections refused for at most 6.5–11.6 s, CMI writes for 8.8–13.3 s; 0 acknowledged writes lost (with the default `restart`: 214.8 s of refused new connections, see "Moving from the in-tree barman") |
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
50 rows (none of the 20, no DELETE), in **60–65 s** for a 1 MB database through the plugin (35–40 s with the in-tree barman: the recovery
Job now starts the plugin's sidecar too); the time grows with the size of the base backup and the WAL to replay. The recovered cluster
is a new one: point the applications at it (or copy the data back) once it is checked.

The operator's namespace is default-deny too: the operator may resolve names, reach the API server (one address), every CloudNativePG
instance on 5432 and 8000 and the Barman Cloud plugin on 9090, and be called by the API server on 9443; the plugin may reach the API
server and be called by the operator. Calico enforces it on kind: when the rule for instances was first written for
`cnpg.io/cluster: postgresql` only, a cluster recovered next to it showed `Instance Status Extraction Error` until the rule named every
cluster, so the policy is exercised, not just present. The webhook source is the API server's own address (the control plane node
on kind); on a managed cluster use the control plane's range.

## The Barman Cloud plugin

CloudNativePG's own `barmanObjectStore` is deprecated and removed in **1.32.0** (1.31 and its patch releases keep it; the 1.30 release
notes said 1.31, the operator's documentation now says 1.32). Archiving, base backups and recovery go through the plugin
`barman-cloud.cloudnative-pg.io` instead:

| Piece | Where | What it does |
|---|---|---|
| plugin **v0.16.0** (needs operator 1.30 or later) | Deployment `barman-cloud` in `cnpg-system`, gRPC on 9090 | the operator calls it to add a sidecar to every instance Pod and recovery Job, to create a Role per cluster (read its `ObjectStore` and the credentials Secret), and for backups |
| the sidecar `plugin-barman-cloud-sidecar:v0.16.0` | in each instance Pod, next to `postgres` | runs barman-cloud: WAL archive and restore, base backups, retention; reads its `ObjectStore` from the API server and writes the object store |
| cert-manager **v1.21.3** | namespace `cert-manager` | issues the two certificates of the operator-plugin gRPC (mutual TLS) from a self-signed `Issuer` in `cnpg-system`; nothing else uses it |
| the `ObjectStore` `postgresql-backups` | `databases` (`pitr.yaml`) | the bucket, its credentials (Secret `pitr-s3`), compression, the 7-day retention |

`tests/kind/full.sh` installs both from their release manifests, pinned by checksum, after the policies of their namespaces are in
place, and before the cluster exists. As released, the plugin watches every namespace and its ClusterRole (read, create and delete
Secrets, create Roles and RoleBindings) is bound cluster-wide: whoever controls the plugin Pod reads every Secret in the cluster.
`tools/ha_plugin_scope.py databases` limits it to the namespace that holds the clusters (`WATCH_NAMESPACE`, and the ClusterRole bound by
a RoleBinding there); the plugin logs `restricting to watched namespaces ["databases"]` and works with no `forbidden` error. A cluster in
another namespace then fails until that namespace is added.

The images are third-party and reported, not gating (`tools/scan_images.sh --third-party`, 2026-11-10): the plugin and its sidecar 5
HIGH each, cert-manager 1 or 2 per image, the operator 6, all in the Go standard library and `golang.org/x/net` they were built with
(denial of service in HTTP/2, Range headers, TLS ECH and HTML parsing; one in `crypto/x509` path building). None has a newer release.
The policies narrow who can send them anything: the plugin is reachable from the operator only; the sidecar talks to the instance
manager over a Unix socket and also listens on 8080 in the Pod, which the instances' policy (5432 and 8000 only) does not admit.

Measured on kind (one node, Calico): the plugin starts under its policy and the operator reaches it only through it; cert-manager starts
under default-deny and issues both certificates (the API server calls its webhook, it calls the API server, nothing else). Right after
the installation the operator registered the plugin before its Pod was ready and its first two connections timed out; after a restart
of the operator there were none (full.sh installs the plugin well before the cluster needs it).

## Moving from the in-tree barman (measured on kind, 2026-11-10)

A cluster that archives with `barmanObjectStore` moves without losing its archive or its backups:

1. install cert-manager and the plugin, scoped (above), with the policies of `platform.yaml`;
2. create the `ObjectStore` with the same `destinationPath`, endpoint and credentials as the old `barmanObjectStore` (`pitr.yaml`
   document 1; the retention moves from the `Cluster` to it);
3. apply the merge patch of `pitr.yaml` (document 2): `backup: null` removes the in-tree configuration and `plugins` adds the archiver
   in **one** change; then the `ScheduledBackup` with `method: plugin` (document 3).

On the running platform (3 instances, an in-tree base backup, 103 WAL segments archived): the patch rolled the 3 instances to Pods
with the sidecar in **253 s**. The primary was the last; until it was recreated it had no sidecar and its archiving failed (13 failed
attempts: "wal archive plugin is not available"); its segments stayed in `pg_wal` and the new Pod archived them, so the bucket held
every segment from 0x01 to 0x68 with none missing. The plugin reads the in-tree backups (the `ObjectStore`'s first recoverability
point is the in-tree backup from before the move); a base backup through the plugin took 13 s from a standby, and the PITR drill
passed on the moved cluster.

The move showed what any rolling update did to the primary: it was restarted in place (`primaryUpdateMethod` was the default,
`restart`), and its smart shutdown refused every new connection for up to `smartShutdownTimeout` (180 s) while the pools kept theirs
(during the move: from 10:43:19 to 10:46:41, then the fast shutdown and the new Pod).
`ROLLOUT=1 bash tests/kind/failover-drill.sh` measures it (a rolling restart of the 3 instances while the CMI writes and a probe opens
a new connection every 0.5 s):

| `primaryUpdateMethod` | rolling restart | longest stretch of refused new connections | longest stretch of refused CMI writes | acknowledged writes lost |
|---|---|---|---|---|
| `restart` (the default) | 247 s | **214.8 s** (110 refused: "the database system is shutting down", then timeouts) | 16.9 s | 0 |
| `switchover` (`cluster.yaml` now) | **72 s** | **11.6 s** (4 refused) | 13.3 s | 0 |
| `switchover`, a new platform with the plugin from the start | 67 s | 6.5 s (3 refused) | 8.8 s | 0 |

The CMI barely noticed either: its pool reuses open connections. A Pod that starts, a backup Job or a migration opens new ones, and with
`restart` they failed for three and a half minutes. With `switchover` the primary role moves to an updated standby first; the price is
that an image change and a PostgreSQL parameter change cannot go in the same update (the operator rejects it).

## What this does not give you

- **Spreading over nodes and zones.** kind has one node; the anti-affinity is `preferred` so the test runs. In production use
  `required` and a topology key of the zone (`topology.kubernetes.io/zone`) so that losing a node or a zone leaves a standby, and run
  CloudNativePG's `PodDisruptionBudget`s through a real node drain.
- **Encryption of the WAL archive and of the base backups.** They go to the object store as gzip, readable by whoever can read the
  bucket. The `pg_dump` backups are age-encrypted (deploy/images/pg-backup); the archive is not. Production uses server-side
  encryption (the `ObjectStore`'s `wal.encryption` and `data.encryption`: `AES256` or `aws:kms`, or a bucket key) and a bucket the
  instances can write but not delete; here MinIO has no KMS.
- **cert-manager for anything else.** It is here for the plugin's internal certificates only, with a self-signed issuer and no ACME;
  the PostgreSQL server certificate still comes from the secrets of `cluster.yaml`.
- **A supported upgrade path.** Operator, PostgreSQL minor and major upgrades have their own procedures (rolling update with a switchover
  of the primary is the default); rehearse them before relying on them.
- **Capacity.** Three instances triple the storage and need sized resources, connection limits (`max_connections`, a pooler such as
  PgBouncer through CloudNativePG's `Pooler`) and monitoring (the operator exports Prometheus metrics; alerts on replication lag, a
  missing standby, a blocked primary).

## Run it

    # the images are loaded into the Docker daemon with crane (the daemon cannot always pull; see deploy/kubernetes/RUNBOOK.md)
    bash tests/kind/ha-postgres.sh             # the cluster on its own, the failover drill
    PG_HA=1 bash tests/kind/full.sh            # the whole platform on it (with cert-manager, the plugin, WAL archiving)
    bash tests/kind/failover-drill.sh          # kill the primary while the CMI takes writes
    ROLLOUT=1 bash tests/kind/failover-drill.sh   # a rolling restart of the cluster while the CMI takes writes
    bash tests/kind/pitr-drill.sh              # point-in-time recovery through the plugin
    bash tests/kind/backup-drill.sh            # the encrypted backup and the restore, against the cluster
