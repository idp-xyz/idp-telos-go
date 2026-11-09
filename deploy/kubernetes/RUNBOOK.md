# 本机 Kubernetes 测试环境手册

WSL 里的 kind 集群（Calico）、整套平台在集群上的部署与验收、沙箱的 Docker / Kubernetes 后端，以及 gVisor 与 Kata 两种加固运行时。命令都在 2026-11-06 于参考主机上**从一个干净的环境（删掉集群、工具和镜像）按本手册从头执行过一遍**，下面的耗时是那次的实测。沙箱本身的运维见 `idp-sandbox/docs/RUNBOOK.md`；compose 版平台见本仓库 README。更完整的带图版本是本地的 `docs/reports/IDP_TELOS_Kubernetes_Test_Environment_Runbook_2026-11-06.html`（`docs/` 不入库）。

## 速查

| 项 | 值 |
|---|---|
| 集群 / 上下文 | `kind` / `kind-kind`，节点容器 `kind-control-plane`；kind v0.31，Kubernetes v1.35，Calico v3.30.3 |
| 工具 | `~/.local/bin`：`kind`、`kubectl`、`crane`（无需 root） |
| 命名空间 | `databases keycloak temporal idp-cmi idp-host idp-workspace idp-eer idp-mcp idp-sandbox idp-sandbox-runs` |
| 运行时类 | `gvisor`（runsc 20260126.0，kvm 平台）、`kata`（4.3.1，Go 运行时 + Cloud Hypervisor） |
| 端口转发（full.sh） | Keycloak 17443（https）· Workspace 17560 · HOST 17450 · CMI 17550 · EER 17180 · idp-mcp 注册服务 17250 · 网关 17320 |

```bash
kubectl get nodes; docker start kind-control-plane            # 重启后恢复集群
bash tests/kind/full.sh                                       # 整平台 PATH A–D；镜像已在时加 SKIP_BUILD=1；SANDBOX_RUNTIME_CLASS=gvisor|kata；MODEL=live；TEARDOWN=1
LOAD_LOCAL_IMAGES=1 bash idp-sandbox/tests/kind/smoke.sh      # 沙箱的集群检查
kind delete cluster --name kind; bash idp-sandbox/tests/kind/local-cluster.sh   # 删除 / 重建
```

**从零的顺序与实测耗时**（干净环境，同一台机器）：

| 步骤 | 命令 | 耗时 |
|---|---|---|
| 1 建集群 | `bash idp-sandbox/tests/kind/local-cluster.sh` | 约 8 分钟（装工具、拉节点和 Calico 镜像、建集群） |
| 2 整平台（含构建所有镜像） | `bash tests/kind/full.sh`（第一次不要加 `SKIP_BUILD`，镜像还不存在） | 约 2 分钟（Go 缓存命中；全新机器会更久） |
| 3 沙箱的集群检查 | `LOAD_LOCAL_IMAGES=1 bash idp-sandbox/tests/kind/smoke.sh` | 约 2.5 分钟，22 项 |
| 4 gVisor | `bash idp-sandbox/tests/kind/gvisor.sh`，再按下面"加固运行时"换运行时并检查 | 约 1 分钟装 + 2.5 分钟检查 |
| 5 Docker 后端在 gVisor 下 | `bash idp-sandbox/tests/integration/gvisor.sh` | 约 4 分钟（含取 `docker:dind`），20 项 |
| 6 Kata | `bash idp-sandbox/tests/kind/kata.sh`，再换运行时并检查 | 约 11 分钟（下载 1.3 GB）+ 2.5 分钟检查 |
| 7 整平台再跑一遍，沙箱在 gVisor 下 | `SKIP_BUILD=1 SANDBOX_RUNTIME_CLASS=gvisor bash tests/kind/full.sh` | 约 2 分钟 |
| 8 真实模型，在浏览器里体验 | `SKIP_BUILD=1 SANDBOX_RUNTIME_CLASS=gvisor MODEL=live bash tests/kind/full.sh`，再按"访问集群里的平台"开转发、登录 | 约 2 分钟（本机模型已在运行） |

## 环境须知

**节点和 Docker 守护进程拉不了 Docker Hub、quay.io、ghcr 的镜像。** shell 的 `https_proxy` 只对 curl、crane 有效；kind 把它复制进节点后指向节点自己。所有镜像都要从宿主加载进节点。

