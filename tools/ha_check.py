"""Static checks of the HA PostgreSQL manifests (deploy/kubernetes/ha-postgres), so that a weakening cannot go in unnoticed:

  python tools/ha_check.py         exit 0 passed, 1 failed

What it holds the cluster to (the properties tests/kind/ha-postgres.sh and failover-drill.sh then demonstrate on a real cluster):
three or more instances; synchronous replication with at least one synchronous standby and data durability "required" (no
acknowledged write that only the failed primary has); TLS from secrets (no self-signed default), a minimum of TLS 1.2, scram-sha-256,
and a pg_hba that rejects cleartext TCP before CloudNativePG's catch-all; instances spread over nodes; the policy that lets the
instances, the operator, the API server and the object store talk, and nothing wider; the Service "postgresql" that follows the primary;
continuous WAL archiving with a bounded archive_timeout, credentials from a Secret, a retention policy and a scheduled base backup
(pitr.yaml, which tests/kind/pitr-drill.sh then recovers from).
"""

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "kubernetes" / "ha-postgres"


def main() -> int:
    problems = []
    cluster = next(d for d in yaml.safe_load_all((ROOT / "cluster.yaml").read_text()) if d and d["kind"] == "Cluster")
    spec = cluster["spec"]
    pg = spec.get("postgresql", {})
    sync = pg.get("synchronous", {})
    if spec.get("instances", 0) < 3:
        problems.append("fewer than 3 instances: one failure leaves no standby to promote and no synchronous partner")
    if sync.get("number", 0) < 1 or sync.get("dataDurability") != "required":
        problems.append("synchronous replication must name at least one synchronous standby with dataDurability: required")
    if not sync.get("failoverQuorum"):
        problems.append("synchronous.failoverQuorum must be true (promote only a standby that holds every acknowledged commit)")
    certs = spec.get("certificates", {})
    if not certs.get("serverTLSSecret") or not certs.get("serverCASecret"):
        problems.append("the server certificate must come from secrets (serverTLSSecret and serverCASecret)")
    params = pg.get("parameters", {})
    if params.get("ssl_min_protocol_version") not in ("TLSv1.2", "TLSv1.3"):
        problems.append("ssl_min_protocol_version must be TLSv1.2 or TLSv1.3")
    if params.get("password_encryption") != "scram-sha-256":
        problems.append("password_encryption must be scram-sha-256")
    hba = pg.get("pg_hba", [])
    rejects = [i for i, l in enumerate(hba) if l.split()[:1] == ["hostnossl"] and l.split()[-1] == "reject"]
    if not rejects:
        problems.append("pg_hba must reject cleartext TCP (hostnossl ... reject)")
    elif any(l.split()[:1] == ["host"] for l in hba[: rejects[0]]):
        problems.append("a pg_hba line allows cleartext before the hostnossl reject")
    if spec.get("affinity", {}).get("enablePodAntiAffinity") is not True:
        problems.append("pod anti-affinity must be on: the instances must not share a node when there are several")
    docs = [d for d in yaml.safe_load_all((ROOT / "platform.yaml").read_text()) if d]
    svc = next((d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "postgresql"), None)
    if not svc or svc["spec"]["selector"].get("cnpg.io/instanceRole") != "primary":
        problems.append("the Service postgresql must select the primary (cnpg.io/instanceRole: primary)")
    pol = next((d for d in docs if d["kind"] == "NetworkPolicy"), None)
    if not pol:
        problems.append("no NetworkPolicy for the instances")
    else:
        for rule in pol["spec"].get("ingress", []):
            for src in rule.get("from", []):
                ns = src.get("namespaceSelector", {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
                if "ipBlock" in src or (src.get("namespaceSelector") is not None and ns != "cnpg-system"):
                    problems.append("the instances' ingress allows more than the instances themselves and the operator")
        for rule in pol["spec"].get("egress", []):
            for dst in rule.get("to", []):
                if "ipBlock" in dst and not dst["ipBlock"]["cidr"].endswith("/32"):
                    problems.append("the instances' egress to the API server must be a single address (/32)")
    pitr = [d for d in yaml.safe_load_all((ROOT / "pitr.yaml").read_text()) if d]
    patch = next((d for d in pitr if "kind" not in d), {})
    store = patch.get("spec", {}).get("backup", {}).get("barmanObjectStore", {})
    if not str(store.get("destinationPath", "")).startswith(("s3://", "gs://", "https://")):
        problems.append("pitr.yaml: barmanObjectStore.destinationPath must be an object store (s3://, gs://, https://)")
    creds = store.get("s3Credentials", {})
    if not all(isinstance(creds.get(k), dict) and creds[k].get("name") for k in ("accessKeyId", "secretAccessKey")):
        problems.append("pitr.yaml: the object store credentials must come from a Secret (name and key), not inline")
    if not patch.get("spec", {}).get("backup", {}).get("retentionPolicy"):
        problems.append("pitr.yaml: a retentionPolicy is required (the archive must not grow without bound)")
    timeout = int(patch.get("spec", {}).get("postgresql", {}).get("parameters", {}).get("archive_timeout", 0) or 0)
    if not 0 < timeout <= 300:
        problems.append("pitr.yaml: archive_timeout must be set, at most 300 s (it is the recovery point objective of a quiet database)")
    if not any(d.get("kind") == "ScheduledBackup" and d["spec"]["cluster"]["name"] == "postgresql" for d in pitr):
        problems.append("pitr.yaml: no ScheduledBackup for the cluster: recovery needs a base backup to start from")
    single = [r for r in (pol or {"spec": {}})["spec"].get("egress", []) for dst in r.get("to", []) if dst.get("ipBlock", {}).get("cidr", "").endswith("/32")]
    if pol and len(single) < 2:   # the API server and the object store, each one address
        problems.append("the instances' policy has no egress to the object store (a single address)")
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("PASSED: the HA PostgreSQL manifests: %d instances, synchronous quorum with data durability required, TLS from secrets, cleartext rejected, "
          "policy limited to the instances, the operator, the API server and the object store; WAL archiving, retention and a scheduled base backup" % spec["instances"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
