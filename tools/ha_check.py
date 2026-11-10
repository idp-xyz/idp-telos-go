"""Static checks of the HA PostgreSQL manifests (deploy/kubernetes/ha-postgres), so that a weakening cannot go in unnoticed:

  python tools/ha_check.py         exit 0 passed, 1 failed

What it holds the cluster to (the properties tests/kind/ha-postgres.sh and failover-drill.sh then demonstrate on a real cluster):
three or more instances; synchronous replication with at least one synchronous standby and data durability "required" (no
acknowledged write that only the failed primary has); TLS from secrets (no self-signed default), a minimum of TLS 1.2, scram-sha-256,
and a pg_hba that rejects cleartext TCP before CloudNativePG's catch-all; instances spread over nodes; the policy that lets the
instances, the operator, the API server and the object store talk, and nothing wider; the Service "postgresql" that follows the primary;
the operator's namespace default-deny, its operator Pods called by the API server only and its Barman Cloud plugin by the operator only;
cert-manager's namespace default-deny, called by the API server only; continuous WAL archiving through the Barman Cloud plugin (not the
in-tree barmanObjectStore, removed in CloudNativePG 1.32) with a bounded archive_timeout, credentials from a Secret, a retention policy
and a scheduled base backup (pitr.yaml, which tests/kind/pitr-drill.sh then recovers from).
"""

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "kubernetes" / "ha-postgres"


