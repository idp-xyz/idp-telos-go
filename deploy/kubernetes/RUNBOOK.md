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
| 本机已有（`idp-*:dev`、`eer-runtime:dev`、postgres、keycloak、`local/temporal:1.10.0`） | `load_image` |
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

PostgreSQL 的数据卷是 PVC `postgres-data`（5Gi）。`backup.yaml` 的 CronJob `pg-backup` 每小时（`17 * * * *`）做五件事（镜像 `deploy/images/pg-backup`：PostgreSQL 客户端工具 + `age` + `curl`）：

1. 每个库 `pg_dump -Fc`，全局对象 `pg_dumpall --globals-only`，先用 `pg_restore -l` 确认能读；
2. 用 ConfigMap `backup-recipients` 里的**公钥**逐个文件 `age` 加密，明文只在 `emptyDir` 里短暂存在；**私钥从不进集群**，`full.sh` 把它放在 `.local/kind-full/backup-identity.txt`；
3. 写 `MANIFEST.sha256`（算在密文上，没有私钥也能校验），本地卷保留最近 7 份；
4. 推到 S3 兼容存储（Secret `backup-target`；kind 里是宿主机上的 RustFS 容器 `idp-kind-backup-target`，端口 `<kind 网关>:19000`，凭据每次 `full.sh` 重新生成），**最后传清单**，没有清单的集合视为不完整；传完把清单读回来比对；远端保留最近 30 份；
5. 任何一步失败，Job 就失败，缺备份告警（`deploy/observability/backup-alerts.yaml`）会看到。

`NODE_RESTART=1 bash tests/kind/backup-drill.sh`（约 2 分钟，要先有 `full.sh` 起的集群）：写一条标记记忆 → 备份（检查卷上和桶里都只有 `.age`，桶里的对象带 age 头）→ 重启 PostgreSQL Pod → 重启节点 → `DROP DATABASE idp_cmi` → **从桶里下载、校验清单、用私钥解密、恢复**（私钥只在这个 Job 期间作为 Secret 存在，用完删除；`DRILL_SOURCE=local` 改从本地卷恢复）→ 确认 CMI 又能服务、bob 仍读到标记记忆。不设 `NODE_RESTART` 就跳过节点重启。

备份目标是 RustFS（`docker.io/rustfs/rustfs:1.0.4-glibc`，Apache-2.0，按摘要固定，Barman Cloud 插件自己的测试用的也是这个构建）：MinIO 社区版已停止维护、不再发布镜像，原来固定的 MinIO 镜像在 quay.io 上已拉不到（CI 一直扫不到它；本机那份 2025-09 的镜像有 103 个已知漏洞，不会再修）。Docker 里没有时 `full.sh` 用 crane 按摘要拉取再打标签，控制台关闭。

不起集群也能测镜像：`bash deploy/images/pg-backup/test.sh`（只要 Docker、PostgreSQL、RustFS 镜像）：没有公钥拒绝备份、桶里无明文、错私钥恢复失败且不建库、被篡改的对象被清单拦下、不完整集合被跳过、远端保留。

范围：数据在 Pod 重启和节点重启后保留；**重跑 `full.sh` 会重建命名空间，数据随之清空**，密钥与备份密钥对、RustFS 凭据也每次重新生成。宿主机上的 RustFS 在集群之外，但与集群在同一台机器上，**防得住集群丢失，防不住机器丢失**；生产要用异地的对象存储（AWS S3、R2、OCI 等，改 `backup-target` 即可）、把私钥放进密钥管理或离线保存，并用高可用 PostgreSQL 做主库。

## 数据库连接加密（TLS）

PostgreSQL 开启 TLS（1.2 及以上），服务端证书由 `.local/kind-ca` 的测试 CA 签发（SAN 含 `postgresql.databases.svc.cluster.local` 等），放在 Secret `postgres-tls`；`postgres-hba` 里的 `pg_hba.conf` 只允许 Pod 内的 unix socket 免密码，**所有 TCP 连接必须 TLS + scram-sha-256，没有加密的一律拒绝**。组件的连接串都是 `sslmode=verify-full`（校验证书和主机名），CA 通过每个命名空间里的 ConfigMap `pg-ca` 挂到 `/etc/idp/pg-ca/ca.crt`，`PGSSLROOTCERT` 指向它（libpq、pgx、asyncpg 都读这个变量；`full.sh` 在渲染时给组件命名空间里所有 Deployment、Job、CronJob 加上）。备份和恢复 Job 也走校验过的 TLS。`full.sh` 末尾检查：所有来自组件的连接都是加密的（TLS 1.2 或 1.3），并且一条明文 TCP 连接被拒绝。

