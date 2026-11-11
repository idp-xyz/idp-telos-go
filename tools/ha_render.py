"""The HA PostgreSQL Cluster as the kind tests apply it: deploy/kubernetes/ha-postgres/cluster.yaml with merge patches on the
Cluster (any other document of it follows as it is).

  python -I tools/ha_render.py [--namespace NS] [--merge FILE]... [--nodes FILE|-] > cluster.yaml

--merge   a merge patch (RFC 7386, as kubectl patch --type merge): every document of FILE without a "kind" (pitr.yaml's second
          document, single-node.yaml), in order
--nodes   the output of kubectl get nodes -o json: with fewer than three schedulable nodes that carry topology.kubernetes.io/zone
          (the one-node kind cluster) deploy/kubernetes/ha-postgres/single-node.yaml is merged last, which relaxes the spreading
          over nodes and zones (production keeps it), and a note on stderr says so

Exit 1 when cluster.yaml has no Cluster.
"""

import argparse
import json
import pathlib
import sys

import yaml

HA = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "kubernetes" / "ha-postgres"
ZONE = "topology.kubernetes.io/zone"


def merge(target, patch):
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge(out.get(key), value)
    return out


def zoned_schedulable(nodes: dict) -> list:
    """Names of the nodes a Pod of the cluster could go to and that are in a zone."""
    names = []
    for node in nodes.get("items", []):
        spec, labels = node.get("spec", {}), node.get("metadata", {}).get("labels", {})
        if spec.get("unschedulable") or any(t.get("effect") == "NoSchedule" for t in spec.get("taints") or []):
            continue
        if labels.get(ZONE):
            names.append(node["metadata"]["name"])
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace")
    ap.add_argument("--merge", action="append", default=[])
    ap.add_argument("--nodes")
    a = ap.parse_args()
    docs = [d for d in yaml.safe_load_all((HA / "cluster.yaml").read_text()) if d]
    cluster = next((d for d in docs if d.get("kind") == "Cluster"), None)
    others = [d for d in docs if d is not cluster]   # as they are, in the same namespace
    if not cluster:
        print("FAIL: no Cluster in cluster.yaml", file=sys.stderr)
        return 1
    patches = [pathlib.Path(p) for p in a.merge]
    if a.nodes:
        nodes = json.load(sys.stdin if a.nodes == "-" else open(a.nodes))
        zoned = zoned_schedulable(nodes)
        if len(zoned) < 3:
            patches.append(HA / "single-node.yaml")
            print(f"DEV: {len(zoned)} schedulable node(s) in a zone (fewer than 3): the instances may share a node, no zone needed "
                  "(deploy/kubernetes/ha-postgres/single-node.yaml)", file=sys.stderr)
    for path in patches:
        for doc in yaml.safe_load_all(path.read_text()):
            if doc and "kind" not in doc:
                cluster = merge(cluster, doc)
    if a.namespace:
        for d in [cluster] + others:
            d["metadata"]["namespace"] = a.namespace
    yaml.safe_dump_all([cluster] + others, sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
