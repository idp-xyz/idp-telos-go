# 本机 Kubernetes 测试环境手册

WSL 里的 kind 集群（Calico）、整套平台在集群上的部署与验收、沙箱的 Docker / Kubernetes 后端，以及 gVisor 与 Kata 两种加固运行时。命令都在 2026-11-06 于参考主机上**从一个干净的环境（删掉集群、工具和镜像）按本手册从头执行过一遍**，下面的耗时是那次的实测。沙箱本身的运维见 `idp-sandbox/docs/RUNBOOK.md`；compose 版平台见本仓库 README。更完整的带图版本是本地的 `docs/reports/IDP_TELOS_Kubernetes_Test_Environment_Runbook_2026-11-06.html`（`docs/` 不入库）。

## 速查

| 项 | 值 |
|---|---|
| 集群 / 上下文 | `kind` / `kind-kind`，节点容器 `kind-control-plane`；kind v0.31，Kubernetes v1.35，Calico v3.30.3 |
| 工具 | `~/.local/bin`：`kind`、`kubectl`、`crane`（无需 root） |
| 命名空间 | `databases keycloak temporal idp-cmi idp-host idp-workspace idp-eer idp-mcp idp-sandbox idp-sandbox-runs idp-web idp-mcp-bridge`，以及 `full.sh` 为 PATH O 加的测试第三方 `thirdparty`，还有 `full.sh` 为 PATH L、M、N 加的测试源站（M4 也用 `web-origin`） `egress-origin`、`web-origin`、`web-attacker` |
| 运行时类 | `gvisor`（runsc 20260126.0，kvm 平台）、`kata`（4.3.1，Go 运行时 + Cloud Hypervisor） |
| 端口转发（full.sh） | Keycloak 17443（https）· Workspace 17560 · HOST 17450 · CMI 17550 · EER 17180 · idp-mcp 注册服务 17250 · 网关 17320 · 助手页 17561 · 沙箱 17570（PATH D 的未签名调用）· idp-web 17580（PATH M 的未签名调用）· 测试第三方 17590（PATH O 的控制与授权页） |

```bash
kubectl get nodes; docker start kind-control-plane            # 重启后恢复集群
bash tests/kind/full.sh                                       # 整平台 PATH A–O；镜像已在时加 SKIP_BUILD=1；另一个集群与主集群并存时加 KIND_CLUSTER=名字 KIND_BACKUP_TARGET=名字 KIND_BACKUP_PORT=端口（脚本会重建备份目标容器）；SANDBOX_RUNTIME_CLASS=gvisor|kata；MODEL=live；TEARDOWN=1
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

## 整平台（PATH A–O）

`SKIP_BUILD=1 bash tests/kind/full.sh`：先删平台的十七个命名空间再重建（每次都是全新的平台）；生成本次的 CA、realm、密钥到 `.local/kind-full`（已忽略）；用后台端口转发跑与 compose 版相同的验收，成功时每条路径一行 `PASS: PATH …`（还有 PostgreSQL TLS 与 pgvector 两行）。

- **PATH A**：bob（Keycloak）→ Workspace 换令牌 → HOST 运行 → CMI 上下文包 → bob 的收件箱。
- **PATH B**：operator 提交 T2 意图 → EER → bob 的收件箱 → 经 Workspace 批准 → EER 完成，审计里批准人是 bob。
- **PATH D**：bob 运行 Python → HOST → idp-mcp 网关（HOST 为 bob 换来的令牌）→ 沙箱（Kubernetes 后端，转发调用者）→ `answer 42` 回到收件箱；网关记录里 subject 是 bob、client 是 idp-host。沙箱的启动日志写着 `callers must be signed by the gateway`；再经端口转发（不经沙箱的网络策略）直连沙箱：不带网关签名的 `tools/list` 仍能回答，冒用 bob 的 `tools/call` 被拒绝（403，沙箱日志 `caller assertion refused`）。见下面"网关对调用者的签名"。
- **PATH E**：bob 对记忆助手说一句话 → HOST 把这次对话作为情景记忆写进 CMI（作用域是 bob）→ 新对话的上下文包里取回它 → 同一对话保留历史 → alice 看不到。
- **PATH I**：bob 让"谨慎的代码助手"跑代码 → idp-host 停下运行等他确认 → Workspace 把等待中的调用与它将要执行的代码给他看（管理员 alice 对这次运行与决定都是 404）→ bob 确认后代码执行、输出进收件箱；同一对话里第二次点拒绝，代码不执行。壳的"对话"页走的就是这条路径。
- **PATH L**（compose 版也有，检查脚本是同一个 `deploy/compose/egress_check.py`）：bob 的令牌按 HOST 为运行换令牌的方式换成网关的令牌 → idp-mcp 网关 → 沙箱 → 带 `network=egress` 的运行经出口代理访问到他租户名单上的主机（集群里的测试源站），名单外的主机被代理拒绝（403），绕过代理直连不通；不带 `network=egress` 的运行环境里没有代理、也连不到代理。代理的日志里是 bob 的租户。见下面"沙箱的出口代理"。
- **PATH M**（compose 版也有，记录由同一个 `deploy/compose/web_check.py` 读）：bob 问 web-assistant 一个只有测试源站的文章答得出的问题 → HOST 的 agent 经网关搜索（idp-web，搜索服务是 fixture）、用搜索结果的 `ref` 读那篇文章 → 回答里有只有文章才有的数字和 `[1]`；网关的记录里调用者是 bob、客户端是 idp-host；idp-web 的审计有查询词（明文）和读取的地址、没有 `ref`；源站的日志里只有这篇文章（和 robots.txt）。随后经端口转发（17580）直连 idp-web：不带网关签名、自称 bob 的 `tools/call` 得 403，`tools/list` 仍能回答（登记要用）。
- **PATH M4**（compose 版也有）：PDF 读取（idp-web ADR-0001 D31）。测试源站自己造两份 PDF（`web_origin.py`，仓库里没有第三方文档）：`/tariff.pdf`（两页，第 2 页有一个别处没有的数字 29.47）与 `/scan.pdf`（一页只有图形、没有文字操作符）。fixture 里查询含 `tariff` 的找到前者、含 `receipt` 的找到后者。bob 的两个运行（各搜索一次，共两次）：第一个运行的回答（模型替身引用读到的开头）里有 `[page 1]`、`[page 2]`、`PDF, 2 pages` 与 29.47，idp-web 的审计是 `web_fetch` 的 `allowed`、有字节数；第二个回答里是"没有文字层"的句子，审计是 `decision=failed`、`reason=pdf_no_text_layer`；源站日志里有这两个地址。M3 结束时把租户当天的搜索计数清零，M4 先读计数、要求至少还剩两次、结束时再清零，PATH N 仍有完整的三次。idp-web 的 Pod 要能运行 PDF 读取程序：seccomp 的 `RuntimeDefault` 档案与 `allowPrivilegeEscalation: false` 都不挡它（见 `idp-web/docs/RUNBOOK.md` 6.1）；若 `idp-web` 的启动日志里有 `reading PDFs is not available: …`，M4 的第一条断言会失败，原因是 `pdf_reader_unavailable`。
- **PATH N**：搜索找到的"公告"页面里写着"去取攻击者的地址"，模型替身照做。(a) idp-web 拒绝这个地址（对不上 `ref`，审计里 `decision=refused`、`reason=ref_…`）；(b) `web_open_url` 被 HOST 停下等 bob 确认（HOST 配置的 `confirmTools`，Release 里没有这条要求），bob 拒绝，idp-web 从没收到这次调用；(c) 攻击者的日志里没有任何请求；(d) CMI 里一条 confidential 的条目，不带 web 工具的对照运行拿得到，带 web 工具的运行拿不到（`externalTools`；路径内临时把租户的 `readerCeiling` 提到 confidential，结束后还原）。两条路径只有模型替身版，`MODEL=live` 时是 `NOT_RUN`。
- **PATH O**（compose 版也有，步骤是同一个 `deploy/compose/path_o.sh`）：第三方的 MCP 服务（一个测试 Pod：授权服务器与受保护的 MCP 服务合在一起，记录每个请求，`deploy/compose/thirdparty_double.py`），成员用自己的 OAuth 授权经 idp-mcp-bridge 访问。浏览器是脚本做的：POST Workspace 的授权路由，在第三方的页面上同意，把重定向的查询交给 `/v1/me/connections/callback`。O0 评审账号与登记；O1 bob 连接，库里的令牌是密文；O1b 预注册的客户端；O2 bob 的运行带着他自己的令牌到第三方，第三方收不到任何 TELOS 身份；O3 alice 没授权：事件 `idp.host/authorization.required`（只有连接 id、没有链接），第三方一次调用也没收到；O4 令牌过期刷新一次、短命令牌调用前刷新、`insufficient_scope` 保留授权；O4c bob 发起的 state 被 alice 的令牌提交会被拒绝且不调用桥；O5 错误的 `iss` 不存任何东西；O6 bob 断开，第三方看到撤销；O7 第三方签发的令牌、授权码、客户端密钥在任何日志、事件、数据库里都找不到；O8 探测 Pod 证明只有网关、注册服务、Workspace 到得了桥的端口。`MODEL=live` 时 NOT_RUN。
- **PATH F**：bob 经 Workspace（用他自己的令牌换 CMI 的令牌）列出并删除自己的记忆，alice 删不了；删除后列表和检索里都没有。
- **PATH C**：EER Intelligence 的集成测试，在宿主上用 uv 对集群里的 CMI 运行（`env -i` 干净环境）。
- 之后装好"助手"应用（`deploy/apps/assistant`）：两个 agent 的聊天页，页面由 `idp-workspace` 命名空间里的静态服务提供（`deploy/kubernetes/full/assistant.yaml`）。
- `MODEL=live`：两个 agent 用本机模型；PATH A 另查回答引用了 CMI 里的规定，PATH D 查回答报告了代码的输出（工具确实被调用由网关的记录证明）。

轻量版（只有 CMI、HOST、Workspace，开发令牌，无 Keycloak；GitHub 上的 kind 工作流跑的就是它）：`KIND_CLUSTER=kind SKIP_BUILD=1 LOAD_BASE_IMAGES=1 bash tests/kind/smoke.sh`。静态检查（不需要集群）：`uv run --no-project --with PyYAML==6.0.3 python -I tools/k8s_check.py --full`——11 个命名空间都是 restricted 且默认拒绝，20 条流量在两端都被放行。

## 沙箱的出口代理

`deploy/kubernetes/full` 用的是沙箱的 `idp-sandbox/deploy/kubernetes-egress`（沙箱自己的清单加出口代理，idp-sandbox ADR-0001 D21、D22）：`idp-sandbox` 命名空间里多一个 `idp-sandbox-egress`（Deployment 与 Service，3128），`sandbox_run` 多一个可选参数 `network=egress`。带这个参数的运行只能连到代理，代理按网关转发来的租户查名单；不带的运行和以前一样没有任何网络。清单里的名单是沙箱自带的空名单，谁也出不去。

`full.sh` 为它做四件事：

- 构建并加载 `idp-sandbox-egress:dev`（compose 验收也构建它）；
- 建密钥 `idp-sandbox-egress-key`（沙箱服务用它给运行签凭据，代理用它验），每次运行新生成；
- 把名单换成本次运行的：本次的租户可以访问 `origin.egress-origin.svc.cluster.local`，其他人为空（`.local/kind-full/overlay/tenants.yaml`）；
- 加一个集群内的测试源站（`tests/kind/egress-origin.yaml`，命名空间 `egress-origin`），并给代理设 `IDP_EGRESS_ALLOW_PRIVATE=true`、加一条只通向这个源站的 NetworkPolicy——源站的地址是私有地址，代理默认拒绝。**这两项只属于测试**，真实部署不设。

在跑着的集群上改名单（`full.sh` 下次运行会重写）：

```bash
CM="$(kubectl -n idp-sandbox get deploy idp-sandbox-egress -o jsonpath='{.spec.template.spec.volumes[?(@.name=="tenants")].configMap.name}')"
kubectl -n idp-sandbox edit configmap "$CM"                      # tenants.yaml：default 与 tenants.<租户 id>，格式见 idp-sandbox/deploy/kubernetes-egress/tenants.yaml
kubectl -n idp-sandbox rollout restart deploy/idp-sandbox-egress   # 代理只在启动时读名单
kubectl -n idp-sandbox logs deploy/idp-sandbox-egress              # 每个请求一行：decision、host、tenant
```

租户 id 在 `.local/kind-full/overlay/tenants.yaml` 里。名单上的公网主机还要节点本身能连到；代理的 NetworkPolicy 只放行公网地址的 443 和 80。单独再验一次这条路径（转发开着时）：`python3 -I deploy/compose/egress_check.py`，参数见文件头。

## idp-web（联网检索）

`deploy/kubernetes/full` 原样包含 idp-web 自己的清单（`idp-web/deploy/kubernetes`，运维见 `idp-web/docs/RUNBOOK.md`）：命名空间 `idp-web` 里一个 Deployment（一个副本）与 Service（8580），默认拒绝，出口只到公网地址的 443、80（减去服务自己拒绝的全部 IPv4 段）。**发布的清单里没有任何租户、没有搜索服务**；验收需要的东西全在 `full.sh` 每次渲染的覆盖层（`.local/kind-full/overlay`）和它建的对象里，发布的清单里没有：

- 构建并加载 `idp-web:dev`（compose 验收也构建它）；
- 数据库（idp-web ADR-0001 D28 到 D30）：PostgreSQL 里的库 `idp_web`；Secret `idp-web-database`（`service-url`，Deployment 以文件挂载）与 `idp-web-database-owner`（`owner-url`、`app-password`，只有 Job 用），每次新生成密码；迁移由 idp-web 自己的 Job `idp-web-migrate` 做，服务角色由 `full/setup-jobs.yaml` 的 `kind-login`（`idp-web ensure-login idp_web_svc`）建，之后 `full.sh` 重启 idp-web；TLS 与其余组件相同（`pg-ca` 与 `PGSSLROOTCERT`，`sslmode=verify-full`）；`databases` 的 NetworkPolicy 放 `idp-web` 命名空间进来；
- 网关的公钥（和沙箱用的是同一把，ConfigMap `idp-web-gateway-caller-keys`）、`ref` 密钥（Secret `idp-web-ref-key`，每次新生成）、fixture 搜索结果（ConfigMap `idp-web-fixture`，地址是本次运行的测试源站）；
- 租户文件：本次的租户启用，且 `openUrl: true`（让 HOST 少了确认时一个地址就能漏到测试源站）；其他人不可用（`overlay/web-tenants.yaml`），并设 `dailySearches: 3`：PATH M3 用它验证每日配额（M 搜一次，M3 搜剩下的两次后第三次被拒绝，并把计数清零，让 PATH N 有自己的搜索）；
- 搜索服务设为 `fixture`，`IDP_WEB_TEST_ORIGINS` 设为两个测试源站的 `主机:80`（**只属于测试**，发布的清单里没有它，`idp-web/tests/deploy` 断言这一点）；
- 两个测试源站各在自己的命名空间（`tests/kind/web-origin.yaml`）：`web-origin` 里的是文章与"公告"页，`web-attacker` 里的是公告里让读者去取的地址；脚本是 compose 验收用的 `deploy/compose/web_origin.py`（放进 ConfigMap `web-origin-script`）。两个命名空间都是 restricted、默认拒绝，只放 idp-web 的 Pod 进来；
- 一条**测试专用**的 NetworkPolicy `idp-web-test-origins`（在 `idp-web` 命名空间，也在 `web-origin.yaml` 里）：测试源站在集群里，地址是私有地址，发布的出口规则**正确地**把它们排除了，只有这一条放它们进来。去掉它，PATH M 就失败（这是它在起作用的证明，P2 任务做过）；
- 在 idp-mcp 里登记 `web`（`mcp_setup.py` 的第 8 个参数，端点是 Service 的地址，要与 idp-web 的 `IDP_WEB_CALLER_AUDIENCE` 逐字相同：`http://idp-web.idp-web.svc.cluster.local:8580/mcp`）；注册服务的 `IDP_ALLOWED_UPSTREAM_HOSTS` / `IDP_PRIVATE_UPSTREAM_HOSTS` 在 `full/kustomization.yaml` 里含这个主机；web 工具只授给 HOST 令牌带的角色 `host-user`；
- HOST 的配置（`full/kustomization.yaml`）里 `externalTools: ["web__*"]` 与 `confirmTools: ["web__web_open_url"]`。

idp-web 的入口策略自己放行网关和注册服务（注册服务要发现工具），所以平台的覆盖层不像沙箱那样需要一条 `sandbox-registry`。

在跑着的集群上改某个租户的名单（`full.sh` 下次运行会重写）：

```bash
kubectl -n idp-web get deploy idp-web -o jsonpath='{.spec.template.spec.volumes[?(@.name=="tenants")].configMap.name}'   # 生成的 ConfigMap 名
kubectl -n idp-web edit configmap <上面的名字>                      # tenants.yaml：default 与 tenants.<租户 id>：enabled、includeDomains、excludeDomains、openUrl
kubectl -n idp-web rollout restart deploy/idp-web                   # 服务只在启动时读一次
kubectl -n idp-web logs deploy/idp-web --tail=-1 | grep '"msg":"audit"'   # 审计行：tool、decision、reason、query、url、没有 ref
```

租户 id 在 `.local/kind-full/overlay/web-tenants.yaml` 里。审计行在 `kubectl -n idp-web logs deploy/idp-web`，两个测试源站的访问日志在 `kubectl -n web-origin logs deploy/origin` 与 `kubectl -n web-attacker logs deploy/attacker`（每个请求一行 JSON；攻击者的日志里应当一行也没有）。镜像是 scratch，没有 shell，进不去 Pod；看日志与 `/metrics`（运维端口 9580：`kubectl -n idp-web port-forward deploy/idp-web 9580`；网络策略只放 `observability` 命名空间抓取，端口转发不受它限制）。

## 网关对调用者的签名

网关转发调用者时可以签名，沙箱可以要求这个签名（idp-mcp ADR-0001 D44、idp-sandbox ADR-0001 D18）。没有它，凡是能连上沙箱 8570 端口的都能自称任何调用者（租户由调用者决定），网络策略就成了唯一的边界。`deploy/kubernetes/full` 的叠加层（`kustomization.yaml`）与 `full.sh` 把它配上了，两个组件自己的清单没改：