`kubectl exec` 进 PostgreSQL 时要写 `-c postgres`（Pod 里还有一个复制私钥用的初始化容器，否则 kubectl 会在标准错误里打"Defaulted container"）。

生产要求（这里没有、也不能在 kind 里演示）：服务端证书由企业 CA 签发并自动轮换（这里 30 天，`full.sh` 每次重发）；**静态加密靠存储层**——云盘的 KMS 加密或 LUKS，数据库本身不加密落盘数据；备份已经加密（上一节）；服务之间的 HTTP 调用（Workspace 到 HOST、HOST 到 CMI 等）在 kind 里仍是明文，用服务网格或各组件自己的 TLS 配置解决。compose 版本仍是 `sslmode=disable`，只用于开发。

## 镜像漏洞扫描（trivy）

`bash tools/scan_images.sh` 用固定版本的 trivy（`ghcr.io/aquasecurity/trivy:0.76.2`，在 Docker 里运行，经 Docker 套接字读本机的镜像）扫平台的镜像，只报有修复版本的 HIGH / CRITICAL，每个镜像一行，最后一行是数据库的日期。`--own` 只扫我们自己构建的镜像，加 `STRICT=1` 时有发现或没扫成就返回 1（平台 CI 用它卡住发布）；`--third-party` 只报告第三方镜像（我们改不了）；也可以直接给镜像名。没扫成的那行写着 trivy 的原因。

trivy 要两个数据库，放在 `TRIVY_CACHE`（默认 `.local/trivy-cache`，已忽略）：

| 库 | 目录 | 下载 / 解开后 | 谁用 | 到期 |
|---|---|---|---|---|
| 漏洞库 | `db/` | 133 MB / 1.6 GB | 每个镜像 | 1 天 |
| Java 索引 | `java-db/` | 951 MB / 1.5 GB | 带 jar 的镜像：没有它 Keycloak 扫不了（`Unable to initialize the Java DB`） | 3 天 |

联网时 trivy 用到哪个库就下载哪个，到期自己更新，脚本把 shell 的 `HTTPS_PROXY` 传给 trivy 的容器。参考主机的代理慢（2026-11-10 实测 60–120 KB/s）时，下载会撞上 trivy 默认 5 分钟的超时，那行写 `… context deadline exceeded`（加 `--timeout 60m` 后漏洞库下载了 23 分钟，Java 索引按这个速度要三个多小时）。这时在网络好的机器上下载、打包，拷过来离线扫：

```bash
# 网络好的机器：Docker 和上面的 trivy 镜像；在代理后面给两条 docker run 加 -e HTTPS_PROXY=$HTTPS_PROXY --network host，网络慢再加 --timeout 60m
mkdir -p trivy-offline && chmod 777 trivy-offline
docker run --rm -v "$PWD/trivy-offline:/root/.cache/trivy" ghcr.io/aquasecurity/trivy:0.76.2 image --download-db-only
docker run --rm -v "$PWD/trivy-offline:/root/.cache/trivy" ghcr.io/aquasecurity/trivy:0.76.2 image --download-java-db-only
tar -C trivy-offline -czf trivy-db.tar.gz db java-db           # 约 1.1 GB，打包约 1 分钟

# 要扫的机器（要扫的镜像先放进 Docker：crane pull IMG f.tar && docker load -i f.tar）
mkdir -p .local/trivy-cache && tar -C .local/trivy-cache -xzf trivy-db.tar.gz   # 约 20 秒
TRIVY_OFFLINE=1 bash tools/scan_images.sh
```

`TRIVY_OFFLINE=1`：两个库缺一个就 `BLOCKED` 退出（退出码 2）；不更新库、不查远端、不检查新版本，trivy 的容器不接网络（`--network none`），所以不会卡在网络上，也确实没有用网络；不拉镜像，Docker 里没有的那行写 `not in Docker`（`STRICT=1` 时算失败）。实测（2026-11-10，库从参考主机的缓存打包、在空目录解开）：同一批镜像离线和联网的结果一样（python:3.12-slim 4、operator 6、Keycloak 1、`idp-postgres-ha` 0），5 个镜像 17 秒。

**库会过期。** 离线的库不再更新，之后公布的漏洞扫不出来，"0" 只说明库里没有。报告最后一行写两个库的日期，超过 7 天的会加粗提醒（`**10 days old**: findings published since are not seen`）；离线用的库至少每周换一次。CI 每次都下载新库，没有这个问题。

## 高可用 PostgreSQL（可选）

