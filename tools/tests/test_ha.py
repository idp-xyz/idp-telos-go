"""Tests of tools/ha_check.py (every weakening of the HA PostgreSQL manifests is caught) and tools/ha_plugin_scope.py."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parents[1]
HA = TOOLS.parent / "deploy" / "kubernetes" / "ha-postgres"


def find(docs, kind=None, name=None, ns=None):
    for d in docs:
        if kind is None and "kind" not in d:
            return d
        if kind and d.get("kind") == kind and (name is None or d["metadata"]["name"] == name) and (ns is None or d["metadata"].get("namespace") == ns):
            return d
    raise KeyError((kind, name, ns))


def policy(docs, ns, name):
    return find(docs, "NetworkPolicy", name, ns)["spec"]


# file -> a change that weakens it; ha_check must fail on each
WEAKENINGS = {
    "the in-tree barmanObjectStore back": ("pitr.yaml", lambda ds: find(ds)["spec"].__setitem__("backup", {"barmanObjectStore": find(ds, "ObjectStore")["spec"]["configuration"]})),
    "the patch keeps spec.backup": ("pitr.yaml", lambda ds: find(ds)["spec"].pop("backup")),
    "a ScheduledBackup not through the plugin": ("pitr.yaml", lambda ds: find(ds, "ScheduledBackup")["spec"].pop("method")),
    "the archiver names another ObjectStore": ("pitr.yaml", lambda ds: find(ds)["spec"]["plugins"][0]["parameters"].__setitem__("barmanObjectName", "other")),
    "the plugin is not the WAL archiver": ("pitr.yaml", lambda ds: find(ds)["spec"]["plugins"][0].__setitem__("isWALArchiver", False)),
    "credentials inline": ("pitr.yaml", lambda ds: find(ds, "ObjectStore")["spec"]["configuration"]["s3Credentials"].__setitem__("accessKeyId", "AKIA123")),
    "no retention": ("pitr.yaml", lambda ds: find(ds, "ObjectStore")["spec"].pop("retentionPolicy")),
    "serverName on the ObjectStore": ("pitr.yaml", lambda ds: find(ds, "ObjectStore")["spec"]["configuration"].__setitem__("serverName", "x")),
    "no archive_timeout": ("pitr.yaml", lambda ds: find(ds)["spec"]["postgresql"]["parameters"].pop("archive_timeout")),
    "the primary restarted in place": ("cluster.yaml", lambda ds: find(ds, "Cluster")["spec"].__setitem__("primaryUpdateMethod", "restart")),
    "asynchronous replication": ("cluster.yaml", lambda ds: find(ds, "Cluster")["spec"]["postgresql"]["synchronous"].__setitem__("dataDurability", "preferred")),
    "the operator policy selects every Pod": ("platform.yaml", lambda ds: policy(ds, "cnpg-system", "operator").__setitem__("podSelector", {})),
    "the operator cannot reach the plugin": ("platform.yaml", lambda ds: policy(ds, "cnpg-system", "operator")["egress"].pop()),
    "the plugin open to its namespace": ("platform.yaml", lambda ds: policy(ds, "cnpg-system", "barman-cloud")["ingress"][0].__setitem__("from", [{"podSelector": {}}])),
    "the plugin open to everyone": ("platform.yaml", lambda ds: policy(ds, "cnpg-system", "barman-cloud")["ingress"][0].pop("from")),
    "the plugin's egress to all Pods": ("platform.yaml", lambda ds: policy(ds, "cnpg-system", "barman-cloud")["egress"].append({"to": [{"namespaceSelector": {}}]})),
    "no policy for the plugin": ("platform.yaml", lambda ds: ds.remove(find(ds, "NetworkPolicy", "barman-cloud", "cnpg-system"))),
    "no default-deny in cnpg-system": ("platform.yaml", lambda ds: ds.remove(find(ds, "NetworkPolicy", "default-deny", "cnpg-system"))),
    "no default-deny in cert-manager": ("platform.yaml", lambda ds: ds.remove(find(ds, "NetworkPolicy", "default-deny", "cert-manager"))),
    "cert-manager's webhook from anywhere": ("platform.yaml", lambda ds: policy(ds, "cert-manager", "webhook")["ingress"][0]["from"][0]["ipBlock"].__setitem__("cidr", "0.0.0.0/0")),
    "cert-manager to the internet (ACME)": ("platform.yaml", lambda ds: policy(ds, "cert-manager", "cert-manager")["egress"].append({"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}], "ports": [{"port": 443}]})),
}


class HaCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "tools").mkdir()
        shutil.copy(TOOLS / "ha_check.py", self.tmp / "tools")
        shutil.copytree(HA, self.tmp / "deploy" / "kubernetes" / "ha-postgres")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def check(self):
        return subprocess.run([sys.executable, "-I", str(self.tmp / "tools" / "ha_check.py")], capture_output=True, text=True)

    def test_the_manifests_pass(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_every_weakening_fails(self):
        for name, (file, change) in WEAKENINGS.items():
            with self.subTest(name):
                path = self.tmp / "deploy" / "kubernetes" / "ha-postgres" / file
                original = path.read_text()
                docs = [d for d in yaml.safe_load_all(original) if d]
                change(docs)
                path.write_text(yaml.safe_dump_all(docs))
                try:
                    r = self.check()
                    self.assertEqual(r.returncode, 1, f"not caught: {name}\n{r.stdout}")
                finally:
                    path.write_text(original)


MANIFEST = [
    {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "plugin-barman-cloud", "namespace": "cnpg-system"}},
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "plugin-barman-cloud-binding"},
     "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "plugin-barman-cloud"},
     "subjects": [{"kind": "ServiceAccount", "name": "plugin-barman-cloud", "namespace": "cnpg-system"}]},
    {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "barman-cloud", "namespace": "cnpg-system"},
     "spec": {"template": {"spec": {"containers": [{"name": "barman-cloud", "env": [{"name": "SIDECAR_IMAGE", "value": "x"}]}]}}}},
]


class PluginScope(unittest.TestCase):
    def scope(self, docs, *namespaces):
        return subprocess.run([sys.executable, "-I", str(TOOLS / "ha_plugin_scope.py"), *namespaces],
                              input=yaml.safe_dump_all(docs), capture_output=True, text=True)

    def test_binds_in_the_namespaces_only(self):
        r = self.scope(MANIFEST, "databases", "other")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = [d for d in yaml.safe_load_all(r.stdout) if d]
        self.assertFalse([d for d in out if d["kind"] == "ClusterRoleBinding"])
        bindings = [d for d in out if d["kind"] == "RoleBinding"]
        self.assertEqual(sorted(b["metadata"]["namespace"] for b in bindings), ["databases", "other"])
        self.assertTrue(all(b["roleRef"]["name"] == "plugin-barman-cloud" for b in bindings))
        env = find(out, "Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertIn({"name": "WATCH_NAMESPACE", "value": "databases,other"}, env)
        self.assertIn({"name": "SIDECAR_IMAGE", "value": "x"}, env)

    def test_an_unexpected_manifest_is_refused(self):
        self.assertEqual(self.scope(MANIFEST[:1] + MANIFEST[2:], "databases").returncode, 1)
        self.assertEqual(self.scope(MANIFEST).returncode, 2)   # no namespace given


if __name__ == "__main__":
    unittest.main()