- `full.sh` 每次运行生成一对 P-256 密钥（`.local/kind-full/caller.key`、`caller.pub`，已忽略）：私钥进密钥 `idp-mcp/idp-mcp-gateway-caller-key`，作为文件挂到网关的 `/etc/idp/caller/caller.key`（`IDP_GATEWAY_CALLER_KEY_FILE`）；公钥进 ConfigMap `idp-sandbox/idp-sandbox-gateway-caller-keys`（公钥不是秘密），挂到沙箱的 `/etc/idp/gateway/caller.pub`（`IDP_SANDBOX_CALLER_KEYS_FILE`）；
- 沙箱的 `IDP_SANDBOX_CALLER_AUDIENCE` 与 `full.sh` 在 idp-mcp 里登记的端点逐字相同：`http://idp-sandbox.idp-sandbox.svc.cluster.local:8570/mcp`（改了其中一个，另一个要一起改）；
- 验收里的直连用 `kubectl port-forward`：沙箱的网络策略只放行网关与注册服务，策略保持原样；端口转发由 kubelet 在 Pod 自己的网络命名空间内连接，不经这条策略，所以不必为测试开口子。

真实部署里自己生成这对密钥（`openssl ecparam -name prime256v1 -genkey -noout -out caller.key && openssl ec -in caller.key -pubout -out caller.pub`），私钥放密钥、公钥放 ConfigMap，不要入库。**轮换**（沙箱的文件里可以放几把公钥）：

```bash
# 1. 新密钥对；把新公钥追加到沙箱的公钥文件（旧的保留），重启沙箱
openssl ecparam -name prime256v1 -genkey -noout -out new.key && openssl ec -in new.key -pubout -out new.pub
kubectl -n idp-sandbox get cm idp-sandbox-gateway-caller-keys -o jsonpath='{.data.caller\.pub}' > both.pub; cat new.pub >> both.pub
kubectl -n idp-sandbox create cm idp-sandbox-gateway-caller-keys --from-file=caller.pub=both.pub --dry-run=client -o yaml | kubectl apply -f -
kubectl -n idp-sandbox rollout restart deploy/idp-sandbox && kubectl -n idp-sandbox rollout status deploy/idp-sandbox
# 2. 换网关的私钥并重启网关（调用在重启间隙不会被拒：新旧公钥此时都被沙箱接受）
kubectl -n idp-mcp create secret generic idp-mcp-gateway-caller-key --from-file=caller.key=new.key --dry-run=client -o yaml | kubectl apply -f -
kubectl -n idp-mcp rollout restart deploy/idp-mcp-gateway-edge-1 && kubectl -n idp-mcp rollout status deploy/idp-mcp-gateway-edge-1
# 3. 确认经网关的调用正常后，把旧公钥从文件里删掉（只留 new.pub），再重启沙箱
```

沙箱只在启动时读公钥文件，所以改了 ConfigMap 要重启沙箱；沙箱的工作区在内存里，重启会丢掉进行中的工作区。

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

备份目标是 RustFS（`ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc`，Apache-2.0，按摘要固定，Barman Cloud 插件自己的测试用的也是这个构建）：MinIO 社区版已停止维护、不再发布镜像，原来固定的 MinIO 镜像在 quay.io 上已拉不到（CI 一直扫不到它；本机那份 2025-09 的镜像有 103 个已知漏洞，不会再修）。Docker 里没有时 `full.sh` 用 crane 按摘要拉取再打标签，控制台关闭。

不起集群也能测镜像：`bash deploy/images/pg-backup/test.sh`（只要 Docker、PostgreSQL、RustFS 镜像）：没有公钥拒绝备份、桶里无明文、错私钥恢复失败且不建库、被篡改的对象被清单拦下、不完整集合被跳过、远端保留。

范围：数据在 Pod 重启和节点重启后保留；**重跑 `full.sh` 会重建命名空间，数据随之清空**，密钥与备份密钥对、RustFS 凭据也每次重新生成。宿主机上的 RustFS 在集群之外，但与集群在同一台机器上，**防得住集群丢失，防不住机器丢失**；生产要用异地的对象存储（AWS S3、R2、OCI 等，改 `backup-target` 即可）、把私钥放进密钥管理或离线保存，并用高可用 PostgreSQL 做主库。

## 数据库连接加密（TLS）

PostgreSQL 开启 TLS（1.2 及以上），服务端证书由 `.local/kind-ca` 的测试 CA 签发（SAN 含 `postgresql.databases.svc.cluster.local` 等），放在 Secret `postgres-tls`；`postgres-hba` 里的 `pg_hba.conf` 只允许 Pod 内的 unix socket 免密码，**所有 TCP 连接必须 TLS + scram-sha-256，没有加密的一律拒绝**。组件的连接串都是 `sslmode=verify-full`（校验证书和主机名），CA 通过每个命名空间里的 ConfigMap `pg-ca` 挂到 `/etc/idp/pg-ca/ca.crt`，`PGSSLROOTCERT` 指向它（libpq、pgx、asyncpg 都读这个变量；`full.sh` 在渲染时给组件命名空间里所有 Deployment、Job、CronJob 加上）。备份和恢复 Job 也走校验过的 TLS。`full.sh` 末尾检查：所有来自组件的连接都是加密的（TLS 1.2 或 1.3），并且一条明文 TCP 连接被拒绝。

`kubectl exec` 进 PostgreSQL 时要写 `-c postgres`（Pod 里还有一个复制私钥用的初始化容器，否则 kubectl 会在标准错误里打"Defaulted container"）。

生产要求（这里没有、也不能在 kind 里演示）：服务端证书由企业 CA 签发并自动轮换（这里 30 天，`full.sh` 每次重发）；**静态加密靠存储层**——云盘的 KMS 加密或 LUKS，数据库本身不加密落盘数据；备份已经加密（上一节）；服务之间的 HTTP 调用（Workspace 到 HOST、HOST 到 CMI 等）在 kind 里仍是明文，用服务网格或各组件自己的 TLS 配置解决。compose 版本仍是 `sslmode=disable`，只用于开发。

## 镜像漏洞扫描（trivy）

`bash tools/scan_images.sh` 用固定版本的 trivy（`ghcr.io/idp-xyz/mirror:ghcr.io__aquasecurity__trivy__0.76.2`，在 Docker 里运行，经 Docker 套接字读本机的镜像）扫平台的镜像，只报有修复版本的 HIGH / CRITICAL，每个镜像一行，最后一行是数据库的日期。`--own` 只扫我们自己构建的镜像，加 `STRICT=1` 时有发现或没扫成就返回 1（平台 CI 用它卡住发布）；`--third-party` 只报告第三方镜像（我们改不了）；也可以直接给镜像名。没扫成的那行写着 trivy 的原因。

trivy 要两个数据库，放在 `TRIVY_CACHE`（默认 `.local/trivy-cache`，已忽略）：

| 库 | 目录 | 下载 / 解开后 | 谁用 | 到期 |
|---|---|---|---|---|
| 漏洞库 | `db/` | 133 MB / 1.6 GB | 每个镜像 | 1 天 |
| Java 索引 | `java-db/` | 951 MB / 1.5 GB | 带 jar 的镜像：没有它 Keycloak 扫不了（`Unable to initialize the Java DB`） | 3 天 |

联网时 trivy 用到哪个库就下载哪个，到期自己更新，脚本把 shell 的 `HTTPS_PROXY` 传给 trivy 的容器。参考主机的代理慢（2026-11-10 实测 60–120 KB/s）时，下载会撞上 trivy 默认 5 分钟的超时，那行写 `… context deadline exceeded`（加 `--timeout 60m` 后漏洞库下载了 23 分钟，Java 索引按这个速度要三个多小时）。这时在网络好的机器上下载、打包，拷过来离线扫：

