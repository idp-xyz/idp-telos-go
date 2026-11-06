"""Render every component's Kubernetes manifests and check them as one platform (ADR-0001 P3).

For each integration of catalog/integrations.yaml: the caller's namespace has an egress rule to the
callee's namespace on the callee's port, and the callee's namespace has an ingress rule from the
caller's namespace on that port. For each component: its namespace enforces the restricted pod
security standard and has a default-deny policy. Integrations marked `kubernetes: not-deployed` are
reported, not checked.

  python tools/k8s_check.py [--root DIR]        exit 0 passed, 1 failed, 2 a component could not be rendered
  python tools/k8s_check.py --full              the same for the full kind overlay (deploy/kubernetes/full), where the
                                                policies are those of the composed platform, every namespace (also PostgreSQL,
                                                Keycloak, Temporal) is restricted and default-deny, and the flows to that
                                                infrastructure (catalog/infrastructure.yaml) are checked too
Rendering uses kustomize through `go run sigs.k8s.io/kustomize/kustomize/v5@KUSTOMIZE` (no binary needed).
"""
import argparse
import subprocess
import sys
from pathlib import Path

import yaml

KUSTOMIZE = "sigs.k8s.io/kustomize/kustomize/v5@v5.7.1"
HERE = Path(__file__).resolve().parents[1]