- 没有免密 sudo：工具装在 `~/.local/bin`；gVisor / Kata 装进节点容器（特权容器）。
- Docker 用 containerd 镜像存储：`kind load docker-image` 失败，用 `docker save --platform linux/amd64 IMG -o f.tar` 加 `kind load image-archive f.tar --name kind`（脚本里的 `load_image`）。
- 宿主有 `/dev/kvm`（WSL2 嵌套）：gVisor 的 `kvm` 平台、Kata 的虚拟机可用。
- 本机模型（`MODEL=live`）：宿主上 OpenAI 兼容的模型服务，默认 `http://127.0.0.1:8080/v1`（参考主机上是 qwen，支持工具调用）。Pod 经 kind 网络网关上的转发访问它：容器 `idp-kind-model-forward`（宿主网络，只绑 `172.31.0.1:18088`，随 Docker 重启）；`full.sh` 每次重建它，HOST 命名空间只放行这一个地址与端口。
- compose 版平台用 7443 / 8250 / 3320 / 8450 / 8550 / 8560 / 18180–18182，kind 版用 17xxx，可以并存。不要碰 `idp-pulsar` 与其他测试容器。

## 从零建集群与镜像

```bash
bash idp-sandbox/tests/kind/local-cluster.sh
```

装 kubectl / kind / crane；用 crane 经代理拉 `kindest/node:v1.35.0` 与 Calico 的三个镜像并加载；`kind create cluster`（`idp-sandbox/tests/kind/kind.yaml`，关掉默认 CNI）；装 Calico（镜像名换成 quay.io）；等节点 Ready。**必须是 Calico：** kind 默认的 kindnet 没有拦住运行 Pod 的出站，"无网络"只有换 CNI 才成立。

| 镜像 | 怎么进节点 |
|---|---|
| 本机已有（`idp-*:dev`、`eer-runtime:dev`、postgres、keycloak、`local/temporal:1.7.3`） | `load_image` |
| 本机没有、registry 经代理可达 | `crane pull IMG f.tar` 再 `kind load image-archive f.tar --name kind` |
| 沙箱的运行镜像 `python:3.12-slim`、`alpine:3.22` | `LOAD_LOCAL_IMAGES=1` 从本机的 ECR 镜像打短名并加载 |
| 重建组件镜像 | 各仓库 `deploy/build.sh`；EER：`idp-eer/tools/development/runtime_image_offline.sh eer-runtime:dev`；`full.sh` 不带 `SKIP_BUILD` 时全部重建 |

## 整平台（PATH A–D）

`SKIP_BUILD=1 bash tests/kind/full.sh`：先删十个平台命名空间再重建（每次都是全新的平台）；生成本次的 CA、realm、密钥到 `.local/kind-full`（已忽略）；用后台端口转发跑与 compose 版相同的验收，成功时输出四行 `PASS: PATH …`。

- **PATH A**：bob（Keycloak）→ Workspace 换令牌 → HOST 运行 → CMI 上下文包 → bob 的收件箱。
- **PATH B**：operator 提交 T2 意图 → EER → bob 的收件箱 → 经 Workspace 批准 → EER 完成，审计里批准人是 bob。
- **PATH D**：bob 运行 Python → HOST → idp-mcp 网关（HOST 为 bob 换来的令牌）→ 沙箱（Kubernetes 后端，转发调用者）→ `answer 42` 回到收件箱；网关记录里 subject 是 bob、client 是 idp-host。
- **PATH E**：bob 对记忆助手说一句话 → HOST 把这次对话作为情景记忆写进 CMI（作用域是 bob）→ 新对话的上下文包里取回它 → 同一对话保留历史 → alice 看不到。
- **PATH F**：bob 经 Workspace（用他自己的令牌换 CMI 的令牌）列出并删除自己的记忆，alice 删不了；删除后列表和检索里都没有。
- **PATH C**：EER Intelligence 的集成测试，在宿主上用 uv 对集群里的 CMI 运行（`env -i` 干净环境）。
- 之后装好"助手"应用（`deploy/apps/assistant`）：两个 agent 的聊天页，页面由 `idp-workspace` 命名空间里的静态服务提供（`deploy/kubernetes/full/assistant.yaml`）。
- `MODEL=live`：两个 agent 用本机模型；PATH A 另查回答引用了 CMI 里的规定，PATH D 查回答报告了代码的输出（工具确实被调用由网关的记录证明）。