```bash
# 网络好的机器：Docker 和上面的 trivy 镜像；在代理后面给两条 docker run 加 -e HTTPS_PROXY=$HTTPS_PROXY --network host，网络慢再加 --timeout 60m
mkdir -p trivy-offline && chmod 777 trivy-offline
docker run --rm -v "$PWD/trivy-offline:/root/.cache/trivy" ghcr.io/idp-xyz/mirror:ghcr.io__aquasecurity__trivy__0.76.2 image --download-db-only
docker run --rm -v "$PWD/trivy-offline:/root/.cache/trivy" ghcr.io/idp-xyz/mirror:ghcr.io__aquasecurity__trivy__0.76.2 image --download-java-db-only
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
- operator、Barman Cloud 插件、cert-manager 与 PostgreSQL 镜像都要先用 crane 放进 Docker 守护进程（`ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__cloudnative-pg__1.30.1`、`ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__plugin-barman-cloud__v0.16.0`、`ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__plugin-barman-cloud-sidecar__v0.16.0`、`quay.io/jetstack/cert-manager-{controller,webhook,cainjector}:v1.21.3`、`ghcr.io/idp-xyz/mirror:ghcr.io__cloudnative-pg__postgresql__17.11-standard-trixie`），没有的话 `full.sh` 会用 crane 直接装进节点，再构建带 pgvector 的 `idp-postgres-ha:17-dev` 并加载进节点；
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

## 升级须知（2026-11-19 的 BOM）

从 2026-11-17 的 BOM 升到 2026-11-19 的 BOM 时，下面几处行为会变。每条写明：变了什么、怎么看出来、怎么处理。compose 上的配置在 `deploy/compose/e2e.sh` 里是同一组键。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-host（D44） | **模型提供方没有声明 `external: false` 的，一律算企业之外。** 这样的部署里，敏感级别高于上限（`files.externalModelCeiling`，缺省 `internal`）的附件和 CMI 上下文条目，对所有运行都会扣下 | 运行的事件里出现 `idp.host/context.withheld`、`idp.host/attachment.withheld`；成员发现 agent "不记得"机密级别的记忆 | 自有的模型在 HOST 配置的提供方条目里写 `external: false`（`full/kustomization.yaml` 和 compose 的配置里都有例子）。确实是外部模型的，这是预期的行为：要么不给它机密数据，要么按租户调高上限 |
| idp-host（D44） | 网关配置里列进 `externalTools` 的工具（例如 `web__*`）也算企业之外：发布件允许其中任何一个，就按同一上限筛附件和上下文；`confirmTools` 里的工具一律要成员确认，不论发布件怎么写；发布件里的 `toolResults: off` 在这种运行里按 `wrap` 处理 | 同上的两个事件；`idp.host/confirmation.required` | 不需要处理。要给某个能联网的 agent 机密数据，只能把它的对外工具去掉 |
| idp-host（D45） | 对模型服务的可重试错误（408、429、5xx、连接失败）先对同一个模型重试，缺省共 3 次请求 | 事件 `idp.host/model.retry`；`model.usage` 里的 `attempts` | 不需要处理。要关掉就在提供方条目里写 `retry: {attempts: 1}` |
| idp-host（D46） | **模型的流 60 秒没有新内容就判为超时**，按可重试的错误处理。外部 harness worker 同样适用 | 步骤的 `error_code` 是 `idle_timeout`；重试用尽后运行以 `MODEL_ERROR` 失败 | 模型会排队，或长时间思考而不输出的，把提供方条目的 `idleTimeoutSeconds` 调大 |
| idp-host（D46） | 运行到期限或被取消时，进行中的模型调用和工具调用会被打断，约 1 秒内结束。被打断的工具调用记为失败，说明里写"可能已执行，也可能没有" | 运行以 `RUN_TIMEOUT` 或 `CANCELED` 结束得比以前快；`tool_calls` 里有 `interrupted:` 开头的失败 | 不需要处理。有副作用的工具被打断后，要人工核对它是否已经执行 |
| idp-mcp（D12） | 请求必须带 `Mcp-Method` 头；不带的在认证之前就得到 400（原来匿名请求是 401） | 自写的客户端或探活脚本收到 400，错误码 -32020 | 客户端补上这个头，值等于请求体里的 `method` |
| idp-mcp（D8） | 上游地址的规则收紧：IANA 登记为"不可全球路由"的地址段都不算公网（NAT64、6to4、Teredo、240/4、198.18/15 等） | 登记上游时被拒绝，或原有的上游变为不可达 | 这类地址上的上游要列进注册服务的允许名单（`IDP_ALLOWED_UPSTREAM_HOSTS`），与集群内的上游一样 |
| idp-sandbox（D22） | 出口代理的拒绝名单按同一张表补齐 | 沙箱里访问这些地址段被代理拒绝 | 不需要处理 |
| idp-web（新组件） | 新增组件。三个工具默认不授给任何角色 | — | 开启前先读 `idp-web/docs/RUNBOOK.md`。**`web__*` 只授给经 HOST 调用的角色**：成员确认、敏感级别上限、不可信标记都在 HOST 里，直连网关的客户端绕过它们 |

## 升级须知（2026-11-20 的 BOM）

从 2026-11-19 的 BOM 升到 2026-11-20 的 BOM 时，下面几处行为会变。**升级顺序**：
1. 先升级注册服务（含 idp-mcp 的迁移 0008、0009）；
2. 再升级网关；
3. 再升级 HOST（迁移 0010 到 0013）；
4. 最后升级 Workspace（迁移 0006）。

idp-web 若要用每日配额，要先给它建库（`idp-web migrate`、`ensure-login`）。


| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-host（D47） | worker 中断后，进行中的工具调用只有在工具标注为只读或幂等时才重发；其他的不重发，模型读到"被中断，结果未知，先核实"。停机时 worker 等进行中的调用结束、不再发起新的，然后把运行交给别的 worker；超过 `IDP_HOST_STOP_GRACE`（缺省 20 秒）才打断。发布的清单把 Pod 的终止宽限期写明为 30 秒 | 事件 `idp.host/tool.interrupted {resent, reason}`；指标 `idp_host_tool_interruptions_total` | 有副作用的工具被中断后，要人工核对它是否已执行。要禁止某个带"幂等"标注的工具重发，在网关配置里写 `neverResendTools`。宽限期必须小于平台的终止宽限期 |
| idp-host（D48） | **引用了能联网的子 agent（或远端 agent）的 Agent，自己也按上限筛数据。** 这样的 Agent 不再读到高于上限的附件和记忆 | 事件 `context.withheld`、`attachment.withheld` 里的 `via` 写明是因为哪个子 agent | 这是有意的。需要读机密数据的 Agent 不要引用联网的子 agent：拆成两个 Agent，或按租户调高上限，或不把那个工具列进 `externalTools` |
| idp-host（D49，契约 1.11.0） | 运行记录它的工具返回的网页来源（`sources`），并把回答里来源表中没有的引用编号列为 `unverifiedCitations`。只标记，不拦截。数据库迁移 0010 | 运行的表示里多两个字段；事件 `idp.host/run.sources` | 先升级 HOST，再升级 Workspace。保存的是编号、标题、地址、读取时间和摘要，不保存页面正文 |
| idp-host（D50） | 模型服务在流里报告的错误不再记成 `decode`：按对方的类型分为 `provider_overloaded`、`provider_rate_limit`、`provider_server`（可重试）和 `provider_error` | 步骤的 `error_code`；事件 `idp.host/model.error`（只有错误码） | 对方的原话只在 HOST 的日志里：按运行号找 `model call failed` 那一行。日志过了保留期就没有了 |
| idp-host（D51） | 网关标为"外部"的工具（第三方上游）自动算作对外工具，不用列进 `externalTools`。创建运行时会多查一次网关目录 | 事件 `context.withheld` 等的 `via` 写明是哪个工具；查不到目录时算对外 | 不需要处理。网关不可达时，有工具的运行一律按对外筛数据 |
| idp-host（D52） | 给模型配了 `providers[].models[].contextWindow` 之后：调用前按 token 估算输入，按水位逐步截短旧结果和旧脚本参数，到 80% 提示模型收尾，放不下时调用前就失败 | 每步的事件 `idp.host/context.view` | **不配窗口的部署行为不变。** 本机模型建议配上：五轮连续追问从 15 轮完成 7 轮变为全部完成（实测） |
| idp-host（D53） | 一步里连续的只读工具调用并发执行，缺省最多 4 个（`gateways[].parallelCalls`，1 即关闭） | — | 上游要能承受同一运行的并发请求。idp-web 同时只读 2 个 PDF，超出的 2 秒后答"忙"；沙箱的读工具在 Kubernetes 上每个调用一个任务。不想并发的只读工具列进 `sequentialTools` |
| idp-host（D54） | 每一步记录开始输出前的等待、模型服务复用和重新处理的 token 数（迁移 0011） | `run_steps` 的新列；`model.usage` 的新字段 | 不需要处理。用来判断模型服务的缓存是否有效 |
| idp-host（D55） | 因输出上限被截断的回答，带着的工具调用一个也不执行，运行按 D40 失败 | 事件 `idp.host/model.cutoff` | 不需要处理 |
| idp-host（D56） | 首选模型因自身原因出错（密钥、模型名、额度、窗口）时改问下一个候选模型；内容被拒绝时不改问 | 步骤的 `failed_candidates`（迁移 0012）；事件 `model.error` 的 `outcome` | 配了备用模型的部署，以前一次 401 就失败，现在会改问备用模型 |
| idp-host（D57） | worker 的运行接上发起请求的追踪，每次模型调用和网关调用各一个 span（迁移 0013）。发给外部模型服务的请求缺省不带追踪头 | 追踪后端里能看到整条运行 | 没配 OTLP 收集器时没有任何开销。自有模型要带追踪头，就在提供方条目写 `tracePropagation: true` |
| idp-host（D58、D59） | 配了窗口时，截短改为攒批，两次之间发出的前缀不变；`context.keepBudgetNotes` 打开时预算提示改为追加 | `context.view` 的 `batch`、`batchWhy`、`bottom` | **只对按前缀复用缓存的本机模型服务有意义**（如 Strata、llama.cpp）。对照实验（每组 4 遍）：每步重读的 token 中位数降到原来的十分之一以下，开始输出前的等待从约 3.5 秒降到约 1 秒；步数和总时间的差别在波动范围内。**本机这类服务建议配上 `contextWindow` 并打开 `keepBudgetNotes`**。Anthropic、Gemini 适配器不要打开 `keepBudgetNotes` |
| idp-host（D60） | 配了窗口的模型，每次调用都带输出上限：发布件或运行参数给的更小值，否则运维的 `providers[].models[].maxOutputTokens`，否则 32,768，再按窗口剩余收紧。模型陷入重复、被上限截断（或流超过 8 MiB）记为 `output_runaway`，换一种采样（温度 0.7，重复惩罚 0.5）重试一次 | 步骤的 `error_code`；事件 `model.error`、`model.retry` | **没配窗口也没配 `maxOutputTokens` 的模型行为不变**，也就没有防失控。要防失控就配上其中之一。单步正常输出超过约 2.5 万 token 的用途，把 `maxOutputTokens` 调高 |
| idp-mcp（D46） | **网关必须有一份客户端名单（`IDP_GATEWAY_CLIENTS_FILE`），没有就不启动。** 名单之外的客户端通过了认证也看不到任何工具 | 网关启动即退出，错误里写明这个设置；原来能用的客户端 `tools/list` 变成空的；审计里 `DENIED`，原因 `client not listed` | 在名单里列出每个调用网关的 OAuth 客户端。平台自己的 HOST 是 `idp-host: {kind: platform, host: true}`（compose 与 kind 的配置里都有）。`"*": platform` 会关掉这项控制，只用于开发或过渡 |
| idp-mcp（D48） | `hostOnlyTools` 里的工具只有标了 `host: true` 的客户端能看到和调用。平台的配置里是 `web__*` | 其他客户端调用得到"Unknown tool"；审计原因 `host-only` | 不需要处理。确实要让别的客户端用，在 `hostOnlyExcept` 里按工具写例外和理由，每次使用都会记审计 |
| idp-mcp（D49） | 外部客户端有限流（每客户端每分钟 600 次、每成员 120 次，可按客户端改）；平台客户端不限 | HTTP 429 与 `Retry-After`；审计 `RATE_LIMITED` | 按需在名单里给客户端写 `rateLimit`、`subjectRateLimit` |
| idp-mcp（契约 1.2.0） | 审计记录多了 `reason`、`bytes` 和三种结果；数据库迁移 0008 | — | **先升级注册服务（含迁移），再升级网关。** 旧的注册服务会整批拒收带新字段的记录，网关会一直重试并积压 |
| idp-web（D28 到 D30） | 可以给租户设每日的搜索与读取上限（`dailySearches`、`dailyReads`），计数放在 PostgreSQL；来源编号也放进去，多个副本因此一致。发布的清单带数据库：迁移任务、连接用的 Secret、到数据库的出口策略 | 超出上限的调用被拒绝，审计原因 `quota_exceeded`；数据库不通时，有上限的租户得到 `quota_unavailable`，编号得到 `numbering_unavailable`，`/readyz` 返回 503 | 给 idp-web 建库和账号（`idp-web migrate`、`ensure-login`，做法与其他组件相同）。**配了数据库之后它就是硬依赖**：数据库不通，联网工具整体不可用。不想要配额和多副本的部署可以不配数据库，行为与之前相同 |
| idp-web（D27） | 正文由脚本生成的页面不再当作页面交给模型，返回专门的错误 | 审计原因 `needs_script`；指标 `idp_web_failed_total{reason="needs_script"}` | 不需要处理。这个计数是下限，用来决定要不要做渲染器 |
| idp-mcp（D50 到 D53，契约 1.3.0） | 上游有了"外部"一类：不外传身份，元数据只放行白名单，结果限长，拒绝对方发起的反向请求，参数按评审时的定义校验，探测失败即拦截，工具带"外部"标记，只许 HOST 调用（迁移 0009） | 审计结果 `RESULT_TOO_LARGE`、`REVERSE_REQUEST_REFUSED`；`lastStatus.targets[].blocked` | 现有上游缺省是内部，行为不变。`IDP_BRIDGE_ENDPOINTS` 在有外联桥之前不用设。批准外部服务时要带评审过的目录摘要 |
| idp-web（D31） | 读有文字层的 PDF，解析在单独的受限子进程里；扫描件答"没有文字层"。Go 工具链升到 1.27.4。Pod 内存上限从 512Mi 提到 1Gi | 审计原因 `pdf_no_text_layer`、`pdf_encrypted`、`pdf_malformed`、`pdf_too_slow`、`pdf_too_large`、`pdf_busy`、`pdf_reader_unavailable` | 镜像里多了 `/idp-web-pdf`。不想读 PDF 设 `IDP_WEB_PDF=off`；并发由 `IDP_WEB_PDF_CONCURRENCY` 控制（缺省 2） |
| idp-web（D19、D20） | 引用里的地址始终是被编号的那个；读取发生重定向时另给 `finalUrl` | — | 不需要处理 |
| idp-sandbox（D10、D23） | `Mcp-Method` 头必填，与网关一致；office 镜像里多了 `sheetmap`、`sheetcheck`，但不推荐给模型（实测没有减少步数） | 直接调用沙箱、不带这个头的请求得到 400 | 自写的直连脚本补上这个头 |
| idp-workspace（D29，契约 1.3.0） | 运行的收件箱条目带上它用过的网页来源，页面上列出；回答引用了来源表里没有的编号时给出提示（迁移 0006） | 收件箱条目的 `sources`、`unverifiedCitations` | 先升级 HOST。只有正常完成的运行有来源 |
| 平台（镜像） | 第三方基础镜像有了一份放在 `ghcr.io/idp-xyz/mirror` 的副本（工作流 `mirror.yml`）；各仓库的引用还没有切换过去 | 工作流的汇总表 | 第一次运行后，把 `mirror` 包设为公开，只需一次 |

### 本机模型服务

- **一次只服务一个请求。** 平台上多个成员同时用时要排队；实测有人同时使用时，每一步排队 9 到 31 秒。部署时给模型服务开多个并行槽位，或用能并发的服务。
- **缓存是否有效，看 D54 的数字**：非攒批步上，复用的 token 应接近上一步的输入总量。如果复用值总是落在固定间隔上（Strata 的缺省是系统提示词结束处，以及每 16,384 token 一个检查点），说明回合边界的检查点没有对上。这时检查是否配了 `contextWindow` 和 `keepBudgetNotes`。
- **Strata 的检查点**：`--prompt-cache`（缺省 6 个，每个约 118 MB 内存）、`--prompt-cache-every`（缺省 16384）。打开 D58、D59 之后，调小间隔几乎没有额外收益（预测），不必改。

## 升级须知（2026-11-22 的 BOM）

从 2026-11-20 的 BOM 升到 2026-11-22 的 BOM：**新增一个组件 idp-mcp-bridge（外联桥）**，成员用自己的 OAuth 授权访问第三方 MCP 服务（计划：`docs/adr/idp-mcp-bridge-oauth-plan.md`；桥自己的运维手册：`idp-mcp-bridge/docs/RUNBOOK.md`）。不开桥，其余组件的变化不改变现有行为。compose 上的配置在 `deploy/compose/platform.yaml` 与 `deploy/compose/e2e.sh` 里是同一组键，kind 上的在 `deploy/kubernetes/full` 与 `tests/kind/full.sh` 里。

**升级顺序**（顺序错了，错在哪里都写在最后一栏）：
1. idp-mcp：先注册服务，再网关。**没有新的迁移**（0009 是 2026-11-20 的）。
2. idp-host：没有迁移、没有配置项；契约 `idp.telos.host` 仍是 1.11.0。
3. 给桥建库与账号，跑它的迁移任务，建服务账号，再部署桥（下面第 1 到 4 条）。
4. Keycloak 的改动（第 5 条）。**要在 Workspace 开"已连接的服务"之前做完。**
5. Workspace：**先跑迁移 0007**（表 `connection_pending`），再滚动 Deployment；最后才在它的配置里写 `targets.connections`（没写就没有页面、没有路由）。契约升到 `idp.telos.workspace` 1.4.0（只有新增）。
6. 在注册服务里登记连接、授权评审账号，给角色授工具，发新的 Release。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-mcp（D51 a 的修订 15.1） | **指向桥地址（`IDP_BRIDGE_ENDPOINTS` 匹配）的外部上游，网关发签名的调用者断言**（受众是那个目标的端点，有效期 60 秒），不发别的身份；其他外部上游仍什么身份都不发。**网关没有 `IDP_GATEWAY_CALLER_KEY_FILE`（签名密钥）时，对桥的调用不发出**（`NOT_DELIVERED`），桥得不到身份就不该收到调用 | 成员的调用得到工具错误；网关审计里是 `NOT_DELIVERED`；桥的日志里没有这个调用 | 网关设 `IDP_GATEWAY_CALLER_KEY_FILE`（沙箱、idp-web 用的就是这把，不用另做一把）；公钥放进桥的 ConfigMap `idp-mcp-bridge-gateway-caller-keys`（键 `caller.pub`，可放多把，轮换时先加新的）。注册服务与网关**都**设 `IDP_BRIDGE_ENDPOINTS=<桥的地址>/mcp/`（同一个值）；桥地址上登记的服务器必须是 `external`，`identity` 留 `none`（桥的 README 与 RUNBOOK 里写的 `forward` 是错的，登记会被拒绝）。两边时钟差不要超过 30 秒 |
| idp-mcp（15.2，平台验收发现） | 桥的每条连接是 `<桥的地址>/mcp/<连接 id>`，而登记与重新发现要求路径以 `/mcp` 结尾，所以连接登记不了（`endpoint must end in /mcp`）。现在桥地址上的端点免于**这一条**路径规则，别的检查（主机白名单、地址分类、不许有查询串）不变 | 登记桥的连接时 400 | 升到含 15.2 的 idp-mcp。注册服务的允许名单与私网名单（`IDP_ALLOWED_UPSTREAM_HOSTS`、`IDP_PRIVATE_UPSTREAM_HOSTS`）要列上桥的主机，与其他私网上游一样 |
| idp-mcp 网关的配置 | **第三方的工具是 HOST 的**：确认、敏感级别上限、"不可信"标记都在 HOST 里，直连网关的客户端绕过它们。与 `web__*` 一样：只授给经 HOST 调用的角色，并写进客户端名单的 `hostOnlyTools`（平台配置里是 `["web__*", "thirdparty__*"]`） | 别的客户端看得到第三方的工具 | 每条连接（它的 id 就是工具名前缀）都加一项 `<连接 id>__*` |
| idp-host（D61） | 工具结果里带桥的"需要授权"标记时，HOST 照常把它作为工具错误告诉模型，**另发一条事件 `idp.host/authorization.required {toolCallId, tool, connection}`**。只从网关标为外部的工具读，只带连接 id，**从不带链接**（"去授权"的链接由 Workspace 自己生成）；运行不暂停，成员授权后再问一次 | 运行的事件流里有这条事件 | 不需要处理，没有配置项。先升级 HOST，再让 Workspace 开页面（Workspace 才显示"去授权"按钮） |
| idp-mcp-bridge（新组件，B1 到 B3） | 一个 MCP 2026-07-28 服务器，每条连接一个端点；第三方只收到这个成员自己的令牌和协议信封，收不到任何 TELOS 的身份（没有调用者断言、租户、成员、追踪头）。**它是平台里唯一出站到公网的组件**，自己一个命名空间 `idp-mcp-bridge`。**发布的 `connections.yaml` 没有连接，Pod 起不来**（哪些第三方会收到成员的 Agent 发出的内容，是运维的决定） | Pod 退出，状态码 2，日志写明原因 | 写连接文件（`idp-mcp-bridge/docs/RUNBOOK.md` 第 4 节）。平台自己的验收用的连接在 `tests/kind/full.sh`、`deploy/compose/e2e.sh` 里，只用于测试 |
| idp-mcp-bridge 的数据库 | 成员的授权（令牌）在桥自己的库 `idp_bridge` 里（表 `grants`、`pending_states`、`client_registrations`），与其他组件一样分所有者与应用两个角色 | 服务在库的版本与它要的不一致时拒绝启动 | **建库**（DBA）；**所有者的 URL** 放进 Secret `idp-mcp-bridge-database-owner` 的 `owner-url`，**只有迁移任务 `idp-mcp-bridge-migrate` 挂它**；`kubectl apply -k` 之后等任务完成；**一次性**用同一个镜像跑 `idp-mcp-bridge ensure-login idp_bridge_svc`（密码在 `IDP_BRIDGE_APP_DB_PASSWORD`），再建 Secret `idp-mcp-bridge-database`（`service-url` 与签发库证书的 CA `ca.crt`）。之后的升级：先跑迁移任务（任务的模板建好后不能改，先删旧的），再滚动 Deployment。网络策略里数据库的出口、Keycloak 的出口已在桥的清单里，按你的命名空间与标签调整；**桥的清单里到 Keycloak 的出口写的是 8443（https 端口）：运维要改成自己 Keycloak 的端口**（本平台的 `deploy/kubernetes/full` 里是 7080，叠加层的 `keycloak-bridge` 加了一条）；PostgreSQL 与 Keycloak 一侧要放行 `idp-mcp-bridge` 命名空间的入口。网关与注册服务到桥 8680 的出口（idp-mcp 的清单，经 `idp-mcp/mcp-upstream` 标签的命名空间）、Workspace 到桥 8681 的出口（idp-workspace 的清单）已在各组件自己发布的清单里 |
| idp-mcp-bridge（D35） | 成员重新授权时，桥在新授权存好之后向第三方撤销被替换的旧令牌（尽力而为，失败不影响新授权）；断开与 `revoke` 照旧撤销 | 第三方的记录里旧令牌被撤销 | 不需要处理 |
| idp-mcp-bridge 的加密密钥 | 令牌、PKCE 验证码、客户端密钥在库里是 AES-256-GCM 的密文（附加数据是租户、成员、连接与密钥 id），**备份拿到的是密文，密钥在另一个 Secret 里：两者分开保管，丢了密钥则所有授权作废**（成员要重新授权） | — | Secret `idp-mcp-bridge-encryption-keys` 的 `keys`：每行 `<id>:<32 字节的 base64>`（`openssl rand -base64 32`），**第一行加密，所有行都能解**。**轮换**：1. 文件最前面加一行新密钥（旧的留着），更新 Secret，滚动重启；2. 用一次性的 Job 跑同一个镜像的 `reseal`（镜像是 scratch，不能 exec），把所有旧行改封到新密钥下，输出 `re-sealed N values under key <新 id>`；3. 这之后才把旧密钥从文件里删掉，再滚动重启 |
| Keycloak | 桥要一个客户端代表它、Workspace 要能为成员换出以桥为受众的令牌、评审账号要一个角色（做法以 `deploy/keycloak/realm.py` 为例） | Workspace 的"已连接的服务"页面 502；桥拒绝 Workspace 的调用（401） | ① **一个客户端 `idp-mcp-bridge`（bearer-only）**，它的 client id 就是桥的 `IDP_BRIDGE_API_AUDIENCE`；② **Workspace 的令牌交换客户端（`IDP_BRIDGE_WORKSPACE_CLIENT`，平台里是 `idp-workspace`）加一个指向它的受众映射**，并且换出的令牌**保留 `roles` 与 `tenant_id`**（那个客户端上已有的用户角色映射、租户属性映射；桥从令牌取成员、租户与评审角色，不取别处）；③ **评审角色**（realm 角色，名字 = `IDP_BRIDGE_REVIEWER_ROLE`，没有缺省，不设就没有人能）给负责目录的运维，他还要是 Workspace 的成员（`ws-member` 或 `ws-admin`）才用得上它的页面；④ 桥的 `IDP_BRIDGE_OIDC_ISSUER`、`IDP_BRIDGE_OIDC_JWKS_URL`（缺省 `<issuer>/protocol/openid-connect/certs`，必须 https；集群内只有 http 时设 `IDP_BRIDGE_OIDC_ALLOW_HTTP=true`，启动日志会警告） |
| idp-workspace（D30，契约 1.4.0，迁移 0007） | 新增"已连接的服务"：成员在壳的页面给自己连接第三方服务（每个服务一次，在对方的页面上用 OAuth 授权，令牌由桥保管，Workspace 与智能体都看不到）；**OAuth 回调落在 Workspace 自己的 `/oauth/callback`**，只有这位成员自己发起的、没用过、10 分钟内的授权才会交给桥；`/oauth/client/<id>.json` 公开托管 CIMD 客户端元数据文档；对话里工具因没授权失败时显示"需要先连接 <服务>"。迁移 0007 建 `connection_pending`（只有 state 的哈希）。契约 `idp.telos.workspace` 1.4.0 只有新增，`idp.telos.host`、`idp.telos.mcp`、`idp.telos.cmi` 不变 | `/v1/me` 的 `connections` 是 true；页面 | 配置 `targets.connections`：`endpoint`（桥的 **8681** 端口）、`auth: exchange`、`exchange.audience`（= 桥的 `IDP_BRIDGE_API_AUDIENCE`）、`exchange.clientId`（= 桥的 `IDP_BRIDGE_WORKSPACE_CLIENT`）、`publicUrl`（Workspace 的公网地址，只有协议与主机，https；桥的 `IDP_BRIDGE_REDIRECT_URI` = `<publicUrl>/oauth/callback`，两边必须逐字一致）。**授权服务器上登记的重定向地址（预注册客户端）或客户端元数据文档里的 `redirect_uris` 就是它。** Workspace 发布的出口策略已有到桥 8681 的一条（只放桥的 API 端口，不放 MCP 端口）。`testOrigins` 只给验收，**部署里不要设**（桥的 `IDP_BRIDGE_TEST_ORIGINS` 同样永远不要设） |
| 连接与评审账号 | 很多第三方连列出工具都要令牌，网关的登记、评审、漂移探测没有成员：每条连接要有一个**运维授权的评审账号**（第三方那里专门为此建的账号，权限是能列出工具的最小），只用于 `tools/list`，从不用于成员的调用 | 登记后 `discover` 得到 502 | 有评审角色的运维在 Workspace 的"已连接的服务"里对这条连接点"授权评审账号"，**用那个专门的账号**登录并同意；然后在注册服务里 `POST /api/v1/servers`（`class: external`，端点逐字等于桥的 `check-config` 列出的受众）、`discover`、读目录、`approve`（带评审过的目录摘要）、授工具给 `host-user` 一类经 HOST 调用的角色、发 Release。做法见 `deploy/compose/bridge_setup.py` 与验收的 O0 |
| 成员离开租户 | **没有自动触发。** 身份提供方不会通知桥，桥也不扫描；管理员撤销某个成员的授权也只有这条命令 | 离职成员的授权还在库里，令牌在第三方那边还有效 | **离职流程里调用 `idp-mcp-bridge revoke --tenant T --member M [--connection C]`**（逐条向授权服务器撤销，再删除）；`revoke --connection C` 清一条被撤下的连接的所有授权。用一次性的 Job 跑（同一个镜像，同样的数据库、密钥与连接文件设置） |
| 平台（镜像与发布） | 新的镜像 `ghcr.io/idp-xyz/idp-mcp-bridge:<提交>`，和其他组件的镜像一起由 `platform.yml` 在验收通过后发布、签名、附 SBOM | 工作流汇总里多一行 | **第一次发布之后，把 `idp-mcp-bridge` 这个 ghcr 包设为公开，只需一次**（新包缺省是私有的，集群拉不到）。BOM 里多了 `idp-mcp-bridge` 一项；`checkout_components.py` 因此也克隆它 |

**已知缺口**（验收发现）：
- **compose 版的网络隔离比 kind 粗**：compose 不能按对端限制，第三方（测试容器）与桥共用一个网络；kind 里 NetworkPolicy 的验收（O8）才是"只有网关、注册服务与 Workspace 到得了桥的端口"的证明。

## 升级须知（2026-11-23 的 BOM）

从 2026-11-22 的 BOM 升到 2026-11-23 的 BOM。起因是外联桥第一次对真实的第三方服务（GitHub 的托管 MCP 服务）做验证（R1），验证中发现并修好的几处都在这里。**升级顺序**：1. idp-mcp 的注册服务（**迁移 0010**），再网关；2. idp-host（没有迁移）；3. 桥（没有迁移）；Workspace 没有新的变化。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-mcp（15.3，D53 的修订，契约 `idp.telos.mcp` 1.4.0，迁移 0010） | **外部服务器的描述长度上限按服务器设定。** 登记 `class: external` 的服务器时可带 `maxDescriptionBytes`（字节）；不带用部署缺省 `IDP_EXTERNAL_MAX_DESCRIPTION`（1024，不变）；超过部署的上限 `IDP_EXTERNAL_MAX_DESCRIPTION_CAP`（缺省 8192）的登记与修改被拒绝（400，写明上限）；缺省值超过上限时注册服务不启动。**登记后可改，但要重新评审**：`PATCH /api/v1/servers/{id}` 请求体 `{"maxDescriptionBytes": 数字或 null}`，有变化时服务器退回 `DRAFT`，要重新发现（按新上限）、重新批准（四眼），已做好的 Release 仍用它钉住的目录。评审接口 `GET /api/v1/servers/{id}/catalog` 多 `descriptionLimit`，批准即钉住。契约只在注册服务的 API 上有新增，文档的 JSON Schema 不变 | 发现外部服务器时被拒绝：`tool pull_request_read: inputSchema/properties/method/description is 1546 bytes, more than the 1024 allowed`（GitHub 的真实例子，28 个工具里只有这一个超过 1024） | 先升级注册服务（迁移 0010，两列，可为空）。**不要调高部署缺省**（那会放宽所有第三方服务器）：对需要的那一台服务器登记时写 `maxDescriptionBytes`，评审人读得完那么长的指令文字。没有设置的服务器行为与此前完全一致 |
| idp-host（D62） | 工具结果里**内嵌的资源块**（`resource`，GitHub 的 `get_file_contents` 就这样返回文件）的文字现在交给模型：一行头 `[resource <uri> <mimeType>]`，再是它的文字（与其他文字块一样计入结果的上限）；二进制只写名字与大小。此前模型只看到 `[resource ]` | 读文件的工具回答里模型说"没有内容" | 不需要处理，没有配置项，契约不变 |
| idp-mcp-bridge（D36） | **令牌端点用 HTTP 200 回错误（GitHub 就是这样）也算错误**：应答体里有非空 `error` 就是错误应答，不论状态码。`bad_verification_code`、`bad_refresh_token` 按 `invalid_grant` 处理（授权被删除、成员被告知重新授权）；`incorrect_client_credentials`（客户端密钥错）、`redirect_uri_mismatch`（回调地址与 App 里的不同）、`unverified_user_email` 是令牌端点的错误：授权不会被删除，日志里有码 | 此前一个已死的刷新令牌永远是"暂时无法更新"，成员不会被要求重新授权 | 不需要处理 |
| idp-mcp-bridge（D37） | **最小权限：授权请求恰好是连接文件里配置的 `scopes`**（评审账号：`reviewerScopes`，没有则 `scopes`）；没有配置就**没有 `scope` 参数**。此前 `scopes` 为空时会去要资源元数据列出的全部范围，对企业平台这个缺省不对（可能含写权限）。要旧行为必须明确写 `scopesFromResource: true`（缺省 false） | 授权地址里的 `scope` | GitHub App 的连接写 `scopes: []`（App 的权限在 App 的设置里）。依赖旧缺省的连接要把范围写出来，或明确选 `scopesFromResource` |
| 平台（验收工具） | **`GITHUB=live` 模式与 `tools/github_app_manifest.py`**：对真实的 GitHub 做一次受控验证，人只点几下。`python3 -I tools/github_app_manifest.py` 在 127.0.0.1:8562 提供 GitHub 的 App Manifest 流程（组织 idp-xyz，只要 Metadata: read，webhook 关闭，两个回调地址 8561 与 8560），凭据写进 `.local/github-app/`（client-id、client-secret、private-key.pem，目录 700、文件 600，不打印）；`GITHUB=live KEEP=1 bash deploy/compose/e2e.sh` 加一条 `github` 连接（预注册客户端、`scopes: []`、不开桥的出口代理，桥直接上网，地址规则照旧），再 `bash deploy/compose/github_live.sh`：一个监听 127.0.0.1:8561 的脚本代替回调页，先后为评审账号与 bob 打印 GitHub 的授权地址，人只在浏览器里授权；脚本登记服务器（`maxDescriptionBytes: 2048`）、批准目录、只授 `get_me` 与 `get_file_contents`、跑两个调用、查审计、在所有日志、事件、数据库里找 App 的密钥与桥持有的令牌、断开并检查再调用要求授权。`--double` 用验收自己的第三方代替 GitHub 测脚本的逻辑 | — | 只用于验证，不是部署的一部分。GitHub 没有撤销端点：验证后到 github.com 的 Settings → Applications → Authorized GitHub Apps 撤销 App 的授权 |
| 平台（PATH O9） | 验收多一步：一个描述 1500 字节的外部服务器，按缺省上限被拒绝（并写明工具与上限）、PATCH 到 2048 后通过、登记时带 2048 通过、超过上限的值在登记时被拒绝，评审视图写着该服务器的上限 | `PASS: PATH O9` | CI 要求它通过 |

**R1 的结果（2026-11-23，真实的 GitHub）**：用 GitHub App（以清单流程创建，只有 Metadata: read，没有 webhook；GitHub 是否给它发了会过期的用户令牌这次没有核对）和 `https://api.githubcopilot.com/mcp/readonly`：评审账号经真实的 GitHub OAuth 授权，发现列出 28 个工具，批准后只授 `get_me` 与 `get_file_contents`；bob 授权后，他的运行经 HOST、网关、桥调用 `get_me`（得到 GitHub 登录名）与 `get_file_contents`（公开仓库 idp-xyz/idp-mcp-bridge 的 README 回到了模型：这里验证了桥算出的 `Mcp-Param-owner`、`Mcp-Param-repo` 被 GitHub 的真实校验器接受）；桥的审计里两个调用都是 `ok`；App 的密钥和桥持有的所有令牌在任何容器日志、运行事件、数据库里都找不到；bob 断开后下一次调用得到"需要授权"。运行中发现的是描述上限（15.3）与内嵌资源块（D62）；令牌端点的 200 错误（D36）与范围缺省（D37）是接入前读 GitHub 的元数据和审查代码时发现的；都已在上面各行修好。验收脚本自己的两处缺陷（回调监听只认自己发出的 state；交回调时现取令牌）也已修好。另：这台机器到 github.com 时断时续不通，是本地网络的问题，与平台无关。