`PG_HA=1 bash tests/kind/full.sh` 把单个 PostgreSQL 换成 CloudNativePG 集群（3 个实例，同步复制，自动故障切换），组件和备份任务连的还是名叫 `postgresql` 的服务，它始终指向当前主库，连接串不变。设计、测量结果、没有覆盖的东西见 `deploy/kubernetes/ha-postgres/README.md`。

- `bash tests/kind/ha-postgres.sh`：集群单独跑一遍，杀主库时让一个写入程序持续写；
- `bash tests/kind/failover-drill.sh`：整平台跑在集群上，杀主库时 CMI 持续写入，检查 CMI 自己恢复（Pod 不重启）、超时内重新确认写入、没有被确认的记录丢失、所有客户端连接仍是 TLS；
- 测得（kind，单节点）：主库被强制删除后新主库接管，写入被拒绝约 26 到 29 秒，被确认的写入 0 丢失，旧主库自己变成备库；
- operator、Barman Cloud 插件、cert-manager 与 PostgreSQL 镜像都要先用 crane 放进 Docker 守护进程（`ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1`、`ghcr.io/cloudnative-pg/plugin-barman-cloud:v0.16.0`、`ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.16.0`、`quay.io/jetstack/cert-manager-{controller,webhook,cainjector}:v1.21.3`、`ghcr.io/cloudnative-pg/postgresql:17.11-standard-trixie`），没有的话 `full.sh` 会用 crane 直接装进节点，再构建带 pgvector 的 `idp-postgres-ha:17-dev` 并加载进节点；
- 按时间点恢复：集群通过 Barman Cloud 插件（每个实例里的 sidecar）持续把 WAL 归档到对象存储（至少每 60 秒一次）并每天做基础备份；`bash tests/kind/pitr-drill.sh` 演示“误 DELETE 了全部数据”后，用 `recoveryTarget.targetTime` 在新集群里恢复到误操作前一刻（50 行回来、之后的 20 行和 DELETE 不重放）。插件需要 cert-manager（只给插件和 operator 之间的 gRPC 签自签证书），两者的命名空间都是默认拒绝。归档由对象存储做服务端加密（`AES256`；kind 上 RustFS 的主密钥由脚本生成，生产用 `aws:kms` 和自己的密钥）。CloudNativePG 1.32 移除内置 barman，从内置 barman 迁到插件的步骤和在 kind 上测得的结果见 `deploy/kubernetes/ha-postgres/README.md`；
- `ROLLOUT=1 bash tests/kind/failover-drill.sh`：CMI 持续写入时滚动重启整个集群（换镜像、改配置、加插件都是这样），检查最长一段写入被拒绝的时间和没有丢失已确认的写入；集群用 `primaryUpdateMethod: switchover`，主库先切到已更新的备库再重建；
- HA 模式下 `kubectl exec` 要进当前主库：`kubectl -n databases get cluster postgresql -o jsonpath='{.status.currentPrimary}'`，容器仍写 `-c postgres`。
- 生产的调度（`cluster.yaml`）：同一节点上绝不放两个实例（节点上的反亲和性 `required`），并按区分散（`topologySpreadConstraints`，`DoNotSchedule`）；节点没有 `topology.kubernetes.io/zone` 标签时集群不会启动。单节点的 kind 上 `tools/ha_render.py` 合并 `single-node.yaml` 放宽这两条，并会打印 `DEV: ...` 说明。注意：把反亲和性的 `topologyKey` 设成区，在没有区标签的节点上 3 个实例会全挤在一个节点上，集群还显示 3 个就绪（多节点实测）；
- operator 与 Barman Cloud 插件各跑 2 个副本，且不在同一节点（`operator-ha.yaml`、`tools/ha_plugin_manifest.py`）：故障切换由 operator 决定，插件不可达时 operator 完全不协调用它的集群，所以任一个只有一个副本、又和主库在同一节点上时，丢掉那个节点就没有故障切换（实测超过 4 分钟，集群状态一直显示“健康”）。监控要盯 operator 与插件的可用性；
- 多节点：`bash tests/kind/multinode-cluster.sh` 建第二个 kind 集群 `ha`（1 个控制面 + 3 个 worker，分属 zone-a/b/c，Calico；不改 kubectl 当前上下文）；`KIND_CLUSTER=ha ARCHIVE=1 bash tests/kind/ha-postgres.sh` 按生产方式部署（分散、两个副本、WAL 归档到宿主机上的 RustFS `idp-kind-ha-s3`）；`bash tests/kind/ha-maintenance.sh` 在写入时依次：排空主库所在节点（先切换主库，写入最多被拒 6–8 秒）、再排空第二个节点（被 PDB 拒绝）、恢复、停掉主库 + operator 主副本 + 一个插件副本所在的节点（另一个 operator 副本接管，47 秒后提升新主库，写入被拒约 49 秒），全程已确认的写入 0 丢失。删除：`kind delete cluster --name ha; docker rm -f idp-kind-ha-s3`；
- 整平台上多节点：`KIND_CLUSTER=ha PG_HA=1 SKIP_BUILD=1 bash tests/kind/full.sh`（PATH A–H 通过；它会占用主集群的备份目标容器、本地端口和 `.local/kind-full`，做完要在主集群上重跑一次 `full.sh`）；`NODELOSS=1 KIND_CLUSTER=ha bash tests/kind/failover-drill.sh` 在 CMI 写入时停掉主库所在节点（实测写入被拒约 61 秒，已确认的写入 0 丢失）。多节点上 API server 调用别的节点上的 webhook 时源地址是控制面在 Pod 网络上的地址（Calico 隧道地址），`platform.yaml` 的两条 webhook 规则因此同时放行两个地址；Keycloak、Temporal、idp-mcp、沙箱都是单副本，所在节点丢失后要等约 5 分钟才被迁走，这段时间平台的表现没有测；
- `.github/workflows/ha.yml`：每天定时（也可手动触发）在 GitHub 上建 4 节点 kind 集群，跑 `ha-postgres.sh`（`ARCHIVE=1`）和 `ha-maintenance.sh`；告警规则在 `deploy/observability/ha-postgres-alerts.yaml`（operator 或插件无副本或只剩一个、两个副本同节点、有实例不在时备库 PDB 仍允许驱逐），CI 里用 promtool 测；
- 节点维护的步骤：一次只动一个节点——排空、维护、`uncordon`、等到 `Cluster in healthy state` 再动下一个。CloudNativePG 的 PDB 按角色选 Pod，切换主库后备库那个 PDB 的状态会一直过期（实测 40 秒以上仍显示还能再驱逐 1 个），这时再排空一个节点会把最后一个备库也驱逐掉，主库随之无法写入。必须在有实例不在时再排空，先 `kubectl -n databases annotate pdb postgresql recheck=$(date +%s) --overwrite`，确认 `kubectl -n databases get pdb postgresql` 的 ALLOWED 是 0。不要自己再加一个覆盖实例的 PDB：一个 Pod 有两个 PDB 时驱逐接口直接拒绝，所有排空都会失败；

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
| 机器重启后两个 kind 集群的 Pod 全是 `Unknown`，kube-proxy `CrashLoopBackOff`：`too many open files` | 5 个节点容器共用宿主机的 inotify 上限（默认 128 个实例） | `docker exec kind-control-plane sysctl -w fs.inotify.max_user_instances=8192`，再删掉 kube-proxy 和 calico-node 的 Pod 让它们重建；`multinode-cluster.sh` 建集群时会调高，但重启后要重新执行 |
| `scan_images.sh` 一行 `not scanned`：`… context deadline exceeded` | 代理慢，trivy 下载数据库超过默认 5 分钟 | 离线扫（"镜像漏洞扫描"） |
| `not scanned`：`… Unable to initialize the Java DB … cannot be specified on the first run` | 跳过更新时缺 Java 索引，扫到 Keycloak 这类带 jar 的镜像 | 把 `java-db/` 一起打包解开；`TRIVY_OFFLINE=1` 会先检查两个库 |
| `gh` 命令 `EOF` / CI 克隆 `403` | GitHub 偶发限流 | 稍后重试；`gh run rerun --failed` |

## 停止与清理

```bash
docker stop kind-control-plane                    # 停集群，保留（docker start 恢复）
TEARDOWN=1 SKIP_BUILD=1 bash tests/kind/full.sh   # 跑完后删掉平台命名空间
kind delete cluster --name kind                   # 彻底删除
docker rm -f sbx-dind sbx-dind2                   # gVisor 的 docker-in-docker（脚本会自己清理）
```

## 限制

- 开发验证环境：开发令牌、测试 realm、测试 CA、idp-mcp 集群内明文 http（数据库连接已加密，服务之间的 HTTP 没有）、沙箱 Docker 后端挂 Docker 套接字，都不能用于真实环境。
- gVisor、Kata 都是在 WSL2 嵌套虚拟化上测的，真实云节点的行为和性能可能不同；`systrap` 的问题没有查到根因。
- PVC 容量在 kind 上不被执行；出口代理只有 Docker 后端有；EER 的 Intelligence 没有部署在集群里（PATH C 从宿主运行）。
- 平台 CI 不跑整平台的集群验收（只有轻量 kind 版和静态检查），整平台验收目前只在本机。