def has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    return isinstance(obj, list) and any(has_key(v, key) for v in obj)


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
    if spec.get("primaryUpdateMethod") != "switchover":
        problems.append("primaryUpdateMethod must be switchover: a rolling update restarting the primary in place refuses new connections for minutes")
    if spec.get("affinity", {}).get("enablePodAntiAffinity") is not True:
        problems.append("pod anti-affinity must be on: the instances must not share a node when there are several")
    docs = [d for d in yaml.safe_load_all((ROOT / "platform.yaml").read_text()) if d]
    svc = next((d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "postgresql"), None)
    if not svc or svc["spec"]["selector"].get("cnpg.io/instanceRole") != "primary":
        problems.append("the Service postgresql must select the primary (cnpg.io/instanceRole: primary)")
    pol = next((d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "postgresql-ha"), None)
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
    op = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"].get("namespace") == "cnpg-system"]
    if not any(d["spec"].get("podSelector") == {} and set(d["spec"].get("policyTypes", [])) == {"Ingress", "Egress"} and not d["spec"].get("ingress") and not d["spec"].get("egress") for d in op):
        problems.append("cnpg-system needs a default-deny policy (ingress and egress, no rules)")
    operator_labels = {"app.kubernetes.io/name": "cloudnative-pg"}
    plugin_labels = {"app": "barman-cloud"}
    by_name = {d["metadata"]["name"]: d for d in op}
    for d in op:
        for rule in d["spec"].get("egress", []):
            for dst in rule.get("to", []):
                if "ipBlock" in dst and not dst["ipBlock"]["cidr"].endswith("/32"):
                    problems.append(f"cnpg-system/{d['metadata']['name']}: egress to the API server must be a single address (/32)")
                if not dst.get("ipBlock") and not dst.get("namespaceSelector"):
                    problems.append(f"cnpg-system/{d['metadata']['name']}: an egress rule selects Pods of its own namespace only: it must name where it goes")
        if d["metadata"]["name"] not in ("default-deny", "operator", "barman-cloud"):
            problems.append(f"cnpg-system/{d['metadata']['name']}: an unexpected policy in the operator's namespace")
    if "operator" not in by_name:
        problems.append("no NetworkPolicy named operator in cnpg-system")
    else:
        o = by_name["operator"]["spec"]
        if o.get("podSelector", {}).get("matchLabels") != operator_labels:
            problems.append("cnpg-system/operator must select the operator's Pods only (app.kubernetes.io/name: cloudnative-pg)")
        for rule in o.get("ingress", []):
            ports = {p["port"] for p in rule.get("ports", [])}
            if ports != {9443} or any("ipBlock" not in src or not src["ipBlock"]["cidr"].endswith("/32") for src in rule.get("from", [])):
                problems.append("the operator's only ingress is the API server (one address) on 9443, the webhooks")
        if not any(dst.get("podSelector", {}).get("matchLabels") == plugin_labels and {p["port"] for p in r.get("ports", [])} == {9090}
                   for r in o.get("egress", []) for dst in r.get("to", [])):
            problems.append("cnpg-system/operator has no egress to the Barman Cloud plugin on 9090 (WAL archiving would fail)")
    if "barman-cloud" not in by_name:
        problems.append("no NetworkPolicy named barman-cloud in cnpg-system (the plugin)")
    else:
        b = by_name["barman-cloud"]["spec"]
        if b.get("podSelector", {}).get("matchLabels") != plugin_labels:
            problems.append("cnpg-system/barman-cloud must select the plugin's Pods (app: barman-cloud)")
        for rule in b.get("ingress", []):
            if {p["port"] for p in rule.get("ports", [])} != {9090} or any(
                    set(src) != {"podSelector"} or src["podSelector"].get("matchLabels") != operator_labels for src in rule.get("from", [])) or not rule.get("from"):
                problems.append("the plugin's only ingress is the operator on 9090")
        if any(set(dst) != {"ipBlock"} for r in b.get("egress", []) for dst in r.get("to", [])):
            problems.append("the plugin's only egress is the API server (the sidecars, not the plugin, reach the object store)")
    cm = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"].get("namespace") == "cert-manager"]
    if not any(d["spec"].get("podSelector") == {} and set(d["spec"].get("policyTypes", [])) == {"Ingress", "Egress"} and not d["spec"].get("ingress") and not d["spec"].get("egress") for d in cm):
        problems.append("cert-manager needs a default-deny policy (ingress and egress, no rules)")
    for d in cm:
        for rule in d["spec"].get("ingress", []):
            if {p["port"] for p in rule.get("ports", [])} != {10250} or any("ipBlock" not in src or not src["ipBlock"]["cidr"].endswith("/32") for src in rule.get("from", [])):
                problems.append("cert-manager's only ingress is the API server (one address) on 10250, its webhook")
        if any(set(dst) != {"ipBlock"} or not dst["ipBlock"]["cidr"].endswith("/32") for r in d["spec"].get("egress", []) for dst in r.get("to", [])):
            problems.append("cert-manager's only egress is the API server (one address): no ACME, nothing outside the cluster")
    pitr = [d for d in yaml.safe_load_all((ROOT / "pitr.yaml").read_text()) if d]
    patch = next((d for d in pitr if "kind" not in d), {})
    objstore = next((d for d in pitr if d.get("kind") == "ObjectStore"), None)
    if any(has_key(d, "barmanObjectStore") for d in pitr + [cluster]):
        problems.append("pitr.yaml: the in-tree barmanObjectStore is removed from CloudNativePG 1.32: archive through the Barman Cloud plugin")
    if "backup" not in patch.get("spec", {}) or patch["spec"]["backup"] is not None:
        problems.append("pitr.yaml: the patch must remove spec.backup (backup: null), so a cluster using the in-tree barman moves in one change")
    if not objstore:
        problems.append("pitr.yaml: no ObjectStore (the Barman Cloud plugin's object store)")
        objstore = {"metadata": {}, "spec": {}}
    store = objstore["spec"].get("configuration", {})
    if not str(store.get("destinationPath", "")).startswith(("s3://", "gs://", "https://")):
        problems.append("pitr.yaml: the ObjectStore's destinationPath must be an object store (s3://, gs://, https://)")
    creds = store.get("s3Credentials", {})
    if not all(isinstance(creds.get(k), dict) and creds[k].get("name") for k in ("accessKeyId", "secretAccessKey")):
        problems.append("pitr.yaml: the object store credentials must come from a Secret (name and key), not inline")
    if store.get("serverName"):
        problems.append("pitr.yaml: the ObjectStore must not set serverName (the plugin ignores it; it is a parameter of the Cluster's plugin entry)")
    if not objstore["spec"].get("retentionPolicy"):
        problems.append("pitr.yaml: the ObjectStore needs a retentionPolicy (the archive must not grow without bound)")
    archivers = [p for p in patch.get("spec", {}).get("plugins", []) if p.get("name") == "barman-cloud.cloudnative-pg.io" and p.get("isWALArchiver")]
    if not archivers or archivers[0].get("parameters", {}).get("barmanObjectName") != objstore["metadata"].get("name"):
        problems.append("pitr.yaml: the Cluster must archive its WAL through the Barman Cloud plugin (isWALArchiver) into the ObjectStore defined there")
    timeout = int(patch.get("spec", {}).get("postgresql", {}).get("parameters", {}).get("archive_timeout", 0) or 0)
    if not 0 < timeout <= 300:
        problems.append("pitr.yaml: archive_timeout must be set, at most 300 s (it is the recovery point objective of a quiet database)")
    sched = [d for d in pitr if d.get("kind") == "ScheduledBackup" and d["spec"]["cluster"]["name"] == "postgresql"]
    if not sched:
        problems.append("pitr.yaml: no ScheduledBackup for the cluster: recovery needs a base backup to start from")
    elif sched[0]["spec"].get("method") != "plugin" or sched[0]["spec"].get("pluginConfiguration", {}).get("name") != "barman-cloud.cloudnative-pg.io":
        problems.append("pitr.yaml: the ScheduledBackup must use the Barman Cloud plugin (method: plugin)")
    single = [r for r in (pol or {"spec": {}})["spec"].get("egress", []) for dst in r.get("to", []) if dst.get("ipBlock", {}).get("cidr", "").endswith("/32")]
    if pol and len(single) < 2:   # the API server and the object store, each one address
        problems.append("the instances' policy has no egress to the object store (a single address)")
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("PASSED: the HA PostgreSQL manifests: %d instances, synchronous quorum with data durability required, TLS from secrets, cleartext rejected, "
          "policy limited to the instances, the operator, the API server and the object store; the operator, the Barman Cloud plugin and cert-manager "
          "default-deny; WAL archiving through the plugin, retention and a scheduled base backup" % spec["instances"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