## 升级须知（2026-11-24 的 BOM）

从 2026-11-23 的 BOM 升到 2026-11-24 的 BOM：**成员离开时撤销他在第三方的授权**（计划 `docs/adr/idp-mcp-bridge-offboarding-plan.md`，桥的 ADR D38 到 D42，桥的 RUNBOOK 5.9、5.10）。**升级顺序**：只有桥有变化，先跑桥的迁移 Job（**迁移 0003**），再滚动 Deployment；其他组件没有变化。**身份系统**：Keycloak 只是开发测试用的过渡，最终的还没定；下面凡是 Keycloak 专有的东西都在开发测试的脚手架里（`deploy/keycloak/realm.py`、compose 与 kind 的验收配置、桥的 `keycloak-dev` 适配器），核心不依赖它。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-mcp-bridge（D38，迁移 0003） | **"成员离开"接口** `POST /internal/v1/members/offboard`（端口 8681）：一次一个成员（租户、成员、原因 `user_deleted`、`user_disabled`、`tenant_changed`、`operator`、`other`），只许平台的服务调用：OIDC 客户端凭据模式的服务令牌，同一个发行方、受众是 `IDP_BRIDGE_API_AUDIENCE`，`azp` 必须在 `IDP_BRIDGE_OFFBOARD_CLIENTS`（逗号分隔）里；**这个设置为空则接口不存在（404）**。成员的令牌（`azp` 不在名单里、带会话 `sid`、是委托 `act`）403，无效的 401。幂等，答案只有计数。处理同 `revoke`：先撤销刷新令牌再撤销访问令牌（授权服务器能撤销的），删除授权与未完成的授权，写审计（`event: offboard`、原因、来源、调用方 `azp`） | 审计里 `event: offboard`；指标 `idp_bridge_offboard_total` | 名单里**只放专用的服务客户端**，不要放 Workspace 或任何会签发成员令牌的客户端。**服务令牌不能带 `sid`**：Keycloak 的客户端凭据令牌缺省没有 `sid`（验收 PATH O10 检查过），只有给客户端打开"客户端凭据也用刷新令牌"（Use refresh tokens for client credentials grant，已废弃）才会有，那样桥会拒绝，所以别打开 |
| idp-mcp-bridge（D39，D40） | **对账（兜底）**：每 `IDP_BRIDGE_RECONCILE_INTERVAL`（缺省 15 分钟，`0` 关闭）把库里每个（租户、成员）交给目录适配器，结果五种：仍在、已停用、已删除、换了租户、查不清。停用、删除、换租户按"离开"处理，**查不清（超时、出错、没权限、应答不像用户）一律不动**。**安全闸**：一轮里判定离开的成员不少于 5 人且多于已知成员的 20%，这一轮整个不执行（日志 `reconciliation brake`、计数器），运维核对后 `idp-mcp-bridge reconcile --now --force`；`reconcile --now` 立即跑一轮；同一时刻只有一个副本在对账（咨询锁）。评审账号的授权人离开时账号保留，列表里标"授权人已离开"并告警。**重新启用不会自动恢复**：成员回来要重新连接 | 日志 `reconciliation round`（members、present、unknown、leaving、offboarded） | `IDP_BRIDGE_DIRECTORY=none`（缺省）则不对账。要用，配适配器（下一行） |
| idp-mcp-bridge（D41）与开发测试的 Keycloak | **开发测试用的 Keycloak 适配器**（`IDP_BRIDGE_DIRECTORY=keycloak-dev`，启动日志会说明）：`IDP_BRIDGE_KEYCLOAK_URL`、`_REALM`、`_CLIENT_ID`、`_CLIENT_SECRET_FILE`、`_TENANT_ATTRIBUTE`。它用客户端凭据取令牌，`GET /admin/realms/{realm}/users/{sub}`：404 已删除，`enabled: false` 已停用，租户属性不对换了租户，别的都查不清。**最终的身份系统定了以后换适配器（SCIM 的 `GET /Users/{id}` 等），平台的"成员生命周期"入口（SCIM 或 SSF 的推送，实时性由它提供）也还没做**，那之前"成员离开"靠这个对账（延迟一个间隔）或运维调接口 | — | 在开发测试的 Keycloak 里（`realm.py` 已写好）：一个保密客户端 `idp-bridge-directory`，**服务账号只有 realm-management 的 `view-users`**（别的不给），而且客户端要带 **客户端范围 `kc-admin-api`**：**Keycloak 26.8 的管理 REST 接口只认带这个范围（受众加范围）的令牌，没有它答 401 `Token is missing the required admin API scope`，不论服务账号有什么角色**（验收发现）；它的令牌也要 `fullScopeAllowed`，角色才进令牌。再一个保密客户端 `idp-lifecycle-svc` 代表将来的生命周期服务，令牌带桥的受众（`idp-mcp-bridge`），它是 `IDP_BRIDGE_OFFBOARD_CLIENTS` 里唯一的 `azp`。适配器的客户端密钥是挂载的文件（kind 里是 Secret `idp-mcp-bridge-client-secrets` 的 `directory` 键）。出口策略放行到 Keycloak 已有（桥自己的 `bridge-keycloak` 与叠加层的 `keycloak-bridge`，7080） |
| idp-mcp-bridge（D42，F2） | **GitHub 的撤销**：连接配置 `authorization.revocation: github`（只对预注册的客户端）：断开、再授权、成员离开（接口与对账）、`revoke` 都调 GitHub 自己的接口 `DELETE /applications/{client_id}/grant`（失败再试 `/token`），再授权时只撤被取代的那个访问令牌。失败时授权照样删除，列表里"未连接，撤销未确认"（`revocation_unconfirmed`），提示成员到 GitHub 的 Settings → Applications 自己撤销 | 日志里的状态码 | **对 GitHub App 的用户令牌是否有效还没核实**（GitHub 现行文档说可以，旧文档说不适用）：下一次真实的 GitHub 验证时实测。验收里只用替身测了桥的逻辑，没有对真实的 GitHub 做 |
| 平台（验收，PATH O10） | compose 与 kind 的验收新增：bob 连接；模拟"生命周期服务"的客户端调接口让 bob 离开（原因 `user_disabled`：200 且删了授权、第三方收到撤销、审计有原因与 `azp`；重复调用 200 零；bob 自己换来的令牌 403、无令牌 401）；alice 连接后，桥到 Keycloak 的路被切断并在 Keycloak 停用 alice：几轮对账都"查不清"，什么都没撤；路恢复后几轮之内 alice 的授权被删、第三方收到撤销，重新启用后她要重新连接。验收里的对账间隔是 5 秒（compose 用环境变量，kind 在每次运行的叠加层里），**缺省 15 分钟不变** | `PASS: PATH O10b`、`O10c`、`O10d` | CI 要求它们通过 |

## 升级须知（2026-11-25 的 BOM）

