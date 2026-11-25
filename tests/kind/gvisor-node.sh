#!/usr/bin/env bash
# gVisor (runsc) in the node of a throwaway kind cluster and a RuntimeClass `gvisor`, for idp-browser's renderer (PATH M6 runs Chromium with --no-sandbox under it,
# as production does). The same settings as idp-sandbox/tests/kind/gvisor.sh (kvm platform, systemd-cgroup, raised inotify limits: its header says why), but for a
# named cluster and with the two binaries from where they are:
#   KIND_CLUSTER=NAME bash tests/kind/gvisor-node.sh                      download release $GVISOR_VERSION (storage.googleapis.com), sha512 checked
#   KIND_CLUSTER=NAME FROM_NODE=kind-control-plane bash tests/kind/gvisor-node.sh   copy runsc and containerd-shim-runsc-v1 out of another node that has them (read only)
set -euo pipefail
CLUSTER="${KIND_CLUSTER:?KIND_CLUSTER=NAME (the throwaway cluster)}"; NODE="$CLUSTER-control-plane"
VERSION="${GVISOR_VERSION:-20260126.0}" PLATFORM="${GVISOR_PLATFORM:-kvm}"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
if [[ -n "${FROM_NODE:-}" ]]; then
  for f in runsc containerd-shim-runsc-v1; do docker cp "$FROM_NODE:/usr/local/bin/$f" "$T/$f"; done
else
  U="https://storage.googleapis.com/gvisor/releases/release/$VERSION/x86_64"
  for f in runsc containerd-shim-runsc-v1 runsc.sha512 containerd-shim-runsc-v1.sha512; do curl -sfL --retry 3 --retry-all-errors -o "$T/$f" "$U/$f"; done
  (cd "$T" && sha512sum -c --quiet runsc.sha512 containerd-shim-runsc-v1.sha512)
fi
for f in runsc containerd-shim-runsc-v1; do chmod +x "$T/$f"; docker cp "$T/$f" "$NODE:/usr/local/bin/$f"; done
docker exec "$NODE" sh -c "sysctl -w fs.inotify.max_user_instances=8192 fs.inotify.max_user_watches=1048576 >/dev/null
grep -q 'runtimes.runsc\\]' /etc/containerd/config.toml || cat >> /etc/containerd/config.toml <<'TOML'
[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runsc]
  runtime_type = \"io.containerd.runsc.v1\"
[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runsc.options]
  TypeUrl = \"io.containerd.runsc.v1.options\"
  ConfigPath = \"/etc/containerd/runsc.toml\"
TOML
printf '[runsc_config]\n  platform = \"$PLATFORM\"\n  systemd-cgroup = \"true\"\n' > /etc/containerd/runsc.toml
systemctl set-property kubelet-kubepods.slice MemorySwapMax=0
systemctl restart containerd"
K=(kubectl --context "kind-$CLUSTER")
"${K[@]}" wait --for=condition=Ready node --all --timeout=180s >/dev/null
"${K[@]}" apply -f - >/dev/null <<'YAML'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: {name: gvisor}
handler: runsc
YAML
echo "OK: gVisor ($PLATFORM platform) in $NODE, RuntimeClass gvisor"
