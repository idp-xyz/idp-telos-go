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
| `cluster.yaml` | the `Cluster`: 3 instances, **synchronous** replication (a commit is acknowledged only after one standby has it; with no standby the primary blocks rather than acknowledge a write only it has), quorum-checked promotion, TLS from secrets, TLS 1.2 or later, scram-sha-256, `pg_hba` that rejects cleartext TCP, never two instances on a node and one per zone, rolling updates that switch the primary over instead of restarting it |
| `operator-ha.yaml` | the operator's Deployment patched to two replicas, never on one node when there are several (it elects a leader) |
| `single-node.yaml` | DEV ONLY: the spreading relaxed for a cluster with fewer than three nodes in zones (the one-node kind cluster); `tools/ha_render.py` applies it and says so |
| `pitr.yaml` | continuous WAL archiving and a nightly base backup to the object store through the **Barman Cloud plugin**: the `ObjectStore` (7 days retained), a merge patch of the `Cluster` (the plugin is the WAL archiver, `archive_timeout` 60 s) and the `ScheduledBackup` |
| `platform.yaml` | the Service `postgresql` (always the primary: the name every component already connects to), the NetworkPolicy of the instances, default-deny for the operator's namespace with one policy per Deployment (the operator: DNS, the API server, the instances and the plugin out, the API server's webhook calls on 9443 in; the plugin: the API server out, the operator on 9090 in), and default-deny for cert-manager's (the API server out, its webhook on 10250 in) |
| `../../images/postgres-ha/` | the PostgreSQL image: the CloudNativePG operand plus pgvector, no backup tool (the plugin's sidecar has it) |
| `../../../tools/ha_plugin_manifest.py` | the plugin's release manifest limited to the namespace `databases`, with two replicas on different nodes (see below) |
| `../../../tools/ha_render.py` | `cluster.yaml` as the kind tests apply it: merge patches (`pitr.yaml`), the namespace, the one-node relaxation |
| `../../../tests/kind/ha-operators.sh` | the operator, cert-manager and the plugin installed as production runs them (`full.sh` and `ha-postgres.sh` use it) |
| `../../../tests/kind/multinode-cluster.sh` | a second kind cluster `ha`: a control plane and three workers in the zones zone-a, zone-b, zone-c, Calico |
| `../../../tests/kind/ha-maintenance.sh` | on it: the primary's node drained, a second drain refused, a node lost with the operator's leader and a plugin replica on it |
| `../../../tests/kind/ha-postgres.sh` | the cluster on its own on kind: kill the primary under a writer; on several nodes also the placement, with `ARCHIVE=1` the operators and archiving as production runs them |
| `../../../tests/kind/failover-drill.sh` | the whole platform on the cluster (`PG_HA=1 bash tests/kind/full.sh`): kill the primary while the CMI takes writes; `ROLLOUT=1`: a rolling restart of the cluster instead; `BLACKHOLE=1`: the primary vanishes without a reset; `NODELOSS=1` (several nodes): its node is stopped |
| `../../../tests/kind/pitr-drill.sh` | point-in-time recovery: a DELETE that replication copied everywhere is undone by a new cluster recovered to the instant before it |
| `../../observability/ha-postgres-alerts.yaml` | Prometheus rules (kube-state-metrics) for what the Cluster's status does not show: the operator or the plugin without a replica or with one, two replicas on one node, the standbys' budget stale while an instance is away; tested with promtool in CI |
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
`tools/ha_plugin_manifest.py databases` limits it to the namespace that holds the clusters (`WATCH_NAMESPACE`, and the ClusterRole bound by
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

## Several nodes (measured on kind, 2026-11-10)

`tests/kind/multinode-cluster.sh` makes a second kind cluster, `ha`: a control plane and three workers in the zones zone-a, zone-b and
zone-c, local volumes (each instance's data stays on its node), Calico. `KIND_CLUSTER=ha ARCHIVE=1 bash tests/kind/ha-postgres.sh`
runs the cluster there as production runs it (`cluster.yaml` unrelaxed, the operator and the plugin with two replicas, WAL archiving),
then `bash tests/kind/ha-maintenance.sh` drains and loses nodes under a writer that opens a new connection for every row.

**Where the instances go.** Four ways of asking for it, three instances each, on the three workers:

| asked for | zones labelled | result |
|---|---|---|
| anti-affinity `required` on the zone | yes | one per zone; with one worker left schedulable the second instance waits (Pending) |
| anti-affinity `required` on the zone | **no** | **all three on one node**, and the Cluster reports 3 ready instances: a node without the label is in no zone, so the rule never applies |
| anti-affinity `required` on the node + a zone spread (`DoNotSchedule`) | yes | one per zone |
| the same | **no** | nothing starts (not even the init job: "missing required label") |

`cluster.yaml` asks for the third: never two instances on a node, and one per zone, failing loudly where the nodes carry no zone.
The earlier advice here ("required, with the zone as the topology key") was the second row on a cluster without zone labels.
`single-node.yaml` relaxes both for the one-node kind cluster only (`tools/ha_render.py` says so when it applies it).

**The primary's Pod killed** (the writer gives every try 5 s): writes refused for **25–29 s**, as on one node. Across nodes a
connection to a killed primary hangs instead of failing (no reset comes back): a first version of the writer without a time limit
waited on one INSERT and the test reported 0 s. Clients need a connect and a statement timeout, or TCP keepalives, to notice a lost
primary; the platform's components were not run on several nodes.

**The primary vanishes under the whole platform** (`BLACKHOLE=1 bash tests/kind/failover-drill.sh` on the one-node cluster: every
packet from and to the primary's Pod is dropped in the node, so no reset comes back, as when its node loses power). The CMI refused
writes for **60–65 s**, against 26–29 s for a killed Pod: 32 s until the operator saw the primary unhealthy (a deleted Pod is an
event, a silent one has to fail its checks), 21 s to promote, 7 s for the CMI. Every component (CMI, HOST, Workspace, idp-mcp, EER)
went unready, came back within about 15 s of the promotion and reconnected to the new primary; no acknowledged write was lost. What
makes that work, and what does not:

- All of them used pgx with its defaults: no connect timeout, a TCP keepalive of 5 minutes, no `TCP_USER_TIMEOUT`. A dead connection
  was dropped only when the query's context ended (the readiness probes ping the database with a 3 s limit, callers give up), and a
  query with no deadline waited for TCP to give up: **151 s** measured against a blackholed instance, up to 15 minutes by the
  kernel's defaults. Since idp-cmi 640bf35, idp-host 8353dd2, idp-workspace 2472d16, idp-mcp 172506b and idp-eer 042dd53 the
  pools' sockets carry a connect timeout (5 s), a keep-alive (after 15 s idle) and `TCP_USER_TIMEOUT` (30 s): the same query fails
  after **30 s** and the pool reconnects. The outage the CMI sees is unchanged (55–65 s: it is the operator's detection and the
  promotion).
- **EER's World Model projector** ended its process on any database error, where the inbox projector next to it pauses and
  retries: with the timeouts above it restarted 3 times in one such outage (before them its query hung instead of failing). It
  now pauses and retries too, in idp-eer 4d1df11 (0 restarts in the next run, 5 pauses, ready again by itself): its readiness reports the error, its liveness does not depend on it.
- **idp-mcp's registry restarted in both runs**: its liveness probe is the same `/api/v1/health` as its readiness probe and pings
  the database, so a database outage of a minute kills it (the others' liveness is `/healthz`, without the database). It came
  back, but a longer outage would restart it over and over. Fixed in idp-mcp 545bee0 (a liveness probe on `/healthz`, which does
  not ask the database): in a third run the registry was not restarted.

**The primary's node drained** (`kubectl drain`): CloudNativePG switches the primary over to another node first; the drain takes
7–10 s, writes are refused for at most **6–8.4 s**, and the evicted instance waits for its node (its volume is local). Once the switchover
starts the old primary is relabelled `unhealthy` and no budget holds it any more, so the drain may take it during the switchover (which
stops it anyway); no acknowledged write was lost. A second node drained while that instance is away should be refused by CloudNativePG's
budget for the standbys (`postgresql`). It is, but not on its own:

- **The budget's status goes stale at the switchover.** Both of CloudNativePG's budgets select by role. After the switchover the
  standbys' budget still counted the promoted standby as a healthy replica: "expected 2, healthy 2, allowed 1" with one standby left,
  for 40 s and more (every run). The Pods left the budget by a change of label, and Kubernetes' disruption controller recomputes a
  budget only when a Pod it selects *now* changes (`updatePod` in v1.35 looks at the new labels only), so nothing recomputed it. A
  second drain in that state evicted the last standby, twice out of twice, and with one instance left the primary blocks every write
  (`dataDurability: required`). Touching the budget (`kubectl annotate pdb postgresql ...`) makes it recomputed: ALLOWED 0, and the
  second drain is refused, which the drill checks.
- **A budget of our own does not fix it.** One over every instance (`cnpg.io/podRole: instance`, `minAvailable: 2`) next to
  CloudNativePG's gave each Pod two budgets, and the eviction API refuses to evict such a Pod at all ("more than one
  PodDisruptionBudget"): every drain failed. `tools/ha_check.py` now rejects a budget of our own and `enablePDB: false`.

The procedure is therefore one node at a time: drain, maintain, uncordon, wait for "Cluster in healthy state", then the next node.
If another drain must happen while an instance is away, first `kubectl -n databases annotate pdb postgresql recheck=$(date +%s)
--overwrite` and check that `kubectl -n databases get pdb postgresql` shows ALLOWED 0. Automated upgrades that drain nodes back to
back (node pools, kured) must be held to the first rule. Reported upstream with this reproduction: cloudnative-pg #11897.

**The primary's node lost** (`docker stop`, no drain warning), in the worst case: the drill first puts the operator's leader and a
plugin replica on the primary's node. The operator replica on another node took the lead and promoted a standby **47 s** after the
node stopped; writes were refused for **49 s** (a killed Pod costs 25–29 s: here the lease has to expire first, 15 s, and the
primary's loss is noticed by the instances' status no longer answering). When the node came back, its instance rejoined as a
standby (3 ready 60 s after the node started). Over the drill's four steps: 1013 acknowledged rows, none missing.

**The whole platform on the three workers** (`KIND_CLUSTER=ha PG_HA=1 bash tests/kind/full.sh`): paths A to H pass, with `cluster.yaml`
unrelaxed. One thing had to change first: the API server calls a webhook on another node from the control plane's address on the Pod
network (Calico's tunnel address), not from its node address, and the policies of `platform.yaml` admitted the node address only.
cert-manager's webhook was unreachable, no certificate was issued and the plugin never started; on one node the two addresses are
never needed apart. Both webhook rules now name both addresses (on a managed cluster: the control plane's range). Then
`NODELOSS=1 KIND_CLUSTER=ha bash tests/kind/failover-drill.sh` stopped the primary's node under the CMI's writer, with a plugin
replica, Temporal and EER's projector on it: writes refused for **60.8 s**, 43 acknowledged records, none missing. Not covered by
it: Keycloak, Temporal, idp-mcp's gateway and registry and the sandbox run one replica each, and a Pod on a lost node is moved
only after 5 minutes; what the platform does without them for that long was not measured.

**The operators are part of the failover.** CloudNativePG's operator decides the failover, and while it cannot reach the Barman
Cloud plugin it does not reconcile a Cluster that uses it at all:

| what was down when the primary died | result |
|---|---|
| the operator's only replica (on the same node as the primary) | **no failover for 4 minutes and more** (until Kubernetes moves the operator, 5 minutes after the node is unreachable); the Cluster said "healthy, 3 ready" all along, nobody updated it |
| the plugin, its only replica (scaled to 0) | **no failover** ("cannot proceed to reconciliation due to an error while interacting with plugins"); 48 s after the plugin came back, the failover happened |
| one of two plugin replicas (force-deleted) | failover as usual: writes refused 26.3 s, 0 lost |

So the operator runs two replicas (`operator-ha.yaml`) and so does the plugin (`tools/ha_plugin_manifest.py`), never two on one node
when there are several: a spread that refuses (`DoNotSchedule`), not a preferred anti-affinity. With the preference, the drains of
the maintenance above had moved both operator replicas and both plugin replicas onto one node (a preference is only weighed when a
Pod is placed, and nothing moves them back), and losing that node then stopped the failover exactly as with one replica: no new
primary in 4 minutes. Monitoring has to alert on the operator's and the plugin's availability (and on two replicas sharing a node),
not only on the Cluster's status, which goes stale when the operator is gone.

**CloudNativePG 1.30.1 and a plugin that is not ready.** When the plugin answered a new Cluster's first reconciliation with "not found,
requeue" (its watches had failed while its RoleBinding was gone with a deleted namespace), the operator logged "Pre-reconcile hook
stopped the reconciliation loop" and never came back to that Cluster: no Pod for 15 minutes, until an annotation touched it. The
kind scripts restart the plugin after installing it; in production, a Cluster that stays without a phase after a plugin restart needs
the same touch (`kubectl annotate cluster ...`). Known upstream (cloudnative-pg #11618) and fixed by its PR #11683 ("requeue when a
hook asks for it without a delay"), merged after 1.30.1 and in no release yet: the restart can go once the operator has it.

## What this does not give you

- **Real zones and network volumes.** kind's zones are labels on containers of one machine, and its volumes are local to a node: an
  evicted instance waits for its node. With zonal network volumes (EBS, PD) it can move to another node of its zone; that and a zone's
  network failing (rather than a node stopping) were not run.
- **A key service for the WAL archive.** The archive and the base backups are encrypted at rest by the object store (`wal.encryption`
  and `data.encryption: AES256`, SSE-S3; `tools/ha_check.py` requires it): on kind an archived segment reads back with
  `x-amz-server-side-encryption: AES256`, is gzip through the API and not on the store's disk, and the PITR drill recovers from
  it. But RustFS's master key is generated by `tests/kind/s3-target.sh` and lives in the container's environment: whoever holds
  the store holds the key. Production uses `aws:kms` with a key of its own (or the provider's equivalent) and a bucket the
  instances can write but not delete. The `pg_dump` backups are age-encrypted before they leave the cluster (deploy/images/pg-backup).
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

    # several nodes: a second kind cluster "ha" (kubectl's current context stays as it is)
    bash tests/kind/multinode-cluster.sh                     # 1 control plane, 3 workers in 3 zones, Calico
    KIND_CLUSTER=ha ARCHIVE=1 bash tests/kind/ha-postgres.sh # as production: spread, two operator and plugin replicas, archiving
    bash tests/kind/ha-maintenance.sh                        # drain the primary's node, a second drain refused, lose a node
    KIND_CLUSTER=ha PG_HA=1 SKIP_BUILD=1 bash tests/kind/full.sh   # the whole platform there (it takes over the backup target and the local ports of the main cluster: run full.sh on that again afterwards)
    NODELOSS=1 KIND_CLUSTER=ha bash tests/kind/failover-drill.sh   # stop the primary's node under the CMI's writer
    kind delete cluster --name ha; docker rm -f idp-kind-ha-s3