从 2026-11-24 的 BOM 升到 2026-11-25 的 BOM：**新增一个组件 idp-browser（渲染读取，浏览器能力计划第 1 层）**，以及 idp-web 的 D32、D33，idp-mcp-bridge 的 D43，idp-sandbox 的 D24（计划 `docs/adr/browser-capability-plan.md` 第 2、3、6、8 节；idp-browser 自己的运维手册 `idp-browser/docs/RUNBOOK.md`）。**不开渲染器，其余行为不变**：idp-web 不设 `IDP_WEB_RENDER_URL` 时与以前完全一样。**升级顺序**：先部署 idp-browser（命名空间、两个密钥、RuntimeClass、镜像），再给 idp-web 加渲染设置并滚动；idp-web 没有新的迁移。idp-sandbox 与 idp-mcp-bridge 只有拨号方式的变化，随时滚动。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-browser（新组件） | 两个镜像：渲染器 `idp-browser`（headless Chromium，`POST /render`，只收 idp-web 签名的请求，端口 8590，运维端口 9590）与出口代理 `idp-browser-proxy`（3128 代理、3129 管理、9591 运维）。渲染器的每个请求都经代理，代理只拨公共地址（私网、回环、链路本地、云元数据地址、端口不是 80 与 443 一律拒绝），每次渲染有自己的凭据与预算（300 个请求、20 MiB、单个响应 10 MiB）。不存任何数据。**镜像**：`ghcr.io/idp-xyz/idp-browser:<提交>` 与 `ghcr.io/idp-xyz/idp-browser-proxy:<提交>`，由 `platform.yml` 在验收通过后发布、签名、附 SBOM；渲染器的基础镜像是 chromedp 的 headless-shell（Chromium 159.0.8154.13）按摘要，取自平台的镜像站副本（`releases/mirror-images.yaml`，`resolvedVia: mirrors`：登记时 Docker Hub 不可达，摘要由两个镜像站互相印证，下次能直连 Hub 时核实并删掉这一行） | 命名空间 `idp-browser` 里两个 Deployment；BOM 里 `idp-browser` 一项带 `extra_images`（代理的镜像） | `kubectl apply -k idp-browser/deploy/kubernetes`（restricted、默认拒绝、带配额）；镜像按摘要写进 `kustomization.yaml` 的 `images`。**两个新的 ghcr 包（`idp-browser`、`idp-browser-proxy`）和 `idp-mcp-bridge` 的包，第一次发布之后都要设为公开，各一次**（新包缺省私有，集群拉不到） |
| idp-browser 的密钥 | 两把，都不在仓库里：**idp-web 的 P-256 私钥**（Secret `idp-web-render-key`，键 `render.key`，挂进 idp-web 的 `IDP_WEB_RENDER_KEY_FILE`）与它的公钥（ConfigMap `idp-browser-caller-keys`，键 `caller.pub`，渲染器只信这一把）；渲染器与代理共用的**管理密钥**（Secret `idp-browser-proxy-admin-key`，键 `admin.key`，至少 32 字节，两个 Deployment 都挂） | 渲染器日志 `keys` 一栏是它持有的公钥标识；缺 Secret 时 Pod 停在 `ContainerCreating` | `openssl ecparam -name prime256v1 -genkey -noout -out render.key`；`openssl ec -in render.key -pubout -out caller.pub`；`openssl rand -hex 32 > admin.key`。轮换：先把新公钥加进 ConfigMap（可同时放两把）、重启渲染器，再换 idp-web 的私钥 |
| idp-browser（RuntimeClass） | 清单里 Chromium **自己的沙箱是关的**（`IDP_BROWSER_NO_SANDBOX=true`），理由是这个 Pod 在 gVisor（或 Kata）里，所以 `settings.yaml` 里的 `runtimeClass` 与 `isolatedBy` 必须是真的：集群要有那个 RuntimeClass，否则 Pod 起不来；声明了 `isolatedBy` 才允许 `--no-sandbox`（服务自己检查） | Pod 事件 `RuntimeClass "gvisor" not found` | 集群没有 gVisor 或 Kata 就**不要**用发布的清单：改成 Chromium 自己的沙箱（不设 `runtimeClassName`、`IDP_BROWSER_NO_SANDBOX=false`、seccomp 要放开，因为 Chromium 的沙箱要用户命名空间，而 restricted 的 Pod 安全级别不许放开 seccomp，命名空间也得降级）——这不是生产配置，只有测试集群这样做（见下一行） |
| idp-browser（gVisor 下的内存监视，验收发现） | gVisor 的 `/proc/<pid>/statm` 里"共享页"一列恒为 0，渲染器按"驻留减共享"累加整棵 Chromium 进程树，每个进程把共享库（约 250 MB）各算一遍：**空白页的 Chromium 就超过 `IDP_BROWSER_RENDER_MEMORY_MB` 的 1536，每次渲染都以 `render_failed`（"the page used more than 1536 MiB of memory"）结束**。idp-browser 提交 443eb48 有这个缺陷（kind 里在 RuntimeClass gvisor 下第一次渲染就失败）；修正（`internal/render/mem.go`：整棵树都没有共享页时视为"读不到"，监视关闭，由 Pod 的内存上限兜底，附单元测试）在本次交付的 idp-browser 工作树补丁里（`.local/p1-patches/idp-browser-l14.patch`），**合并之前不要在 gVisor 下部署 443eb48 的镜像**，或临时设 `IDP_BROWSER_RENDER_MEMORY_MB=0` 与 `IDP_BROWSER_RECYCLE_MEMORY_MB=0` | 渲染器日志 `render refused or failed … used more than 1536 MiB`，idp-web 审计 `render_failed`；用 `chromium-base` 镜像在 gVisor Pod 里 `cat /proc/<pid>/statm` 能看到第三列全是 0 | 合并补丁后重建镜像；验收（kind 的 M6）用的就是带补丁的镜像 |
| idp-browser（NetworkPolicy） | 默认拒绝，加这些流：idp-web（命名空间 `idp-web`，标签 `component: web`）→ 渲染器 8590；observability → 9590 与 9591；渲染器 → 代理 3128、3129（只有这一条出口）；渲染器与代理 → kube-dns 53；代理 → **公共互联网 80/443，排除所有私网段**。idp-web 一侧要有一条到渲染器 8590 的出口策略（`web-renderer`，idp-web 的清单随 D33 带上，`catalog/integrations.yaml` 的 `web-browser-render` 由 `tools/k8s_check.py` 核对） | `tools/k8s_check.py --full`；kind 里 PATH M6h 用探测 Pod 逐条验证 | 集群的 CNI 必须执行 NetworkPolicy（Calico）；代理的"公共互联网"规则依赖集群的出口路径，有出口网关的环境要按它调整 |
| idp-browser（Chromium 更新节奏） | Chromium 每四周一个大版本，安全修复更勤 | 渲染器日志里的版本 | **每月第一个工作日**看 `docker.io/chromedp/headless-shell` 有没有新的稳定版本，**有 CVE 的版本立即跟**。步骤：取新版本的索引摘要 → 改 idp-browser 的 `deploy/Dockerfile` 的 `CHROMIUM_IMAGE` 与本仓库 `releases/mirror-images.yaml` 的条目（tag、digest、target）→ idp-browser 的 `make test-browser` 全绿 → 构建、`tools/scan_images.sh` → BOM 升级。落后一个月以上要在运维记录里写明理由 |
| idp-web（D33） | 需要脚本的页面（普通读取判为 `needs_script`）与租户 `alwaysRender` 列出的主机交给渲染器读**一次**；普通读取永远先做（地址规则、租户名单、robots.txt、配额、缓存都在它那里），**PDF、纯文本、非 2xx 从不交给渲染器**；拒绝页（HTTP 200 的 "Access Denied"，如 Akamai、FedEx 的措辞）报 `blocked_by_site`，一句话告诉模型"站点不让自动读取"。设置 `IDP_WEB_RENDER_URL`（也是签名的受众，必须与渲染器的 `IDP_BROWSER_CALLER_AUDIENCE` 逐字相同）、`IDP_WEB_RENDER_KEY_FILE`（缺省都不设：功能关闭）、`IDP_WEB_RENDER_TIMEOUT_MS`、`IDP_WEB_RENDER_MAX_BYTES`；租户文件加 `alwaysRender: [主机]`。**渲染器没起来不阻止 idp-web 启动**：渲染不可达时那个页面给 `needs_script` 的句子，审计 `reason=render_unreachable`，别的页面照常读 | 审计行多了 `rendered`、`render`、`renderMs`；指标 `idp_web_renders_total{outcome}`；回答里 `(rendered; bytes …)` | 在 idp-web 的 Deployment 上加上面两个环境变量与密钥挂载（`web.yaml` 里有注释好的例子），租户文件按需加 `alwaysRender`。发布的清单里**没有** `IDP_WEB_TEST_ORIGINS`，验收的叠加层才有 |
| idp-web（D32）、idp-mcp-bridge（D43）、idp-sandbox（D24） | 检查过的地址按 **Happy Eyeballs（RFC 8305）**拨号：交错地址族、错开 250 ms、先连上的赢。设置 `IDP_WEB_DIAL_FAMILY`、`IDP_BRIDGE_DIAL_FAMILY`、`IDP_EGRESS_DIAL_FAMILY`（沙箱的出口代理），值 `prefer-ipv4`（缺省）、`dual`、`ipv4-only`、`ipv6-only`。**缺省取 `prefer-ipv4` 的原因**：真实站点（`www.fedex.com`）在没有可用 IPv6 路由的网络上，解析器把 IPv6 排在前面，原来一个接一个地拨，整个预算花在不通的 IPv6 上（读取 `too_slow`）；IPv6 先拨在这类网络里仍要等 250 ms。纯 IPv6 的网络改 `dual`（IPv4 先拨在那里多花 250 ms）。不认识的值启动失败，启动时记一行日志 | 启动日志 `dial family` 一行；`curl -4` 快而读取慢是这个问题的特征 | 不用动，缺省就是修复；只有纯 IPv6 的环境才设 `dual` |
| 平台（验收，PATH M6） | compose 与 kind 的验收新增 M6a 到 M6h，步骤是同一份 `deploy/compose/path_m6.sh`：(a) 只有脚本才有正文的页面读到了，回答里有 `(rendered;`，审计 `rendered:true render:"ok"`，`idp_web_renders_total{outcome="ok"}` 加一；(b) 迟到的文字（页面再请求一个慢接口）；(c) SSRF 页面用 fetch、XHR、图片、iframe、WebSocket、信标请求元数据地址、三段私网、回环、`::1` 与集群内的名字（金丝雀、Keycloak、CMI、网关），**代理的审计里全部拒绝（`not_public`、`port`），金丝雀一个请求也没收到**；(d) 请求风暴（3000 个子资源请求）在预算内结束（代理放行恰 300 个），下一次渲染照常；(e) HTTP 200 的 Access Denied 页（Akamai 措辞，纯文本与脚本生成两种）给 `blocked_by_site` 的句子；(f) `alwaysRender` 主机上的 PDF 由 PDF 读取器读，渲染器没被调用（渲染计数、代理对该主机的行数、指标都没变，源站只被要了一次）；(g) 停了渲染器：`needs_script` 的句子、审计 `render_unreachable`，另一个页面照常读，渲染器重启后就绪；(h) 网络隔离（compose：渲染器只在 `browsernet`；kind：探测 Pod 对 NetworkPolicy 逐条验证，只有 idp-web 的标签到得了 8590、渲染器的标签只到代理 3128/3129、代理的标签到不了任何集群 Pod）。`platform.yml` 把 M6a 到 M6h 列为必须出现的 PASS | `PASS: PATH M6a` 到 `M6h` | **compose 的隔离比 kind 粗**：渲染器与 idp-web 共用 `browsernet`，kind 才是"只有 idp-web 到得了渲染器"的证明（M6h）。**验收里的隔离**：kind 的一次性集群用 `tests/kind/gvisor-node.sh` 装 gVisor（runsc，kvm 平台），渲染器在 RuntimeClass `gvisor` 下 `--no-sandbox` 地跑，和发布的清单一样；没有 RuntimeClass 时 `full.sh` 退到 Chromium 自己的沙箱并说明。compose 在没有 `runsc` 运行时的 Docker 上用 Chromium 自己的沙箱（seccomp 放开，只读根，无能力，用户 10600，4 GiB），有 `runsc` 就用 gVisor；M6 的 PASS 行写明实际用了哪一种 |

**已知缺口**（验收发现）：
- **idp-web 的清单要随 D33 带上到渲染器的出口策略**（`web-renderer`）；在它合并之前，`full.sh` 的叠加层带同样的一条（`web-renderer-overlay`），`tools/k8s_check.py` 对 `web-browser-render` 在 idp-web 合并前会失败。
- 浏览器能力计划里的后面几层（交互、会话）没做，渲染器只做"渲染后读取"。

## 升级须知（2026-11-25 的第二个 BOM：知识库 KB1）

从 2026-11-25 的 BOM 升到带知识库第一步（KB1）的 BOM：**idp-cmi 增加知识库对象、三级可见、成员写入、生效窗口、结构分块和原件**（idp-cmi ADR-0001 第 24 节 D47 到 D58，迁移 0013 到 0016，契约 `idp.telos.cmi` 1.5.0；计划 `docs/adr/knowledge-base-plan.md`，idp-cmi 的 README"升级到有知识库的版本"）。只有 idp-cmi 变了；契约 1.5.0 对 1.4.0 只有向后兼容的新增，别的组件不用改。**升级顺序**：先停 idp-cmi（见下面的停机说明），跑迁移 Job，再起新版本；对象存储可以之后再配，没配时文本入库照旧、存原件得 503。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-cmi（迁移 0013 到 0016，D47） | **迁移要当停机处理**：0013 与 0015 对 `knowledge_documents`、`knowledge_chunks` 取 ACCESS EXCLUSIVE 锁并**改写每个 chunk 行**，停机随语料增长（idp-cmi 在本机测得 30 万个 chunk 约 7 秒；估算与测量办法见该 ADR D47 之后的"迁移"一段）。迁移在同一事务里对相关表 `NO FORCE ROW LEVEL SECURITY`、回填、再 `FORCE`，所以所有者不是超级用户也能回填。已有文档归入以其来源为名的企业库，可见性不变 | 迁移 Job 的日志；`\d knowledge_bases` | 先停 idp-cmi 再迁移；**迁移后对 `knowledge_chunks` 做 VACUUM**；语料大的环境先在副本上量一次时间 |
| idp-cmi（D52，语义变化） | **"现行"只有一个定义，被取代是推导的**：旧发布把被取代的文档版本存成 `superseded`，升级后这些行按新规则重新解读。**撤销一个后继版本会让前一个版本重新成为现行**（之前什么都不返回）；一个版本到期不会让更早的版本复活；定为未来生效（`validFrom`）的版本不取代现行版本，as-of 视图按窗口给出 | 撤回最新一版后，检索与"现在"的 as-of 视图给同一个答案 | 依赖"撤回即消失"的做法要改成"撤回即回滚到上一版"；要彻底去掉一份文档的所有版本，撤回每一版或用擦除 |
| idp-cmi（D50、D54，原件） | **原件存在对象存储里**，库里只留键、摘要、大小、媒体类型；没配对象存储时 CMI 照常启动（后台连，不等它），存原件得 503。新环境变量：`IDP_CMI_ORIGINALS_ENDPOINT`、`_BUCKET`、`_ACCESS_KEY_FILE`、`_SECRET_KEY_FILE`（后两个是文件，不是值）、`_REGION`（缺省 us-east-1）、`_SSE`（`false` 关 SSE-S3，缺省开）、`_CA_FILE`（https 端点的额外信任 CA）、`_TIMEOUT`（缺省 2m）。下载一律是附件（`Content-Disposition: attachment`、`nosniff`、`Content-Security-Policy: sandbox`），读出时核对摘要，不符是 500 并写日志 | CMI 日志 `knowledge originals configured`；`PUT /v1/knowledge/documents/{id}/original` | 给原件一个**自己的**对象存储实例（不是成员的文件库，也不是数据库备份的桶：三者的生命周期与擦除要求不同）；平台的验收用 RustFS 的第三个实例 `knowledge-s3`（compose）与 `idp-kind-knowledge-originals`（kind，`KIND_KB_S3_NAME`、`KIND_KB_S3_PORT` 可改名改端口，同一台机器上两个集群要各用各的） |
| idp-cmi（`IDP_CMI_TEMP_DIR`） | **原件下载需要一个可写的临时目录**（下载经一个已 unlink 的临时文件校验摘要）；根文件系统只读时必须挂一块：Kubernetes 里 idp-cmi 自己的清单带 `emptyDir`（`/var/cache/idp-cmi`，`sizeLimit: 512Mi`，8 个并发下载乘 50 MiB 再加余量），**平台的叠加层只增不换卷，别用整体替换 `volumes` 的补丁**；compose 里要加 `tmpfs: ['/var/cache/idp-cmi:size=512m,mode=1777']` 和 `IDP_CMI_TEMP_DIR: /var/cache/idp-cmi`（`deploy/compose/platform.yaml` 已带） | 缺目录时下载失败，启动日志 `IDP_CMI_TEMP_DIR: … writable temporary directory` | 自己的编排里照样挂；`tmpfs` 或 emptyDir 的大小不要小于并发下载数乘单个文件上限 |
| idp-cmi（配置文件 `knowledge` 一节） | 新的 `knowledge`：`indexContext`（标题进索引文本，缺省关，D56；在评测集上量过没采纳）、`personalQuotaDocuments`、`personalQuotaBytes`（个人库没有自己的配额时用它；个人库配额为 0 不再等于不限）、`maxInFlightUploadBytes`、`maxConcurrentDownloads`、`maxDownloadsPerCaller`（并发上传下载的上限，防止一个调用者占满）、`transferTimeout` | 配置文件；超限时 429 | 平台的叠加层把 `cmi.yaml` 置空（全部取缺省）；生产里按成员数与文件大小定 |
| idp-cmi（D47、D48、D53，权限） | 成员可以写入：`POST /v1/knowledge/documents`（`cmi-reader` 或 `cmi-writer`），写自己的个人库（第一次上传时建立，名字带哈希，上传时 `kb://me/<路径>` 是别名）或自己管理的企业／单元库；**uri 必须是 `kb://<库名>/<路径>`，否则 422**；企业库、单元库、个人库里相同的路径是不同的文档，谁也不取代谁。**管理员的缺省检索范围与成员相同**：企业库、所属单元的库、自己的个人库，读别人的个人库必须指名并记审计 `knowledge.read_other`；成员读不到的库和文档是 404 | 审计 `knowledge.read_other`、`knowledge_base.read` | 管理员培训：看别人的个人库是被记录的动作；成员写入会向 PDP 问 `knowledge.write`（配了 PDP 的环境要有这条规则；没有 PDP 的环境不受影响） |
| idp-cmi（D57，擦除） | **按主体擦除也擦他的个人库**：文档的文本块、原件（新的擦除域 `originals`，对象删除之后才标 verified）、实体、包里的摘录；uri 改成 `kb://<库名>/erased-<文档 id>`；主体自己请求的包的 `query`、`purpose` 换成墓碑；其他文档里 `owner` 是该主体的清空 | `GET /api/v1/erasures/{id}` 的各域；对象存储里该文档前缀下没有对象 | 对象删除之前这次擦除不会标 verified（D57） |
| idp-cmi（契约） | `idp.telos.cmi` **1.5.0**：新定义 KnowledgeBase；KnowledgeDocument 增 `knowledgeBase`、`validFrom`、`docType`、`owner`、`reviewDue`、`original`；ContextItem 的来源增 `locator`（标题路径、页码、表名）。`idp-contracts` 里的副本与 `idp-cmi/api/contracts` 逐字节相同（`validate.py` 核对）；旧版本文件保留 | `idp-contracts/registry.json` | 消费者按需升级，不用改 |
| 平台（验收，PATH P0） | compose 与 kind 的验收新增 PATH P0（步骤是同一份 `deploy/compose/path_p0.sh`）：管理员建企业库与单元库；bob（单元成员）往个人库上传 `kb://me/…`；非 `kb://` 的 uri 进库得 422；bob 个人库里与企业文档同路径的文档不取代它（carol 仍检索到企业文档）；carol 指名 bob 的个人库得 404；管理员缺省解析不含 bob 的个人文档，指名则读到且记 `knowledge.read_other`；`validFrom` 在将来的版本到那时之前不现行（as-of 视图）；原件上传下载（附件、`nosniff`、CSP sandbox、摘要核对）；擦除 bob 后他的个人文档、原件（桶里没有他的对象）、uri 与他的包里的查询文本都没了。realm 新增用户 `carol`（`cmi-reader`）。kind 的验收里 idp-cmi 的 `originals` Secret 与 `cmi-originals` 出口策略在叠加层里 | `PASS: PATH P0` | 平台验收之外不需要 carol |

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
| 网关到 idp-web 不通（登记时 discover 超时，或调用超时） | 网络策略没放行 8580：网关与注册服务的出口策略要有 8580（idp-mcp 的清单已有），`idp-web` 命名空间要带 `idp-mcp/mcp-upstream: "true"`，且它的入口策略要放行网关**和**注册服务（`idp-web/deploy/kubernetes/networkpolicy.yaml`）；或注册服务的 `IDP_ALLOWED_UPSTREAM_HOSTS` 没有 `idp-web.idp-web.svc.cluster.local` | 对照 `tools/k8s_check.py --full`；`kubectl -n idp-web get pods` 看是否就绪 |
| idp-web 的 Pod 起不来（`CrashLoopBackOff`，日志 `IDP_WEB_PROVIDER is not set`）或 `ContainerCreating` | 发布的清单没有搜索服务（设计）；Secret `idp-web-ref-key`、ConfigMap `idp-web-gateway-caller-keys` 没建 | 整平台里是 `full.sh` 建它们、覆盖层设 `fixture`；单独部署看 `idp-web/docs/RUNBOOK.md` |
| PATH M 失败：回答里是 `the site did not deliver the page within 15 seconds`（审计原因 `too_slow`），或 `no allowed web_fetch of …` | 测试源站是私网地址，要靠 `idp-web-test-origins` 那条测试策略放行（`tests/kind/web-origin.yaml`） | 确认这条策略在 `idp-web` 命名空间里 |
| PATH M6 失败：回答是 `produced by script and cannot be read now`，审计 `render_failed`，渲染器日志 `used more than 1536 MiB of memory` | gVisor 下内存监视误报（见上面升级须知的"gVisor 下的内存监视"） | 用带修正的 idp-browser 镜像，或把 `IDP_BROWSER_RENDER_MEMORY_MB`、`IDP_BROWSER_RECYCLE_MEMORY_MB` 设为 0 |
| 渲染器不可用，idp-web 的审计是 `render_timeout`（等满 20 秒）而不是 `render_unreachable` | Service 没有端点（Deployment 缩到 0 或 Pod 全未就绪）时包没有被 DNAT，目的地仍是 ClusterIP，egress 策略（只放行渲染器的 Pod）把它丢掉，连接超时而不是被拒绝；渲染器进程在、端口不监听才是 `render_unreachable`（连接被拒） | 两者 idp-web 给模型的句子相同（`needs_script`）；要更快地失败，把 `IDP_WEB_RENDER_TIMEOUT_MS` 调小，或让渲染器的 Deployment 至少有一个就绪的 Pod |
| PATH M6h 失败：`pods "probe" is forbidden: failed quota: idp-browser` | `idp-browser` 命名空间的 ResourceQuota 要求每个 Pod 写 requests 与 limits | 探测 Pod 带上资源（`full.sh` 的 `probe_pod` 已带） |
| `MODEL=live`：运行失败或一直 running | 本机模型没开，或转发容器停了 | `curl 127.0.0.1:8080/v1/models`；`docker start idp-kind-model-forward`；`docker logs idp-kind-model-forward` |
| "助手"页显示"请在 IDP Workspace 里打开" | 直接打开了页面，或 17561 的转发没开 | 从壳的导航进入"助手"；开 assistant 的端口转发 |
| 经网关调用沙箱得 403 `the caller assertion is missing or invalid`（沙箱日志 `caller assertion refused`） | 网关没有签名密钥（没设 `IDP_GATEWAY_CALLER_KEY_FILE`，或密钥 `idp-mcp-gateway-caller-key` 不存在）；网关的私钥与沙箱公钥文件里的公钥不成对（轮换时先换了网关）；沙箱的 `IDP_SANDBOX_CALLER_AUDIENCE` 与登记的端点不是逐字相同；该上游没有以 `identity: forward` 登记 | 看网关与沙箱的环境变量和挂载文件；对一下两边的密钥（`openssl ec -in caller.key -pubout` 与 ConfigMap 里的公钥）；先加公钥、重启沙箱、再换网关的私钥；端点按登记的 URL 改 |
| 沙箱：`the request carries no caller identity` | 没有以 `identity: forward` 注册 | 重新注册（不可改，要新建） |
| 迁移 / 建租户的 Job 显示 `Error` | 数据库还没起来时的重试 | 看最后一次是否 `Completed` |
| `kustomize: go: … goproxy.cn … EOF` | 代理偶发中断 | 重试 |
| 机器重启后两个 kind 集群的 Pod 全是 `Unknown`，kube-proxy `CrashLoopBackOff`：`too many open files` | 5 个节点容器共用宿主机的 inotify 上限（默认 128 个实例） | `docker exec kind-control-plane sysctl -w fs.inotify.max_user_instances=8192`，再删掉 kube-proxy 和 calico-node 的 Pod 让它们重建；`multinode-cluster.sh` 建集群时会调高，但重启后要重新执行 |
| 机器重启后 cert-manager、CNPG operator、PostgreSQL、各组件全部 `CrashLoopBackOff`，日志 `dial tcp 10.96.0.1:443: i/o timeout` / `connect PostgreSQL: context deadline exceeded` | Docker 重启时重新分配 kind 网络上的地址，节点换了 IP；`full.sh` 写进 NetworkPolicy 的 API Server 地址（`ipBlock` /32）过期，`default-deny` 拦下了到 API Server 的流量 | `bash tests/kind/pin-node-ips.sh`（`KIND_CLUSTER=ha` 换集群）：给节点固定当前 IP，把 NetworkPolicy 里过期的 API Server 地址改成当前的，不清数据；之后删掉崩溃的 Pod 或等它们下一次重启。`local-cluster.sh`、`multinode-cluster.sh` 建集群时、`full.sh` 每次运行时都会固定，固定后重启不再换 IP |
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
- PVC 容量在 kind 上不被执行；EER 的 Intelligence 没有部署在集群里（PATH C 从宿主运行）。
- 出口代理：PATH L 的源站在集群里，没有测过真实的公网流量；名单按租户，不按人或角色；PATH L 从网关开始，不经过 HOST 和模型（模型替身不会要 `network=egress`）。
- idp-web：PATH M、N 的页面在集群里，搜索服务是 fixture，没有真实的公网流量或真实的搜索服务商走过 Kubernetes 上的出口规则（私网地址被排除这一点由反向检查证明：去掉测试策略，PATH M 失败）；只有模型替身版；每次运行的调用上限做不到（阶段 1，idp-web ADR-0001 D22）；一个副本，编号、缓存、限流在内存里。
- 平台 CI 不跑整平台的集群验收（只有轻量 kind 版和静态检查），整平台验收目前只在本机。