轻量版（只有 CMI、HOST、Workspace，开发令牌，无 Keycloak；GitHub 上的 kind 工作流跑的就是它）：`KIND_CLUSTER=kind SKIP_BUILD=1 LOAD_BASE_IMAGES=1 bash tests/kind/smoke.sh`。静态检查（不需要集群）：`uv run --no-project --with PyYAML==6.0.3 python -I tools/k8s_check.py --full`——10 个命名空间都是 restricted 且默认拒绝，18 条流量在两端都被放行。

## 沙箱的检查与加固运行时

```bash
cd idp-sandbox
LOAD_LOCAL_IMAGES=1 bash tests/kind/smoke.sh      # 构建、加载、应用清单、跑 tests/kind/check.py（22 项）

# 换运行时后跑同一套检查：先装运行时，再改沙箱的环境变量，开端口转发，跑检查
bash tests/kind/gvisor.sh                          # gVisor 装进节点，RuntimeClass gvisor
kubectl -n idp-sandbox set env deploy/idp-sandbox IDP_SANDBOX_RUNTIME_CLASS=gvisor
kubectl -n idp-sandbox rollout status deploy/idp-sandbox
kubectl -n idp-sandbox port-forward svc/idp-sandbox 8570:8570 &       # check.py 默认连 127.0.0.1:8570（SANDBOX_URL 可改）
EXPECT_GVISOR=1 python3 tests/kind/check.py        # 集群 IP 它自己用 kubectl 查（也可用 API_IP / SANDBOX_IP 指定）

bash tests/kind/kata.sh                            # 自己下载 kata-go-static（1.3 GB）；已有包时 KATA_TARBALL=路径
kubectl -n idp-sandbox set env deploy/idp-sandbox IDP_SANDBOX_RUNTIME_CLASS=kata
EXPECT_KATA=1 python3 tests/kind/check.py          # 端口转发还在；断了就重开

bash tests/integration/gvisor.sh                   # Docker 后端全在 runsc 下（docker-in-docker；docker:dind 镜像脚本会自己取）
```

`check.py` 每次检查会创建并删除自己的工作区；**如果它中途崩了（或被中断），留下的工作区要等 30 分钟空闲回收**，期间再跑 `nothing-left-behind` 会失败——`kubectl -n idp-sandbox-runs delete pvc --all` 清掉再跑。

`smoke.sh` 会把 `idp-sandbox` 命名空间里的沙箱换成它自己的清单，和整平台的沙箱重叠，先跑哪个另一个就要重新部署。

| 运行时 | 结果 | 要点 |
|---|---|---|
| runc | 22/22；Docker 20/20 | 与宿主共享内核 |
| gVisor | Kubernetes 22/22；Docker 20/20（严格限额）；整平台 PATH D 通过 | 内核 4.4.0；`/proc/self/status` 无 `NoNewPrivs`；`--pids-limit` 要 ≥ 64；runsc 要开 `systemd-cgroup`、Pod 要关 swap，否则运行的内存与 CPU 上限不生效（最初那次 22/22 里内存炸弹是靠超时过的，修好后 4.5 s 被 OOM 杀） |
| Kata | Kubernetes 22/22 | 客户机内核 6.18.56；内存型 `/tmp` 无大小限制，写满撞内存上限被 OOM 杀；启动较慢 |

性能（同机，Python 3.12）：Pod 从创建到完成 runc 约 3.7 s / gVisor 5–8 s / Kata 约 8 s；4000 个小文件 0.18 / 0.17 / 2.5 s；10 万次 `stat` 0.06 / 0.56 / 0.05 s；300 MB 内存触碰 0.12 / 0.5 / 0.55 s。节点 inotify 上限默认 128，gVisor 的垫片会因此起不来；`gvisor.sh` 调到 8192，这是内核全局设置，**WSL 重启后恢复**：`docker exec kind-control-plane sysctl -w fs.inotify.max_user_instances=8192`。它给 Pod 关掉的 swap 也会在节点重启后恢复：`docker exec kind-control-plane systemctl set-property kubelet-kubepods.slice MemorySwapMax=0`，或者直接重跑 `gvisor.sh`。

## 用浏览器体验（助手页、记忆）

