"""The Barman Cloud plugin's upstream manifest, limited to the namespaces that hold PostgreSQL clusters (README of
deploy/kubernetes/ha-postgres, "The Barman Cloud plugin"):

  python -I tools/ha_plugin_scope.py databases < manifest.yaml > scoped.yaml

As released, the plugin watches every namespace and its ClusterRole (read, create and delete Secrets, create Roles and RoleBindings,
manage ObjectStores) is bound cluster-wide: whoever controls the plugin Pod reads every Secret of the cluster. Here the Deployment gets
WATCH_NAMESPACE (plugin 0.16 and later) and that ClusterRole is bound with a RoleBinding in each listed namespace instead. The binding
for the metrics endpoint's token reviews stays cluster-wide (those are cluster-scoped APIs). Exits 1 when the manifest does not have the
objects this expects, so an upstream change is noticed rather than applied half-scoped. An installation that had the cluster-wide
binding must delete it (kubectl delete clusterrolebinding plugin-barman-cloud-binding): applying this does not remove it.

It also runs two replicas, never on the same node when there are several (plugin 0.16 and later; one leads the ObjectStore controller, both
answer the operator): while the operator cannot reach the plugin it does not reconcile a Cluster that uses it, so a failover waits for
the plugin (measured: none until the plugin came back, README.md).
"""

import sys

import yaml

BINDING = "plugin-barman-cloud-binding"
ROLE = "plugin-barman-cloud"
DEPLOYMENT = "barman-cloud"


def main() -> int:
    namespaces = sys.argv[1:]
    if not namespaces:
        print(__doc__, file=sys.stderr)
        return 2
    docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
    binding = next((d for d in docs if d["kind"] == "ClusterRoleBinding" and d["metadata"]["name"] == BINDING), None)
    deploy = next((d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == DEPLOYMENT), None)
    if not binding or binding["roleRef"]["name"] != ROLE or not deploy:
        print(f"FAIL: the manifest has no ClusterRoleBinding {BINDING} of {ROLE} or no Deployment {DEPLOYMENT}", file=sys.stderr)
        return 1
    containers = deploy["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1:
        print(f"FAIL: the Deployment {DEPLOYMENT} has {len(containers)} containers, expected 1", file=sys.stderr)
        return 1
    deploy["spec"]["replicas"] = 2
    labels = deploy["spec"]["template"]["metadata"]["labels"]
    # a spread that refuses, not a preferred anti-affinity: after the drains of a node maintenance both replicas had landed on one node
    # (a preference is only weighed when a Pod is placed); on one node (kind) that node is the only place and both run there
    deploy["spec"]["template"]["spec"]["topologySpreadConstraints"] = [
        {"maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "DoNotSchedule", "labelSelector": {"matchLabels": labels}}]
    env = [e for e in containers[0].get("env", []) if e["name"] != "WATCH_NAMESPACE"]
    containers[0]["env"] = env + [{"name": "WATCH_NAMESPACE", "value": ",".join(namespaces)}]
    out = [d for d in docs if d is not binding]
    for ns in namespaces:
        out.append({
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": {"name": BINDING, "namespace": ns, "labels": binding["metadata"].get("labels", {})},
            "roleRef": binding["roleRef"], "subjects": binding["subjects"],
        })
    yaml.safe_dump_all(out, sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