## 升级须知（2026-11-25 的第三个 BOM：第三方镜像改从镜像库拉取）

从知识库 KB1 的 BOM 升到这个 BOM：**所有第三方镜像改从 `ghcr.io/idp-xyz/mirror` 拉取**，digest 与源相同（清单 `releases/mirror-images.yaml`，复制工作流 `mirror.yml`）。各组件的代码与行为不变。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| 全部（compose、kind 与正式清单、Dockerfile、CI） | 第三方镜像的引用都换成镜像库的 tag，例如<!-- mirror:upstream: the example names the source on purpose --> `public.ecr.aws/docker/library/postgres:17-alpine` → `ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine`；按 digest 固定的仍按同一个 digest 固定 | `kubectl get pods -A -o jsonpath='{..image}'` 里的第三方镜像都以 `ghcr.io/idp-xyz/mirror:` 开头 | 能访问 ghcr.io 的环境不用做什么。**用自己 registry 的环境**：照清单把每个条目复制进自己的 registry（`crane copy <source>@<digest> <你的 registry>/<tag>`），再用 kustomize 的 `images:` 或各组件的镜像变量改名；digest 不变，可以照旧核对 |
| idp-sandbox（运行时镜像的缺省值） | `IDP_SANDBOX_PYTHON_IMAGE`、`IDP_SANDBOX_SHELL_IMAGE` 的缺省值与 `sandbox.yaml` 里的值从 `python:3.12-slim`、`alpine:3.22` 改成镜像库的名字 | 沙箱 Pod 的环境变量；节点上 `crictl images` 出现镜像库名字的 python 与 alpine | 预先把镜像拉到节点上的环境：按新名字再拉一次，或在节点上把已有镜像打上新名字（同一个 digest）；显式设置了这两个变量的环境不受影响 |
| 本机开发 | 脚本和 compose 按新名字找镜像；本机 Docker 里已有的源镜像不会被自动认出来 | `docker compose pull` 或首次构建开始从 ghcr.io 下载 | `python3 tools/mirror_images.py retag`：给本机已有的源镜像打上镜像库的名字（只打标签，不下载）。到 ghcr.io 很慢的网络上，新镜像先用原来的办法拉源名字，再 `retag` |
| CI 与检查 | 平台的 `tools/mirror_images.py check` 遇到直接写第三方源的引用（含 `python:3.12-slim` 这样的短名）就失败；九个组件仓库的 CI 各有一步同样的检查 | CI 报 `NOT THE MIRROR` 或 grep 步骤失败 | 新的第三方镜像先进清单、合并后由 `mirror.yml` 复制，再改引用；有意写上游名字的行标 `mirror:upstream` |

## 升级须知（2026-11-25 的第四个 BOM：知识库 KB2，嵌入与语义检索）