`bash tests/kind/forward.sh` 在后台保持 Keycloak（17443）、壳（17560）、助手页（17561）的端口转发（`full.sh` 结束时会关掉自己的）；先信任测试 CA `.local/kind-ca/ca.crt`（只需一次：CA 跨 `full.sh` 保留，每次只换服务器证书；也可以先在浏览器里打开 `https://127.0.0.1:17443` 并接受警告）。**没信任时，登录按钮点了没反应是因为浏览器自己去取 Keycloak 的发现文档被拒绝**——新版壳会在按钮下提示原因，用 `bob`（密码打印在脚本输出里，也在 `.local/kind-full/clients.json`）登录 `http://127.0.0.1:17560`。`MODEL=live bash tests/kind/full.sh` 让 Agent 用本机 8080 的真实模型。

- **助手**应用有三个标签：订单助手（按企业知识答）、代码助手（在沙箱里跑代码）、**记忆助手**（记得你说过的话）。“新对话”清掉这次对话的历史；企业记忆（CMI）还在。
- 在记忆助手里说“我最喜欢的颜色是青色”，点“新对话”，再问“我最喜欢什么颜色”：它靠 CMI 里的记忆想起来。同一次对话里问“我刚才说了什么”：靠对话历史。
- 工作台的**记忆**页列出助手记下的关于你的内容，可逐条删除或全部删除（删除后不再被取回）。

## 数据持久与备份演练

PostgreSQL 的数据卷是 PVC `postgres-data`（5Gi）；`backup.yaml` 的 CronJob `pg-backup` 每小时（`17 * * * *`）把每个库 `pg_dump -Fc`、全局对象 `pg_dumpall --globals-only` 写进 PVC `postgres-backups`，`pg_restore -l` 校验、写 `MANIFEST.sha256`，先写 `.partial` 再改名，保留 7 份。

`NODE_RESTART=1 bash tests/kind/backup-drill.sh`（约 1.5 分钟，要先有 `full.sh` 起的集群）：写一条标记记忆 → 备份 → 重启 PostgreSQL Pod → 重启节点 → `DROP DATABASE idp_cmi` → 从备份恢复 → 确认 CMI 又能服务、bob 仍读到标记记忆。不设 `NODE_RESTART` 就跳过节点重启。

范围：数据在 Pod 重启和节点重启后保留；**重跑 `full.sh` 会重建命名空间，数据随之清空**，密钥也每次重新生成。备份未加密、与数据在同一节点，**不是灾备**；生产要用高可用 PostgreSQL、异地加密备份、漏备份告警。

## 访问集群里的平台

```bash
kubectl -n idp-workspace port-forward svc/idp-workspace 17560:8560 &
kubectl -n keycloak port-forward svc/keycloak 17443:7443 &    # 令牌的 iss 必须是 https://127.0.0.1:17443/realms/idp
kubectl -n idp-workspace port-forward svc/assistant 17561:8080 &   # "助手"应用的页面（自己的源）
# 用户密码与客户端密钥：.local/kind-full/clients.json；CA：.local/kind-ca/ca.crt（跨运行保留，已忽略）
```

`full.sh` 自己会起这些转发并在结束时关闭；手工转发在 Pod 滚动重启后会断，重新执行即可。