def render(path):
    out = subprocess.run(["go", "run", KUSTOMIZE, "build", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "kustomize failed")
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def admits(rule, key, other_labels):
    """whether a rule's from/to selects the namespace with these labels (Kubernetes adds
    kubernetes.io/metadata.name itself). An absent from/to admits every peer."""
    entries = rule.get(key)
    if entries is None:
        return True
    for e in entries:
        selector = e.get("namespaceSelector")
        if selector is None:
            continue  # a podSelector or ipBlock alone stays inside its own namespace or outside the cluster
        wanted = selector.get("matchLabels", {})
        if all(other_labels.get(k) == v for k, v in wanted.items()):
            return True
    return False


def opens(rule, port):
    ports = rule.get("ports")
    return ports is None or any(p.get("port") == port for p in ports)


def allows(policies, direction, other_labels, port):
    key = "from" if direction == "ingress" else "to"
    for p in policies:
        for rule in p["spec"].get(direction, []) or []:
            if admits(rule, key, other_labels) and opens(rule, port):
                return True
    return False


def check_full(root):
    """the composed platform of deploy/kubernetes/full: every namespace restricted and default-deny, every integration of
    the catalog (between components) and every flow to the in-cluster infrastructure open at both ends"""
    docs = render(root / "deploy/kubernetes/full")
    namespaces = {d["metadata"]["name"]: {**d["metadata"].get("labels", {}), "kubernetes.io/metadata.name": d["metadata"]["name"]}
                  for d in docs if d["kind"] == "Namespace"}
    policies = {n: [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"].get("namespace") == n] for n in namespaces}
    problems = []
    for n, labels in namespaces.items():
        if labels.get("pod-security.kubernetes.io/enforce") != "restricted":
            problems.append(f"namespace {n} does not enforce the restricted pod security standard")
        if not any(p["spec"].get("podSelector") == {} and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"}
                   and not p["spec"].get("ingress") and not p["spec"].get("egress") for p in policies[n]):
            problems.append(f"no default-deny NetworkPolicy in {n}")
    flows = [(i["id"], i["from"], i["to"], i["port"]) for i in
             yaml.safe_load((root / "catalog/integrations.yaml").read_text())["integrations"] if i.get("kubernetes") != "not-deployed"]
    flows += [(f["id"], f["from"], f["to"], f["port"]) for f in yaml.safe_load((root / "catalog/infrastructure.yaml").read_text())["flows"]]
    for fid, a, b, port in flows:
        if a not in namespaces or b not in namespaces:
            problems.append(f"{fid}: namespace {a if a not in namespaces else b} is not in the full overlay")
            continue
        if not allows(policies[a], "egress", namespaces[b], port):
            problems.append(f"{fid}: {a} has no egress to {b}:{port}")
        if not allows(policies[b], "ingress", namespaces[a], port):
            problems.append(f"{fid}: {b} admits no ingress from {a} on {port}")
    for line in problems:
        print("FAIL:", line)
    if problems:
        return 1
    print(f"PASSED: the full overlay has {len(namespaces)} namespaces, all restricted and default-deny; {len(flows)} flows are allowed at both ends")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=HERE)
    ap.add_argument("--full", action="store_true", help="check the composed platform of deploy/kubernetes/full instead of each component")
    ap.add_argument("--allow-missing", action="append", default=[], metavar="NAME",
                    help="a component that cannot be checked out here (a private repository in CI) is reported, not failed")
    args = ap.parse_args(argv)
    root = args.root
    if args.full:
        try:
            return check_full(root)
        except RuntimeError as e:
            print("FAIL: kustomize:", e)
            return 2
    components = yaml.safe_load((root / "catalog/components.yaml").read_text())["components"]
    integrations = yaml.safe_load((root / "catalog/integrations.yaml").read_text())["integrations"]
    rendered, problems, not_run, notes = {}, [], [], []
    for c in components:
        if c.get("kubernetes") == "not-deployed":
            notes.append(f"{c['name']}: no Kubernetes manifests (catalog: kubernetes: not-deployed)")
            continue
        path = root / c["name"] / "deploy/kubernetes"
        if not path.exists():
            not_run.append(f"{c['name']}: no deploy/kubernetes in the checkout")
            continue
        try:
            rendered[c["name"]] = render(path)
        except RuntimeError as e:
            problems.append(f"{c['name']}: kustomize: {e}")
    namespace_of, labels_of, policies_of = {}, {}, {}
    for name, docs in rendered.items():
        namespaces = [d for d in docs if d["kind"] == "Namespace"]
        # a component may own more than one namespace (idp-sandbox: the service, and where it runs untrusted code);
        # the one named like the component is the one integrations talk to
        primary = [n for n in namespaces if n["metadata"]["name"] == name] if len(namespaces) > 1 else namespaces
        if len(primary) != 1:
            problems.append(f"{name}: {len(namespaces)} namespaces, none named {name}" if namespaces else f"{name}: no namespace")
            continue
        ns = primary[0]
        namespace_of[name] = ns["metadata"]["name"]
        labels_of[name] = {**ns["metadata"].get("labels", {}), "kubernetes.io/metadata.name": ns["metadata"]["name"]}
        policies_of[name] = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"].get("namespace") == ns["metadata"]["name"]]
        for n in namespaces:  # every namespace of the component: restricted pod security and a default-deny policy
            nname = n["metadata"]["name"]
            if n["metadata"].get("labels", {}).get("pod-security.kubernetes.io/enforce") != "restricted":
                problems.append(f"{name}: namespace {nname} does not enforce the restricted pod security standard")
            if not any(d["kind"] == "NetworkPolicy" and d["metadata"].get("namespace") == nname and d["spec"].get("podSelector") == {}
                       and set(d["spec"].get("policyTypes", [])) == {"Ingress", "Egress"} and not d["spec"].get("ingress") and not d["spec"].get("egress")
                       for d in docs):
                problems.append(f"{name}: no default-deny NetworkPolicy in {nname}")
    # a peer selector that points into another namespace must not carry the policy owner's own
    # app.kubernetes.io/name: that is kustomize commonLabels rewriting selectors (it replaces the
    # peer's label, e.g. postgresql, by the component's), and the rule then matches nothing
    for name, policies in policies_of.items():
        own = labels_of.get(name, {}).get("app.kubernetes.io/name")
        for p in policies:
            for direction, key in (("ingress", "from"), ("egress", "to")):
                for rule in p["spec"].get(direction, []) or []:
                    for peer in rule.get(key, []) or []:
                        target = (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
                        pod = (peer.get("podSelector") or {}).get("matchLabels", {})
                        if target and target != namespace_of.get(name) and own and pod.get("app.kubernetes.io/name") == own:
                            problems.append(f"{name}: NetworkPolicy {p['metadata']['name']} selects pods in {target} by its own name label "
                                            f"({pod}) - commonLabels rewrote the peer selector, the rule matches nothing")
    for i in integrations:
        if i.get("kubernetes") == "not-deployed":
            notes.append(f"{i['id']}: not deployed by the manifests ({i['from']} -> {i['to']})")
            continue
        a, b, port = i["from"], i["to"], i["port"]
        if a not in rendered or b not in rendered:
            not_run.append(f"{i['id']}: {a if a not in rendered else b} not rendered")
            continue
        if not allows(policies_of[a], "egress", labels_of[b], port):
            problems.append(f"{i['id']}: {a} has no egress to {namespace_of[b]}:{port}")
        if not allows(policies_of[b], "ingress", labels_of[a], port):
            problems.append(f"{i['id']}: {b} admits no ingress from {namespace_of[a]} on {port}")
    for line in problems:
        print("FAIL:", line)
    for line in not_run:
        print("NOT_RUN:", line)
    for line in notes:
        print("NOTE:", line)
    if problems:
        return 1
    if any(not any(a in line for a in args.allow_missing) for line in not_run):
        return 2
    print(f"PASSED: {len(rendered)} components rendered; every deployed integration is allowed at both ends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