从第三方镜像改从镜像库拉取的 BOM 升到这个 BOM：**平台启用嵌入与语义检索**（知识库计划 `docs/adr/knowledge-base-plan.md` 第 2.4、8、10 节；idp-cmi ADR-0001 D11、D26、D37、D38、D58、D59）。新增一个平台自己构建的镜像 `idp-embedding`，CMI 的配置多了 `embedding` 一节；解析出的条目带 `match`（`lexical`、`semantic` 或 `both`）。**没有嵌入服务时 CMI 照常启动、照常答词面检索**，起来之后自己把缺的向量补上（回填），升级可以不停机地分两步做：先上嵌入服务，再给 CMI 加配置。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| 镜像库（`releases/mirror-images.yaml`） | 新条目<!-- mirror:upstream: the row names the source of the new entry on purpose --> `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4`（digest `sha256:2538ea1c…`，Apache-2.0）；这是一个**独立的改动**：先合并并等 `mirror.yml` 把它复制到 `ghcr.io/idp-xyz/mirror`，再合并用到它的改动 | `python3 tools/mirror_images.py check --strict` 在新文件进库之前报该条目 `UNUSED`（扫描只看 `git ls-files`） | 先合并镜像库条目；`python3 tools/mirror_images.py retag` 给本机已有的源镜像打镜像库的名字 |
| 平台镜像 `idp-embedding`（`deploy/images/embedding`，登记在 `catalog/images.yaml`，BOM 的 `images` 节） | 以镜像库的 TEI 为底，把 `Qwen/Qwen3-Embedding-0.6B` 在固定修订 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` 的权重**拷进镜像**（Apache-2.0；`model.sha256` 里 10 个文件逐个核对，构建时不对就失败）；运行时不下载、不联网，非 root（10700），根文件系统只读；`MODEL_REVISION`、`model.sha256` 是缓存键 | `docker run … idp-embedding:dev` 无密钥时 401，有密钥时 `/v1/embeddings` 给 1024 维；`docker inspect` 看 `User` | 构建脚本 `deploy/images/embedding/build.sh`：模型目录取 `EMBEDDING_MODEL_DIR`，其次 `~/.cache/idp-eval/models/Qwen3-Embedding-0.6B`，再次按固定修订从 `HF_ENDPOINT`（缺省 huggingface.co）取到 `.local/embedding-model/<修订>`。CI 用 `actions/cache` 缓存该目录，键是 `MODEL_REVISION` 与 `model.sha256` 的哈希。发布：`publish_images.sh` 按 BOM 里的源码树哈希打 tag |
| 嵌入服务两个实例（compose：`embedding`、`embedding-query`；kind：`idp-embedding` 命名空间里的两个 Deployment） | **文档实例**（摄入、回填）：4 CPU，`IDP_EMBEDDING_THREADS=4`，`--max-batch-tokens 2048`，内存请求 3 GiB、限额 4 GiB；**查询实例**（只嵌入提问）：2 CPU，`--max-batch-tokens 1024`、`--max-client-batch-size 8`，内存请求 2.5 GiB、限额 3 GiB。同一个镜像、同一把密钥；CMI 的 `embedding.query`（D59，`baseUrl`、`apiKeyFile`、`timeout`，**密钥不继承**，要自己写）指向第二个。**为什么两个**：TEI 按输入排队，同一个实例上一条查询要等完整的摄入请求（4 CPU 实测，每次 64 条时一条查询等了 247 秒，8 条时 p50 27.7 秒、p95 31.3 秒）；分开之后摄入时查询 p50 281 毫秒、p95 401 毫秒，摄入速度不变 | CMI 日志 `the query endpoint matches the documents endpoint: queries use it`；指标 `idp_cmi_query_endpoint_state{state="ok"}` 为 1 | 查询实例不通过探测（`mismatch`、`unreachable`）时查询只答词面（不改用文档实例），指标与日志都有；超过 1024 词元的提问被查询实例拒绝（413），那一条只答词面 |
| 嵌入服务的预热与内存 | **预热那一遍用满一个批次，批次大小决定预热时间和峰值内存**：`--max-batch-tokens 4096`（idp-cmi 评测的缺省）在 4 CPU 上预热 1 分钟到十分钟以上，**峰值 5.8 GiB**，3 GiB 限额下被 OOM 杀过两次；2048 时预热 22 秒、峰值 3.3 GiB、入库速度相同（0.35 对 0.37 块每秒）。查询实例 1024 词元：预热 14 秒、峰值 2.6 GiB（512 词元：6 秒、2.4 GiB）。线程数必须跟着 CPU 限额（`IDP_EMBEDDING_THREADS`，`start.sh` 设 MKL、OpenMP、Rayon），否则 4 CPU 配额上开 20 个线程预热要 6 分钟。稳态内存约 2 GiB | `docker stats` 看峰值；Pod 被 `OOMKilled`；`/health` 在预热完成前不通 | 不要把限额放回 3 GiB 以下；kind 清单的 `startupProbe` 允许 10 分钟；平台的验收脚本等嵌入服务健康最多 20 分钟 |
| 嵌入服务的吞吐（CPU，Qwen3-Embedding-0.6B，fp32，约 400 词元的块）。**所有数字都是在机器被别的任务占着时量的**（负载 3 到 10，每次测量的 `uptime` 记在 `deploy/images/embedding/measure.py` 的输出里）；8 CPU 那一条尤其：0.43 块每秒是在负载 5.6 到 8.9 时量的 | 4 CPU 每秒 0.24 到 0.37 块（每次 64 条与 8 条无差别）；**8 CPU 0.43 块每秒**（只快 1.8 倍，内存中位 2.8 GiB、峰值 2.9 GiB；限额 6 GiB 才起得来）；单条查询 p50 158 到 185 毫秒、p95 约 200 到 230 毫秒 | `deploy/images/embedding/measure.py` | **批量入库是小时级的事**：1 万个块约 8 到 12 小时（4 CPU）。首次导入大语料：临时给文档实例更多 CPU，或接受回填慢慢来（词面检索照常答）。个人库配额乘成员数就是嵌入量的上限 |
| CMI 配置（`embedding` 一节；compose 由 `e2e.sh` 写，kind 在 `deploy/kubernetes/full/kustomization.yaml` 的 `idp-cmi-config`） | idp-cmi 对 Qwen3-Embedding-0.6B 的推荐值，原样使用：`model: qwen3-embedding-0.6b`，`queryPrefix: "Instruct: Given a user question, retrieve the passages that answer it\nQuery: "`，`floor: 0.40`，`memoryFloor: 0.20`，`lexicalBlend: 0.05`，`timeout: 30s`；`dimensions` 不设（TEI 可能拒绝）；密钥是文件（`apiKeyFile`，compose 里 `.local/platform/embedding.key`，kind 里 Secret `idp-embedding-key` 挂在 `/etc/idp/embedding/api-key`，嵌入服务和 CMI 两个命名空间各一份） | 解析出的条目 `match` 字段；`GET /metrics` 上 `idp_cmi_embedding_requests_total{role,outcome}` | **换了模型、前缀或维度要重算向量**（文档前缀的哈希进 `embedding_model`）；`floor` 对语料敏感，上线前要有自己的标注题（计划第 10 节） |
| HNSW 索引（D38） | `idp-cmi vector-index 1024`（属主连接，pgvector 在平台的 PostgreSQL 里）：compose 在 CMI 启动前跑一次；kind 是 `kind-vector-index` Job（`deploy/kubernetes/full/setup-jobs.yaml`，带 migrate 标签） | `pg_indexes` 里 `knowledge_chunks` 有 `hnsw` 索引 | 换维度要重建 |
| NetworkPolicy（kind） | `idp-embedding` 命名空间默认拒绝；只有 `idp-cmi` 的 Pod 能连 8700，可观测命名空间连 9700，嵌入 Pod 出站什么都不连（权重在镜像里，DNS 也不需要）；`idp-cmi` 一侧的 `cmi-embedding` 只放行到嵌入 Pod 的 8700；`catalog/infrastructure.yaml` 有流 `embedding-cmi`，`tools/k8s_check.py --full` 通过（14 个命名空间、27 条流）。compose 里 `embednet` 是 internal 网络，只有 CMI 同在 | `tools/k8s_check.py --full`；M6h 同类的探针 Pod | |
| 嵌入服务停了（D58、D59） | **不影响解析**：CMI 答词面检索（`match: lexical`），不把任何块标成永久失败（只有嵌入服务拒绝文本本身，400、413、422，才算永久）；服务回来后回填补齐。指标：`idp_cmi_embed_failures_total{kind="unavailable",role}`（整个请求没拿到答案，一次一计）、`idp_cmi_embedding_last_success_timestamp_seconds{role}`（`time() - 它 > 600` 可以告警）、`idp_cmi_query_endpoint_state{state}` | 同上 | 回填间隔 `backfill`（缺省 30 秒）；连接被拒或名字解析不了时库里的 `embed_attempts`、`embed_retry_at` **不变**（只有响应本身失败才记），要看中断看指标或日志 |
| 平台（验收，PATH P1） | compose 与 kind 的验收新增 PATH P1（同一份 `deploy/compose/path_p1.sh`）：**P1a** 查询实例在、文档实例停：上传的两篇文档词面可答、不共享任何词的改述找不到、`unavailable`（`role=documents`）计数增长、没有块被标成永久；**P1b** 文档实例回来：回填补齐向量（compose 12 到 28 秒、kind 3 秒），改述现在命中，`match: semantic`；**P1c** 中文问题找到英文文档；**P1d** 服务在线时上传的文档由投影嵌入，改述命中（23 到 28 秒），HNSW 索引存在；**P1e** 查询实例停：解析仍答（词面），`unavailable`（`role=query`）增长，回来之后改述又是 `semantic`、状态 ok。改述与中文问题的得分（0.4452、0.4743，下限 0.40）写在 `path_p1.sh` 的注释里 | CI 的必需列表 `… P0g P1a P1b P1c P1d P1e` | 模型、修订、前缀或下限变了而 P1b、P1c 失败时，先看得分 |
| 平台（CI 的预算） | kind 一轮（镜像已缓存）约 17.5 分钟，**kind 节点容器的内存峰值 10.5 GiB**（含两个嵌入实例各约 2.4 GiB）；compose 一轮约 11 分钟（镜像已构建）。标准 GitHub 运行器是 4 vCPU、16 GB：内存够，但 CPU 只有这里的五分之一，嵌入与预热会慢得多 | 作业超时（现在 `timeout-minutes: 60`） | 在运行器上第一次跑要看实际时间，必要时给 kind 作业加时限，或把 `IDP_EMBEDDING_THREADS` 与 CPU 限额降到运行器给得起的值（验收的等待上限是 20 分钟） |
| 评测（idp-cmi 的 `make test-eval`，在平台的嵌入服务上重跑，2026-11-25） | 对着平台镜像（TEI cpu-1.9.4、Qwen3-Embedding-0.6B 在固定修订、`--max-batch-tokens 2048`）重跑 `qwen3-0.6b-instruct`、floor 0.40、blend 0.05：大集（`large`）recall@1 0.750、@3 0.929、@5 0.962、@10 0.987、MRR 0.864、nDCG@10 0.894、无答案 7/10；留出集（`heldout`）0.794、0.904、0.921、0.924、0.847、0.866、20/30；**与 idp-cmi 仓库里已提交的结果文件逐字段相同**（42 与 44 个字段，只有配置名的写法不同：`f0.4` 对 `f0.40`），所以平台的 TEI 版本、批次大小与权重没有改变排名 | 重跑的办法：`IDP_EVAL_EMBEDDING_URL=…/v1 IDP_EVAL_EMBEDDING_MODEL=qwen3-embedding-0.6b IDP_EVAL_EMBEDDING_KEY=… IDP_EVAL_EMBEDDING_NAME=qwen3-0.6b-instruct IDP_EVAL_QUERY_PREFIX='Instruct: …\nQuery: ' IDP_EVAL_VARIANTS='0.40:0.05' make test-eval` | 换模型、修订、TEI 版本或前缀后重跑，与仓库里的结果比较 |
| idp-cmi（D59，查询专用的嵌入地址） | 新设置 `embedding.query`（`baseUrl`、`apiKey` 或 `apiKeyFile`、`timeout`）：查询走它，文档走主地址；模型、维度、前缀与 CA 与主地址相同，配了不同的模型或维度 CMI 起不来。**密钥不从主地址继承**，要给它自己的 `apiKeyFile`。迁移 0017 新建 `embedding_probe_references`（按模型身份存探测的参照向量，不是租户数据） | `idp_cmi_query_endpoint_state{state="ok"}` 为 1；未通过探测前查询只走词面 | 两个实例都起来、`state` 为 `ok` 之后语义检索才用查询实例；不配 `embedding.query` 时与以前完全相同 |
| idp-host（D47-14，调用方模式的回归） | 调用方已经用工具消息回答过的调用，不再被当成"写了一半的一步"重建并派发；调用方模式的第二轮会照常调用模型。这是 382162b 起的回归 | 调用方模式第二轮的运行有 `STEP_STARTED`，以模型的回答结束 | 无需处理 |
| idp-eer（CI 与依赖） | otel 升到 v1.47.0（GO-2026-6974、GO-2026-6975）；与 HOST、CMI 的集成工作流也每天跑一次、可手动运行 | — | 无需处理 |
| 平台目录（`catalog/components.yaml`） | 新增一节 `incubating`，登记 idp-eii（私有仓库，不进 BOM、不部署）；`tools/platform.py` 只读 `components` | — | 无需处理 |

## 升级须知（2026-11-25 的第五个 BOM：知识库 KB3，原件转换）

从知识库 KB2 的 BOM 升到这个 BOM：**平台启用原件的转换（KB3）**——PDF、Word、PowerPoint、Excel 的原件由 CMI 的后台线程交给**第二个 idp-sandbox 实例（摄入沙箱）**转成带定位的文本，经**系统网关**到达它（知识库计划 `docs/adr/knowledge-base-plan.md` 第 2.2、5、8 节；idp-cmi ADR-0001 第 26 节 D61 到 D70；idp-sandbox ADR D25；转换器格式 `docs/adr/kb3-converter-format.md`）。变了的组件：idp-cmi（迁移 0018、契约 1.6.0、新设置）、idp-sandbox（办公镜像里有 `ooxml2md` 与 `pdftext --markdown`；**`language: office` 的说明变了**）；平台多了系统网关、摄入实例、一个 Keycloak 客户端和一组 NetworkPolicy。**升级顺序**：先 idp-sandbox 的新镜像（办公镜像）与交互沙箱的重新发现（见下），再 idp-cmi 迁移 0018，最后建系统网关与摄入实例、给 CMI 配 `knowledge.conversion`。**没配 `knowledge.conversion`（或 `enabled: false`）时 CMI 照旧：原件只存不转**，所以可以先升级、后启用。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-cmi（迁移 0018，D61 到 D70） | 新表 `knowledge_conversions`（转换作业：队列、租约、重试、结果）、`knowledge_ticket_uses`（用过的票据，保证一张票只用一次，跨副本与重启）；`knowledge_documents.status` 增 `pending`（重建检查约束，对该表整表校验一遍，持锁时间随文档数增长，按停机处理），新列 `text_source`；`knowledge_chunks` 增 `slide`、`sheet`、`cell_range`（常量缺省值，不改写行）；两张新表同样 ENABLE 加 FORCE 行级安全，作业线程用专用角色 `app.role = 'converter'` 跨租户取作业；`GRANT DELETE ON knowledge_chunks`（提交时换掉这一版的块） | `\dt knowledge_*`；迁移 Job 完成 | 与别的迁移一样先跑迁移 Job 再滚动；旧版本的 CMI 对 `pending` 行不认识，**别在迁移之后回滚到旧镜像再存原件** |
| **`pending` 的语义**（D64，契约 1.6.0 的 `status`） | 一份文档先写一版（带占位文本），再存 PDF／Office 原件：这一版在**第一次**转换成功之前是 `pending`——**不是现行、不取代任何版本、不被检索、不被嵌入**，所以转换期间它要取代的前一版照常可取，半份文档永远搜不到；成功时在**一个事务**里换成提取出来的块（定位、注入扫描、投影事件）并变为 `active`。失败（永久错误）时它仍是 `pending`，前一版仍是现行，上传的人可以撤回它、`POST /v1/knowledge/documents/{id}/ingest` 重试或换原件。已经是提取出来的文本的版本（再转一次、换原件）状态不变，旧文本照常可取，直到提交把它换掉。**消费者要容忍 `status: pending`**（`idp.telos.cmi` 的 `status` 枚举多了一个值） | `GET /v1/knowledge/documents/{id}` 的 `status` 与 `ingest`（`state`、`attempts`、`error` 原因码、`notice` 给上传者的说明）；PATH P2a、P2b | 前端（KB5）读 `ingest.notice`；监控"长时间 pending"：`idp_cmi_conversions_total{outcome,code}` |
| 系统网关（compose：`mcp-gateway-system`；kind：`idp-mcp` 命名空间的 `idp-mcp-gateway-system`，端口 3221，标签 `app.kubernetes.io/component: gateway-system`） | **一个网关实例只服务一个租户**（idp-mcp D6），所以摄入有自己的网关：租户是**系统租户**（平台自己的工作负载的租户，验收里每次新生成，注册服务里 `seed-tenant system <uuid>`）；Release 里**只有一个目标 `ingest`**（摄入沙箱，`identity: forward`，网关用**自己的**调用者签名密钥签，摄入沙箱只信这一把），授权**只给角色 `cmi-ingest`**，且只授五个工具（create、import_file、run、export_file、delete；读写列文件的三个在目录里、不授给任何人）；客户端文件**只列 CMI 的客户端 `idp-cmi-ingest`**；**`IDP_CALL_TIMEOUT` 放大**（这里 330 秒：大于一次运行的 300 秒，小于 CMI 的 `callTimeout` 390 秒；网关默认 60 秒会把长运行记成 `UNKNOWN`）。**`ingest__*` 不放进任何网关的 `hostOnlyTools`**：那会把 CMI（它不是 HOST）锁在外面，摄入悄悄断掉；访问控制靠"一个租户、一个角色、一个客户端、一个令牌受众"。注册：`deploy/compose/mcp_setup_ingest.py`（发现、**评审人读目录并确认它点名了 `ooxml2md` 与 `pdftext FILE.pdf --markdown`**、批准、授权、编译、评审、部署，等网关证明 GOOD；操作员、评审人、管理员是系统租户里三个不同的主体） | PATH P2e：CMI 的令牌在系统网关上列出五个 `ingest__` 工具；HOST 的委托令牌、成员自己的令牌、idp-probe 的令牌都得 401，在成员侧网关上 HOST 看不到任何 `ingest__` 工具 | 新部署：按上面的脚本做；**注册服务的 `IDP_ALLOWED_UPSTREAM_HOSTS` 与 `IDP_PRIVATE_UPSTREAM_HOSTS` 要加摄入沙箱的主机名** |
| **重新发现、重新评审、重新发布**（idp-sandbox D25） | 办公档位的 `sandbox_run` 说明变了，网关钉住的是发现时的目录：**已在运行的交互网关**（`edge-1`）在升级沙箱之后要对沙箱服务器再 `POST /servers/{id}/discover`（目录变了，服务器回到 REVIEWED）、由**另一个人**批准、再编译并部署 Release；不做，网关的漂移检查拒绝这些调用。系统网关是新的，没有旧的批准，第一次就是新目录 | 注册服务里服务器状态；网关日志的 `target status`；PATH D 等用到办公档位的路径 | 按 idp-mcp 的 RUNBOOK 重新评审；评审人看到的说明里应有 `ooxml2md` 与 `--markdown` |
| 摄入沙箱实例（compose：`sandbox-ingest`；kind：`idp-sandbox-ingest` 与 `idp-sandbox-ingest-runs` 两个命名空间，`deploy/kubernetes/full/ingest.yaml`） | **同一个镜像、同一份代码，自己的配置**（idp-sandbox D25）：`IDP_SANDBOX_OWNER=ingest`（启动清扫只碰同一标签）；`IDP_SANDBOX_FILES_URL` = CMI 的内部文件端点，**与 CMI 的 `endpointUrl` 完全一致**；传输上限 `IDP_SANDBOX_TRANSFER_MB=50`；`IDP_SANDBOX_WORKSPACE_MB=160`（至少）；`IDP_SANDBOX_TIMEOUT_S=300`（`runSeconds` 不得超过它）；`IDP_SANDBOX_MEMORY_MB=512`（每次运行）；服务容器**至少 1 GiB**（传输时每路并发约 110 MiB）；`IDP_SANDBOX_MAX_CONCURRENT=8`（= CMI 的 `sandboxConcurrency`，沙箱对第 9 个并发直接拒绝、不排队）；`IDP_SANDBOX_MAX_PER_CALLER=16`（**至少等于并发数**：摄入只有一个工作负载身份，所有工作区都算在它名下，缺省 8 在 8 个并发加一个正在删除的工作区时就拒绝）；`IDP_SANDBOX_MAX_WORKSPACES=24`（不小于它）；`IDP_SANDBOX_IDLE_MIN=5`（CMI 每次做完都删工作区，这是兜底；也是 CMI worker 崩溃后原件明文在沙箱里最多留多久）；`IDP_SANDBOX_CALLER_KEYS_FILE` 只放**系统网关的**公钥；`IDP_SANDBOX_OFFICE_IMAGE` = 办公镜像（`idp-sandbox-office:dev`，kind 里要 `kind load`）。Kubernetes：**自己的运行命名空间**，ConfigMap 的 `ResourceQuota` **至少 60**（50 MiB 切成 53 块；这里设 128，够几个大文件同时在传），运行 Pod 无网络（`run-pods-no-network`），`IDP_SANDBOX_RUNTIME_CLASS`（kind 上由 `SANDBOX_RUNTIME_CLASS=gvisor` 给两个沙箱，摄入的运行也在 gVisor 里）。**沙箱不验票据的签名**（只比对租户、主体与种类）：验证全在 CMI 的文件端点 | 摄入沙箱日志 `file transfers enabled`、`callers must be signed by the gateway`；PATH P2a | 数字来自 idp-sandbox 的实测（ADR D25）；机器小就降并发，三处一起降：沙箱、`sandboxConcurrency`、CMI 的 `concurrency` |
| Keycloak 客户端（`deploy/keycloak/realm.py`，**只在 dev/test 的 realm 里；Keycloak 是过渡的身份提供方**） | 新客户端 `idp-cmi-ingest`：service account（`client_credentials`），`fullScopeAllowed: false`，令牌里硬编码**系统租户**的 `tenant_id`、角色 `["cmi-ingest"]`、受众 = 系统网关的 MCP URI；service account 用户的 **id 固定**。**网关的调用者主体取令牌的 `sub`**（不是 `preferred_username`），沙箱拿它和票据的 `sub` 比，所以 **CMI 的 `workloadSubject` 必须是这个 `sub`**（realm 固定它，写进 `clients.json` 的 `ids.idp-cmi-ingest`；`preferred_username` 是 `service-account-idp-cmi-ingest`）。CMI 只认"一个 OIDC 客户端的凭据"（`tokenUrl`、`clientId`、`clientSecretFile`；密钥是文件，每次续期重读，轮换不用重启；令牌端点不跟重定向、不走环境代理）。换了身份提供方（SCIM／SSF 等），只换这一个客户端的签发方式 | `ingest_check.py` 解码令牌：`tenant_id`、`roles`、`aud`、`azp`、`sub` | 生产：在最终的身份提供方里建同样语义的工作负载身份；**别让成员或 HOST 的客户端能换到系统网关的受众**（P2e 检查 Keycloak 拒绝） |
| CMI（新设置） | 环境变量 `IDP_CMI_INGEST_LISTEN`（内部文件端点的监听，**不得与 API 同址，启动校验**；compose 里绑定 CMI 在 `ingestnet` 上的固定地址 `10.253.77.2:8551`，别的网络连不到；kind 里 `0.0.0.0:8551` 加 NetworkPolicy）、`IDP_CMI_INGEST_TICKET_KEY_FILE`（至少 32 字节，文件；**换密钥不是无缝的**：滚动更新时旧副本签的票被新副本拒绝，作业按暂时错误重试）、可选 `IDP_CMI_INGEST_TLS_CERT_FILE`／`_KEY_FILE`（缺省明文，靠网络策略与五分钟、单次、绑定一份文档的票据）。配置文件 `knowledge.conversion`：`enabled`、`gatewayUrl`（系统网关的 MCP 地址）、`toolPrefix`（缺省 `ingest__`）、`caFile`、`tokenUrl`、`clientId`、`clientSecretFile`、`scope`、`tokenCaFile`、`workloadTenant`（系统租户）、`workloadSubject`（见上）、`endpointUrl`（票据的受众，与摄入沙箱的 `IDP_SANDBOX_FILES_URL` 一致）、`ticketTtl`（5m）、`concurrency`（**每个副本**，缺省 4，**不得超过 `sandboxConcurrency`**；kind 有两个副本，4×2 = 8 正好等于沙箱的并发）、`sandboxConcurrency`（8）、`maxAttempts`（6）、`runSeconds`（300，不得超过摄入实例的 `IDP_SANDBOX_TIMEOUT_S`）、`callTimeout`（缺省 `runSeconds`+90 秒；**必须大于系统网关的 `IDP_CALL_TIMEOUT`**）、`jobTimeout`（15m）、`lease`（20m，大于 `jobTimeout`）、`interval`（缺省 5 秒；验收用 2 秒）、`maxResultBytes`（6 MiB）、`maxConcurrentReads`（8）。**暂时的错误**（沙箱满、网关 401／429／5xx、连不上、超时、对象存储不可达）退避 30 秒起翻倍、至多 30 分钟、共 `maxAttempts` 次；**永久的**（`corrupt`、`unsupported`、`limits`、`no_text`、`conversion_timeout`、`conversion_oom`、`empty_output`、`text_too_large`、`quota_exceeded`、`import_refused` 等）立即结案。Kubernetes：Service `idp-cmi-ingest`（ClusterIP，端口 8551，**不暴露到集群外**） | 启动日志 `conversion of originals is on`；缺哪一项，启动即拒绝；`idp_cmi_conversions_total{outcome,code}` | compose 由 `e2e.sh` 写 `.local/platform/cmi.yaml`；kind 在 `deploy/kubernetes/full/kustomization.yaml` 的 `idp-cmi-config`（`__SYSTEM_TENANT__`、`__SYSTEM_SUBJECT__` 由 `tests/kind/full.sh` 替换） |
| NetworkPolicy（kind；compose 用网络） | 摄入沙箱的 Pod **只**到 CMI 的文件端点（8551）加 API 服务器（它要建运行的 Job）；CMI 到系统网关（3221）与令牌端点（Keycloak 7080，已有）；系统网关到注册服务、Keycloak、摄入沙箱，入站只收 CMI；注册服务另放行系统网关；运行 Pod 无网络；**成员侧的 Pod（HOST、Workspace、idp-web、EER 等）连不到文件端点和系统网关**（系统网关的 Pod 标签是 `gateway-system`，不是 `gateway`，所以成员侧网关的放行规则管不到它）。compose：`ingestnet`（internal）只有 CMI、摄入沙箱、系统网关与注册服务，文件端点只监听 `10.253.77.2`；系统网关在 compose 里为验收发布在回环端口 3321 并在 `edge` 上（取 Keycloak 的密钥），**compose 窄不了这一条**，靠令牌受众。`catalog/integrations.yaml` 有三条 `kubernetes: full-only` 的集成（`cmi-mcp-ingest`、`mcp-sandbox-ingest-tools`、`sandbox-ingest-cmi-files`，可带 `from_namespace`／`to_namespace`），`catalog/infrastructure.yaml` 有 `keycloak-token-cmi-ingest`；`tools/k8s_check.py --full` 通过（16 个命名空间、31 条流），不加 `--full` 的逐组件检查跳过 `full-only` 的 | PATH P2f（kind：探针 Pod 站在各命名空间里按标签试连 Pod 地址）；`tools/k8s_check.py --full` | 生产里的网络换成自己的 CNI；规则的语义不变 |
| 契约 `idp.telos.cmi` **1.6.0**（只做新增） | `KnowledgeDocument.ingest`（`state`、`attempts`、`error`、`notice`、`updatedAt`、`nextAttemptAt`）、`status` 增 `pending`、`ContextItem.locator` 增 `slide`、`sheet`、`range`（XLSX 的 `range` 从每块第一列的 Excel 行号算出，如 `A12:F40`；DOCX 的 `page` 只在显式分页符处，Word 的引用主要靠标题路径）。`idp-contracts/idp.telos.cmi/1.6.0` 与 `idp-cmi/api/contracts` 逐字节相同（`validate.py` 核对：345 个用例、49 条不变量）；1.5.0 文件保留；`releases/compatibility-matrix.yaml` 已重生成（HOST 钉 1.4.0、EER 钉 1.2.0，都是"落后但向后兼容"） | `idp-contracts/registry.json`；`python3 tools/platform.py matrix` | 消费者按需升级，不用改 |
| 平台（验收，PATH P2） | compose 与 kind 的验收新增 PATH P2（同一份 `deploy/compose/path_p2.sh`，在 P1 之后、PATH C 之前；用 carol 的个人库，最后按主体擦除 carol）：**P2a** 四种原件各一（PDF 三页按页、DOCX 有标题与表格按标题路径且表格单元格可搜、PPTX 按幻灯片、XLSX 按 `工作表!范围`），文档由 `deploy/compose/kb3_docs.py` 用标准库生成，没有业务文档；**P2b** 摄入沙箱停着时新版本 `pending`、前一版照常可取、新文本搜不到，沙箱回来后新文本可搜、旧文本不再被搜到；**P2c** 10,500 行的表以 `truncated` 结案并在文档上带说明，前 1 万行可搜、之后的不可搜；**P2d** 扫描件以 `no_text` 失败并保持 `pending`，`POST …/ingest` 再排队，没有原件的文档 422，成功的转换可以再来一次；**P2e** 令牌（见上）；**P2f** 网络与伪造的票据（HTTP 403）；**P2g** 按主体擦除后文本、作业、用过的票据与对象都不剩，搜不到。等待都有上限并用时钟计时 | `PASS: PATH P2a` 到 `P2g` | 见各条 PASS 行里的用时 |
| 验收的数字（2026-11-25，本机 20 CPU；compose 与 kind 各一轮，镜像已构建；**机器同时被别的任务占着**） | **转换**：PDF 三页、DOCX、PPTX、XLSX 各 2 到 3 秒（compose，运行是 Docker 容器）、22 到 24 秒（kind，运行是 gVisor 下的 Job，大头是起 Pod 与 ConfigMap 搬运）；10,500 行的表 4 秒（compose）／22 秒（kind），截断在 10,000 行；扫描件 3 秒／16 秒失败；摄入沙箱停着时的新版本：第一次尝试在上传后 1 到 2 秒，沙箱回来后 30 秒左右（`gateway_unavailable` 退避 30 秒）／40 秒完成；擦除 2 到 5 秒 verified。**投影队列**：每个块一个投影事件，要嵌入；10,000 行的窄表约 100 到 140 个块，**嵌入服务在 4 CPU 上把它们排完要 2 到 17 分钟**（块多了就是 255 个，PATH C 在 kind 上因此失败过一次：排在它前面的事件太多，记忆记录来不及投影），所以 PATH P2g 在擦除之后**等投影队列排空**（上限 30 分钟，用时写在 PASS 行里：compose 986 秒、kind 138 秒），之后才是 PATH C；**CMI 的擦除不删排队的投影事件**，被擦掉的块仍会占队列到处理完（"[erased]"文本也被嵌入一遍）——这是 CMI 的行为，不是平台的，记在这里。**kind 节点容器内存峰值 11.3 GiB**（含两个嵌入实例、gVisor 里的运行 Pod 与摄入沙箱） | 各 `PASS: PATH P2*` 行 | CI 的预算：kind 一轮在 KB2 的基础上再加约 20 分钟 |
| 已知的限制与没做的（D70） | 扫描件与 OCR（KB4；`no_text` 是它的入口）；对象存储里崩溃留下的暂存对象没有后台清扫（下一次擦除才会找到，占存储）；票据密钥不能无缝轮换；内部文件端点缺省明文；工作区里短暂放着原件明文（一个崩溃的 worker 最多留到摄入沙箱的闲置回收，5 分钟），"擦除之后沙箱里没有明文"不是这里的保证；**compose 窄不了系统网关**（靠令牌的受众，见 PATH P2e），kind 的 NetworkPolicy 窄得了（PATH P2f）；运行在 gVisor 里时 `RLIMIT_AS` 是否与 runc 下一样，idp-sandbox 没有量，这里的转换在 gVisor 下都成功了，但没有测大文件 | — | — |
| idp-cmi（票据密钥，D61 的启动检查） | CMI 启动时**拒绝**两种票据密钥文件：组或其他用户可读的，和不同字节少于 16 种的（十六进制字符串只有 16 种字节，正好被拒） | CMI 起不来，日志说密钥文件的权限或强度不够 | 用 `openssl rand -base64 48` 生成；文件属 CMI 的用户、权限 0400。compose 由一次性服务 `ingest-key-init` 拷进卷 `ingestkey`；Kubernetes 的 Secret 卷是 0644，所以 CMI 的 Deployment 用初始化容器 `ingest-key` 把它拷进内存 emptyDir，权限 0400 |
| idp-cmi（工作负载身份，D70 的更正） | 网关从令牌的 `sub` 取调用者，所以 CMI 的 `workloadSubject` 要等于服务账户用户的 **id**，不是 `preferred_username`。dev/test 的 realm 给 `idp-cmi-ingest` 的服务账户用户一个固定 id，写在 `clients.json` 的 `ids.idp-cmi-ingest` | 摄入实例拒绝票据，报"文件引用属于别人" | 换身份系统时，同样把这个服务账户的 `sub` 写进 `workloadSubject` |

## 升级须知（KB8 的 BOM，2026-11-26）

从 KB3 的 BOM 升到这个 BOM：**HOST 有了三个知识检索工具，CMI 有了读与找**（知识库计划 `docs/adr/knowledge-base-plan.md` 第 2.9、2.10、2.11、8 节 KB8；两边共同遵守的约定 `docs/adr/kb8-cmi-api.md`；idp-cmi ADR-0001 第 27 节 D71 到 D78；idp-host ADR-0001 第 47、48 节 D65 到 D69）。变了的组件：idp-cmi（迁移 0019、契约 1.7.0、`POST /v1/context/read`、`POST /v1/context/find`、解析的两个新过滤、`awaitsOriginal`）、idp-host（迁移 0014 到 0016、契约 1.12.0、三个内置工具、问题改写、新设置）。**升级顺序**：先 idp-cmi（迁移 0019，再滚动），再 idp-host（迁移 0014 到 0016，再滚动）；HOST 的工具只在发布件里开了、并且部署满足下面的条件时才出现，所以先升级、后启用，已有发布件的行为一字不变（没有开工具的发布件看到的文字与 KB8 之前相同，D69-5、D69-15）。

| 组件 | 变了什么 | 怎么看出来 | 怎么处理 |
|---|---|---|---|
| idp-cmi（迁移 0019，D76、D77） | 四列：`knowledge_documents.in_force_since`（版本变成 active 的那一刻，asOf 视图只从这一刻起看得到它，也只从这一刻起取代前一版）、`text_original_sha256`（提取出来的文本来自哪一份原件），`knowledge_chunks.cut_after`（这一块在硬切处结束，`find` 据此接缝），`context_packages.consulted`（读与找查过的版本，没有条目的包也记，擦除靠它找到包）；一个触发器（版本从 pending 变成 active 或 superseded 时设 `in_force_since`）。**回填读每个租户的行**，迁移里对涉及的表 `NO FORCE ROW LEVEL SECURITY`、事务结束前恢复（同 0013）；`in_force_since` 对不是 pending 的版本取 `created_at`（生效的真实时刻在 0019 之前没有记，撤回的版本分不清是在等文本时撤的还是生效后撤的，分不清时按"生效过"算）；`cut_after` 是推断：接近长度上限的散文块、同一节还有后续块的，记为未知（`NULL`），`find` 在接缝两种拼法都试一遍，其余记 false。**按停机处理**：`knowledge_documents` 与 `knowledge_chunks` 整表更新、迁移期间持锁，时间随语料增长（评审时没有给出测得的数字：升级前在预发布环境对生产量级的拷贝跑一遍并记下）。迁移 Job 与别的迁移一样，先跑完再滚动 | `\d knowledge_documents`；迁移 Job 完成；`SELECT count(*) FROM knowledge_documents WHERE in_force_since IS NULL AND status <> 'pending'` 为 0 | 回滚到旧镜像之前想一想：旧版本不认识 `pending` 行的新来源（KB3 起）；0019 之后的行不能被旧版本的 asOf 逻辑正确读 |
| **`awaitsOriginal` 与行为变化**（idp-cmi D77，契约 1.7.0 写入字段） | **给已经生效的版本存要转换的原件，现在是 409**（active 且文本是写入者给的、superseded、revoked、expired；PDF、Word、PowerPoint、Excel）。1.6.0 会把这样的版本改成 `pending`，现在要这样写：**新写一个版本、带 `awaitsOriginal: true`**（一开始就是 `pending`，文本提交的那一刻才生效，前一版在那之前照常可取），再 `PUT …/original`。服务没有开转换（没有 `knowledge.conversion`）时带 `awaitsOriginal` 是 503。已经转换过的版本再转同一份原件（`POST …/ingest`，或再 PUT 同一份文件）仍可以，这是唯一的原地更正；换另一份原件是 409，不论转换开不开。文本、Markdown 等不转换的原件不受影响。**已知的调用者只有平台的验收脚本**（`deploy/compose/path_p2.sh`，已改）；自己的上传程序、Workspace 的上传页（KB5）要改成先写带 `awaitsOriginal` 的版本 | 对 active 版本 `PUT …/original` 的 PDF 得 409（PATH P2d 检查）；`ingest` 的状态 | 升级前找出所有"先写文本、再传 PDF"的调用方；没有改的，它们的上传会得 409，不会静默改变文档 |
| idp-cmi（新路由与新过滤，D71 到 D75、D78） | `POST /v1/context/read`（读一份文档的指定位置：`page`、`slide`、`headings` 前缀、`sheet`+`range`，前后各带 `around` 块）、`POST /v1/context/find`（在一份文档里按原词找：子串、空白规范化、拉丁字母不分大小写，条目 `match: exact`，`hits` 是在条目 `excerpt` 里的位置，`hitCount`）、解析的 `knowledgeBases`、`docTypes`。读与找**不另造权限**，可见谓词与解析相同（敏感上限、ACL、PDP、库范围、现行版本），**看不到一律 404，不说原因**，PDP 拒绝也是 404（审计照记）。**别的成员个人库里的文档，读与找要请求明确点名那位成员（`scope: {kind: subject, id}`）且调用者有权点名**，不点名时写入服务与管理员也是 404（HOST 永远不点名）；点名时记 `knowledge.read_other`。`sources`（至多 32 个）是 SQL 里的可见谓词。包与解析的包一样密封、可核验；擦除时条目的摘录、标题、`hits`、标题路径换成墓碑，**没有条目的包（一次没找到的 find）的 `query` 也被擦掉**（它说了找的是什么） | PATH P3b、P3g | 调用方不要用服务令牌替成员读：见下面 HOST 一行 |
| idp-host（迁移 0014 到 0016，D68、D69-6、D69-9、D69-13） | 0014：`runs.cmi_credential`（成员的 CMI 令牌，用同一把 `IDP_HOST_CREDENTIAL_KEY` 密封；运行结束与取消时清除）；0015：`cmi_credential_ref`（子运行指向根运行那一份，不存副本，一个 refresh token 只在一处续期）；0016：`request_raw_sha256`（调用方原样请求的摘要，幂等重放在交换令牌、改写、检索**之前**就找到已存的运行；更早创建的运行是 NULL，仍由旧摘要找到）、`cmi_renewing_until`（续期的短租约，续期时不持行锁去调授权服务器）。三个都是加一列，不改写行 | `\d runs` | 先迁移再滚动；没有 `IDP_HOST_CREDENTIAL_KEY` 时，开了知识工具的发布件创建运行是 409 |
| **`IDP_HOST_MEMBER_ROLES`**（idp-host D68、D69-1，**失败关闭**） | 逗号分隔的角色：**主体持有其中至少一个，HOST 才保存他的 CMI 令牌、才提供三个工具**；**没有配置就没有人**，发布件开了工具也一个都不会提供。A2A 进来的运行，不论对端有什么角色，都不保存令牌、不提供工具。平台设 **`ws-member,ws-admin`**（人的角色；服务账号只有客户端写死的角色，所以 EER 的 Intelligence 等服务账号永远没有这些工具）。持 `ws-admin` 的成员的模型也读不到别人的个人库（HOST 不点名，CMI 要点名，D78） | PATH P3；HOST 启动日志；compose 在 `deploy/compose/platform.yaml` 的 `host-env`，kind 在 `deploy/kubernetes/full/kustomization.yaml` 的 `idp-host-env` | 生产里按身份提供方的角色名设；**别把服务账号的角色放进去** |
| **CMI 要委派并 `sessionBound`**（idp-host D68） | 三个工具**只用成员的令牌**，从不用 HOST 自己的 CMI 密钥或自己签发的服务令牌（CMI 让写入服务与管理员凭文档 id 读任何成员的个人库，服务令牌一旦被工具用上，每个成员的个人库就对模型开着）。所以 `cmi.auth.mode` 必须是 `delegation`（RFC 8693 令牌交换，受众 `idp-cmi`）；**`cmi.auth.sessionBound: true`**：交换带 refresh token（`standard.token.exchange.enableRefreshRequestedTokenType: SAME_SESSION`），worker 在过期前续期、CMI 回 401 时续期一次再发，续期后存回；不是 sessionBound 时令牌到期后工具回"不可用"，运行照常做完。CMI 是静态密钥或 `client_credentials` 接入时，这三个工具不提供，开头那次解析、D24 的复验与 D33 的写回仍照旧用 HOST 自己的凭据。令牌存不下或解不开（密钥轮换、损坏）时工具不提供，运行不挂（D69-4）。**Keycloak（只在 dev/test）**：HOST 的客户端 `idp-host` 本来就有 `standard.token.exchange.enableRefreshRequestedTokenType: SAME_SESSION`、受众 `idp-cmi` 与成员的 `tenant_id`、`roles` 映射，**`deploy/keycloak/realm.py` 没有为此改客户端**；只多了两个成员用户 `dave`、`erin`（`ws-member`、`host-user`、`cmi-reader`），给 PATH P3 用（bob 与 carol 被 P0、P2 擦除） | PATH P3a（CMI 记的请求者是成员，不是 HOST 的服务账号）、P3e（运行结束后 `runs.cmi_credential` 为空） | 换身份提供方时：支持"带 refresh token 的、绑定会话的令牌交换"（RFC 8693 的 SAME_SESSION 的对应物），否则只能不绑定会话 |
| **调到 HOST 的令牌也要带会话**（2026-11-26 kind 验收发现） | HOST 的 `cmi.auth.sessionBound` 要求它收到的令牌属于一个会话；上游（Workspace 给 HOST 的令牌交换）不带会话时，身份系统拒绝交换（Keycloak 报 `Refresh token not valid as requested_token_type`） | HOST 照 `onError: skip` 跳过运行开始那次检索，运行上没有 `contextPackageId`，三个知识工具也不可用；HOST 日志有 `CMI context skipped … token exchange refused` | Workspace 给 HOST 的交换同样设 `sessionBound: true`（`deploy/kubernetes/full/kustomization.yaml`、compose 的 Workspace 配置都已设）；换身份系统时，确认它能为会话内的令牌换出带刷新令牌的令牌（RFC 8693） |
| 发布件的 `context.tools` 与 `context.rewrite`（idp-host D65、D66，契约 1.12.0） | `context.tools`：`knowledge__search {query, entities?, asOf?, knowledgeBases?, docTypes?}`、`knowledge__read {documentId, locator?}`、`knowledge__find {documentId, text}`，要 `context.cmi: true`，至多三项、不重复；工具不经网关、前缀 `knowledge__` 归 HOST（允许列表不能写，网关同名的工具也不提供）；参数严格校验（键名逐字符、类型、长度），校验不过不发给 CMI；CMI 的错误只映射成 HOST 自己的话（404 = "not found or not visible"，401/403 = 不可用或无权，422 = 请求被拒，429 = 忙，其余 = 服务不可用），CMI 的正文、地址、令牌不进结果；`toolExecution: caller` 的运行不提供。每次调用一个包、一条事件 `idp.host/context.package {packageId, digest, tool, query | documentId+locator | documentId+text, itemCount, firstIndex, returned}`；**引用编号在一次运行里连续**（开头的包的知识条目编 `[1]…`，工具的包接着编，计数器就是运行自己事件里最后一个编号之后的数，所以接手与重启都接着编）；**D44**：带对外工具或可能用外部模型的运行，结果按租户上限筛，扣下的记 `idp.host/context.withheld`，**HOST 不把敏感上限传给 CMI**（传了条目会在 CMI 那里悄悄消失，记不到、也告诉不了模型）；**没有记下上限而带对外工具的运行，知识工具的结果全部扣下**（`noCeiling: true`）；工具结果在线程历史里一律包成不可信。`context.sources` 对 read 与 find 同样有效（交给 CMI，范围外的文档答 404）。`context.rewrite: off`（缺省）或 `model`：线程里已有前文时，运行开始那次解析之前，由发布件自己的模型（HOST 受管的模型调用：低思考强度、温度 0、输出上限 128 token、只给最近 6 条前文各截 600 字符；回答必须是非空的一行、至多 300 字符、不被输出禁用模式命中，否则用原话）把问题改写成不依赖上文的一句；事件 `idp.host/context.rewrite {mode, outcome: rewritten | unchanged | fallback, original, query, reason?, provider, model, promptTokens, completionTokens, latencyMs}` 在 `context.resolved` 之前，`resolved` 也带 `query`；改写先问 D15 的额度，token 记进运行的第 0 步与 `runs.tokens_used`；读租户策略失败时不改写；幂等重放在改写之前返回已存的运行。**平台验收用的发布件**（PATH P3 自己创建）：三个工具全开加 `rewrite: model`、同样但 `rewrite` 关、限定来源、用外部模型、没有工具 | PATH P3a、P3c、P3d、P3f | 发布件按需开；**已有发布件不用改** |
| `IDP_HOST_REWRITE_TIMEOUT` 与 `providers[].reasoningEffort` | `IDP_HOST_REWRITE_TIMEOUT`：改写那次模型调用的总时限，缺省 `8s`（创建运行的请求因此最多多等这么久，只在有前文且发布件开了改写时）；超时用原话，事件 `outcome: fallback`、`reason: timeout`。`providers[].reasoningEffort: true`：声明这个提供者认 OpenAI 兼容的 `reasoning_effort`，改写才把"低"发给它；没有声明就不发，OpenAI 的非推理模型不会因此拒绝。平台的模型替身两处都设了（compose 的 `host.yaml` 由 `e2e.sh` 写，kind 在 `deploy/kubernetes/full/kustomization.yaml` 的 `idp-host-providers`），另有一个同样的提供者 `local-ext`（没有 `external: false`，即对外的模型），只有 PATH P3c 的发布件用它 | PATH P3d（`run_steps` 第 0 步在改写开着时有一行、关着时没有） | 其它适配器忽略思考强度；D63-2 的其余部分没有做 |
| 契约版本 | `idp.telos.cmi` **1.7.0**（只做新增：`ContextItem.match` 增 `exact`、增 `hits`、`hitCount`，`KnowledgeDocument` 增写入字段 `awaitsOriginal`；**行为差别在契约之外**：见上面 409）；`idp.telos.host` **1.12.0**（只做新增：`ContextPolicy.tools`、`ContextPolicy.rewrite`；事件 `context.package`、`context.rewrite` 与 `context.resolved` 的新字段不属于 schema）。`idp-contracts` 里的副本与组件仓库的 `api/contracts` 逐字节相同（`validate.py`：362 个用例、51 条不变量）；旧版本文件保留；**HOST 钉着的 CMI 契约副本还是旧版本，矩阵里记为"落后但向后兼容"**，HOST 之后在自己的仓库里加 1.7.0 的副本 | `idp-contracts/registry.json`；`python3 tools/platform.py matrix` | 消费者按需升级 |
| 平台（验收，PATH P3） | compose 与 kind 的验收新增 PATH P3（同一份 `deploy/compose/path_p3.sh`，在 P2 之后、PATH C 之前；成员是 dave 与 erin，管理员是 alice；用 HOST 的模型替身的 `know:`、`know-dig:`、`know-read:`、`call:` 流与它的改写，没有任何别的模型调用）：**P3a** dave 的运行先搜再在第一个结果的文档里找一段话、另一次先搜再读第 2 页，每次调用一个包事件，编号接着开头的包往下编，回答引用的编号都是给过的，CMI 记的请求者是 dave 而不是 HOST 的服务账号；**P3b** dave 读自己的个人笔记可以，读 erin 的是 "not found or not visible"，alice（`ws-admin`）经 HOST 的工具读 dave 的笔记也是（read 与 find），alice 直接调 CMI：不点名 404、点名 dave 的主体作用域读到并记 `knowledge.read_other`；**P3c** 同一个问题，外部模型的运行里机密段落被扣下（`context.withheld`，byLevel 机密），企业自己的模型的运行里给出（这一步里租户的 `readerCeiling` 临时升到 `confidential`，用后放回，同 PATH N (d)）；**P3d** 追问被改写，CMI 收到的是改写后的问句，关掉改写时没有改写事件、CMI 收到原话、运行没有第 0 步（改写的那次模型调用）；**P3e** 运行结束后这条路径的运行没有一个还留着 `runs.cmi_credential`；**P3f** `context.sources` 限定时 read 与 find 别的来源的文档是 "not found or not visible"，没有来源规则的发布件读得到；**P3g** 擦除文档后读与找做的包（包括一次没找到的 find）被擦掉，`knowledge__read` 找不到它。CI 的必过清单里加了 P3a 到 P3g | `PASS: PATH P3a` 到 `P3g` | 模型替身没有按请求计数的接口，"没有多余的模型调用"靠运行的第 0 步与改写事件证明；没有在运行**进行中**检查令牌确实存着（要一个停在确认上的运行，没做） |
