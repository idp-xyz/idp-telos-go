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