Web 壳：浏览器打开 `http://127.0.0.1:17560`，用 alice（管理员，多一个"管理"页）或 bob（成员）登录，密码在 `clients.json` 的 `users` 里。Keycloak 用的是本次运行的测试 CA：先在浏览器里打开 `https://127.0.0.1:17443` 放行证书（或导入 `ca.crt`），否则点登录没有反应。登录后："发起"按动作的输入 Schema 生成表单（问订单助手、让 agent 跑代码），结果回到收件箱；"助手"是聊天页，订单助手按 CMI 里的取件规定作答（中英文各一份），代码助手在沙箱里写代码并运行；审批在收件箱里批准 / 拒绝。`MODEL=live` 时回答来自本机模型，否则是模型替身的回声。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| Pod `ImagePullBackOff`，日志 `proxyconnect tcp: 127.0.0.1:45221 … refused` | 节点继承了宿主的代理 | 把镜像加载进节点；`imagePullPolicy: IfNotPresent` 或 `:dev` 标签 |
| `check.py`：`nothing-left-behind` 失败，列出 `persistentvolumeclaim/…` | 上一次检查崩了或被中断，留下了工作区 | `kubectl -n idp-sandbox-runs delete pvc --all`，或等 30 分钟空闲回收 |
| `kind load docker-image`：`ctr … images import … failed` | containerd 镜像存储保存了全平台 | `docker save --platform linux/amd64` 再 `kind load image-archive` |
| Docker 拉 Hub / quay：`TLS handshake timeout` | 守护进程不走 shell 的代理 | `crane pull IMG f.tar` + `docker load` |
| 运行 Pod 能连外网 | CNI 不执行 NetworkPolicy | 用 Calico |
| gVisor Pod 一直 `ContainerCreating`：`creating process: out of memory` / `failed to create a syscall thread` | 默认的 `systrap` 平台在这台 WSL2 上起不来（根因未查） | `runsc.toml` 里 `platform = "kvm"`（或 `ptrace`），版本 20260126.0 |
| gVisor 垫片 `too many open files` | 节点 inotify 上限 128 | `sysctl -w fs.inotify.max_user_instances=8192` |
| gVisor 下内存炸弹不被杀、跑满超时才停（`memory-bomb-killed` 失败） | kind 用 systemd cgroup 驱动，runsc 没开 `systemd-cgroup` 时把 Pod 的 cgroup 路径当成目录名，沙箱跑在 Pod 的 cgroup 之外，内存与 CPU 上限都不生效；开了之后，kubelet 只给容器关 swap，节点又有 WSL2 的 swap，炸弹会先换进 swap、偶尔拖十几秒才被杀 | `runsc.toml` 里 `systemd-cgroup = "true"`，`systemctl set-property kubelet-kubepods.slice MemorySwapMax=0`；`gvisor.sh` 都已包含，节点重启后重跑 |
| Kata：`systemd dbus error` | Rust 运行时需要节点里没有的 D-Bus | 用 Go 运行时（kata-go-static） |
| Kata：QEMU 起来就崩（`Failed to negotiate QMP`） | 嵌套 KVM 上 QEMU 客户机崩溃 | 用 Cloud Hypervisor（`configuration-clh.toml`），`sandbox_cgroup_only = false` |
| gVisor：`--pids-limit 16` 起不来；docker-in-docker 里 `cannot read client sync file` | 上限数的是沙箱的宿主线程；嵌套 Docker 没有 cgroup 权限 | 上限用 64；`--cgroupns=host` |
| 网关到沙箱不通 | 网络策略没放行 8570 | 网关与注册服务的出口策略加 8570；沙箱命名空间带 `idp-mcp/mcp-upstream: "true"` |
| `MODEL=live`：运行失败或一直 running | 本机模型没开，或转发容器停了 | `curl 127.0.0.1:8080/v1/models`；`docker start idp-kind-model-forward`；`docker logs idp-kind-model-forward` |
| "助手"页显示"请在 IDP Workspace 里打开" | 直接打开了页面，或 17561 的转发没开 | 从壳的导航进入"助手"；开 assistant 的端口转发 |
| 沙箱：`the request carries no caller identity` | 没有以 `identity: forward` 注册 | 重新注册（不可改，要新建） |
| 迁移 / 建租户的 Job 显示 `Error` | 数据库还没起来时的重试 | 看最后一次是否 `Completed` |
| `kustomize: go: … goproxy.cn … EOF` | 代理偶发中断 | 重试 |
| `gh` 命令 `EOF` / CI 克隆 `403` | GitHub 偶发限流 | 稍后重试；`gh run rerun --failed` |

## 停止与清理

```bash
docker stop kind-control-plane                    # 停集群，保留（docker start 恢复）
TEARDOWN=1 SKIP_BUILD=1 bash tests/kind/full.sh   # 跑完后删掉平台命名空间
kind delete cluster --name kind                   # 彻底删除
docker rm -f sbx-dind sbx-dind2                   # gVisor 的 docker-in-docker（脚本会自己清理）
```

## 限制

- 开发验证环境：开发令牌、测试 realm、测试 CA、idp-mcp 集群内明文 http、沙箱 Docker 后端挂 Docker 套接字，都不能用于真实环境。
- gVisor、Kata 都是在 WSL2 嵌套虚拟化上测的，真实云节点的行为和性能可能不同；`systrap` 的问题没有查到根因。
- PVC 容量在 kind 上不被执行；出口代理只有 Docker 后端有；EER 的 Intelligence 没有部署在集群里（PATH C 从宿主运行）。
- 平台 CI 不跑整平台的集群验收（只有轻量 kind 版和静态检查），整平台验收目前只在本机。
