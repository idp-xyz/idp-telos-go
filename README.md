# idp-telos-go

IDP TELOS 的 Go 实现。本仓库是平台仓库（仓库工程架构中的 idp-telos）：承载 `idp.telos.*` 契约登记、组件目录、平台发布清单与兼容性矩阵、整平台组装与跨系统验收、架构文档和品牌资源（平台 ADR 为本地文档 `docs/adr/ADR-0001-platform-integration.md`）。七个运行时组件各自是独立仓库，按约定并排克隆在本目录下，并被 `.gitignore` 忽略；本仓库不含它们的源码。

| 目录 | 内容 | 仓库 |
|---|---|---|
| `idp-mcp/` | MCP 基础设施：`mcp-gateway`（数据面）与 `registry-service`（控制面）。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-mcp-infrastructure.md` | [idp-xyz/idp-mcp](https://github.com/idp-xyz/idp-mcp) |
| `idp-host/` | Agent Hosting & Coordination：Agent 与 Skill 发布、Run 状态机、harness、模型适配器（OpenAI 兼容 / Anthropic / Gemini / 外部 harness worker）与路由、经 idp-mcp 的工具调用（含 Keycloak 委派）、子 agent、租户配额、内容护栏、评估门禁、AG-UI 与 A2A。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-host-design.md` | [idp-xyz/idp-host](https://github.com/idp-xyz/idp-host) |
| `idp-eer/` | Enterprise Execution Runtime：持久化 Agent Loop，Agent Turn 由 idp-host 提供（ADR-036），Decision 从 idp-cmi 取上下文并留下记忆（ADR-037），T2 审批等待推入 idp-workspace 收件箱（ADR-038）；钉住契约 `idp.telos.host` 1.5.0 与 `idp.telos.cmi` 1.2.0（登记版本对其只有新增，见兼容性矩阵） | [idp-xyz/idp-eer](https://github.com/idp-xyz/idp-eer) |
| `idp-cmi/` | Context, Memory & Knowledge Infrastructure：来源与信任级别、记忆记录（来历、有效期、撤销）、知识文档与全文索引、密封的上下文包、敏感级别与 ACL。M1–M6 完成并实测（混合检索、写入时注入隔离、实体引用、整合 Worker、连接器、评估套件、Kubernetes 清单、compose e2e 含与 idp-host 的联调；幂等与版本、受控擦除、候选 / 存疑、单元作用域与共享、外部 PDP、消费时复验、双时态、outbox 投影、标注评估集；未进包报告、外部模型敏感上限、审批核验、来源可追溯率）；设计见其 `docs/adr/ADR-0001-cmi-design.md` | [idp-xyz/idp-cmi](https://github.com/idp-xyz/idp-cmi) |
| `idp-workspace/` | 企业工作入口：工作空间与成员、应用注册与安装（不可变发布、可撤销）、工作收件箱（生产者推入、只作视图）、工作上下文、动作契约（转给 idp-host 运行、idp-eer 审批决定或签名的应用 webhook）与应用桥 SDK（`packages/bridge`，idp.bridge/v1）、Web 壳（`apps/web`，React，收件箱实时推送，Agent 运行结果回到收件箱）。OIDC 登录、应用令牌、下游令牌交换。M1–M3 完成并实测（含与真实 idp-host、Keycloak 的联调与 Chromium 冒烟）；设计见其 `docs/adr/ADR-0001-workspace-design.md` | [idp-xyz/idp-workspace](https://github.com/idp-xyz/idp-workspace) |
| `idp-sandbox/` | Agent 执行沙箱：MCP 服务器，放在 idp-mcp 网关后面作为上游，让 Agent 在无网络、只读根、无特权、资源受限的一次性容器里运行 Python / Shell，文件保存在有大小上限的内存工作区。工具 `sandbox_create` / `_run` / `_write_file` / `_read_file` / `_list_files` / `_delete`。M1 完成并实测（14 个真实容器的逃逸 / 滥用用例，经真实网关的端到端）；容器共享内核、需要引擎套接字、网关不转发调用者身份等限制见其 ADR §5；设计见其 `docs/adr/ADR-0001-sandbox-design.md` | [idp-xyz/idp-sandbox](https://github.com/idp-xyz/idp-sandbox) |
| `idp-web/` | Agent 联网：MCP 服务器，放在 idp-mcp 网关后面作为上游，让 Agent 搜索网页、读取网页正文。工具 `web_search` / `web_fetch` / `web_open_url`；`web_fetch` 只读搜索结果里出现过的地址（签名的 `ref`），`web_open_url` 读成员给的地址（租户文件里默认关，确认由 HOST 强制）；地址检查与 SSRF 防护、正文提取、租户名单、限流、审计（查询词明文）。设计见平台的 `docs/adr/ADR-0003-web-search-and-fetch.md` 与其 `docs/adr/ADR-0001-web-design.md`；compose 与 Kubernetes（kind）上都已接入（PATH M、N；清单在 `idp-web/deploy/kubernetes`） | [idp-xyz/idp-web](https://github.com/idp-xyz/idp-web) |
| `idp-mcp-bridge/` | 外联桥：MCP 服务器，放在 idp-mcp 网关后面，每条连接一个端点，替成员调用第三方 MCP 服务；成员各自用 OAuth（授权码 + PKCE、客户端元数据文档或预注册客户端）授权一次，令牌密封在桥自己的 PostgreSQL 里，第三方只收到这个成员自己的令牌、收不到任何 TELOS 身份。计划见 `docs/adr/idp-mcp-bridge-oauth-plan.md`；设计见其 `docs/adr/ADR-0001-bridge-design.md` | [idp-xyz/idp-mcp-bridge](https://github.com/idp-xyz/idp-mcp-bridge) |
| `idp-browser/` | 渲染读取（浏览器能力计划第 1 层）：渲染器（headless Chromium，POST /render，只收 idp-web 签名的请求）与它唯一的出口——只拨公共地址的出口代理（每次渲染有自己的凭据与预算：300 个请求、20 MiB）；idp-web 把需要脚本的页面和租户的 `alwaysRender` 主机交给它读一次，渲染结果仍走 idp-web 的提取、引用与审计。不存任何数据；两个镜像 `idp-browser`、`idp-browser-proxy`；隔离：gVisor（RuntimeClass）下 Chromium 关自己的沙箱，否则用 Chromium 自己的。计划见 `docs/adr/browser-capability-plan.md`。PATH M6（compose 与 kind 同一份 `deploy/compose/path_m6.sh`） | [idp-xyz/idp-browser](https://github.com/idp-xyz/idp-browser) |
| `idp-contracts/` | `idp.telos.*` 契约登记：`idp.telos.mcp` 1.1.0、`idp.telos.host` 1.7.0、`idp.telos.cmi` 1.5.0、`idp.telos.workspace` 1.1.0（各由其生产者产出并由测试钉住）；四条规则见其 README | 本仓库 |
| `catalog/` | 组件目录、允许的跨系统调用及其证据、状态权威归属 | 本仓库 |
| `releases/` | 平台发布清单 `platform-bom.yaml`（组件提交、镜像、契约版本）与兼容性矩阵 `compatibility-matrix.yaml`，由 `tools/platform.py` 生成与校验 | 本仓库 |
| `deploy/` | 整平台组装（`deploy/compose/platform.yaml`：Keycloak（https 发行者）、idp-cmi、idp-host、idp-workspace、idp-eer、idp-mcp 的注册服务与网关、idp-sandbox 及其出口代理、idp-web、idp-mcp-bridge 与 Temporal）与跨系统验收（`deploy/compose/e2e.sh`：PATH A 问答经 Workspace→HOST→CMI；PATH B T2 审批经 EER→Workspace 收件箱→成员批准→EER 完成；PATH D 成员让 Agent 运行代码：Workspace→HOST→idp-mcp 网关（HOST 为成员换来的令牌）→idp-sandbox（网关转发调用者）→隔离容器；PATH L 出口：带 `network=egress` 的运行只能经沙箱的出口代理访问到成员所在租户名单上的主机；PATH E 记忆：成员说过的话由 HOST 写进 CMI（情景记忆，作用域为该成员），新对话的上下文包里能取回，同一对话保留历史；PATH F 成员在壳里查看并删除自己的记忆（经 Workspace 以成员身份访问 CMI，别人删不了）；PATH M 成员让 Agent 联网检索：Workspace→HOST→网关→idp-web（搜索用 `fixture`，源站是会记录请求的测试容器），回答引用 `[1]`，网关记录名为该成员，idp-web 的审计有查询词与读取的地址，未经网关签名的调用被拒；PATH N 页面里的指令让模型把数据发往"攻击者"：idp-web 拒绝没有对应 `ref` 的地址，`web_open_url` 由 HOST 的配置停下等成员确认（发布件没有要求），攻击者容器的访问日志为空，高于敏感级别上限的 CMI 条目不给带联网工具的运行（idp-host D44）；PATH O 成员用自己的 OAuth 授权访问第三方 MCP 服务：第三方是会记录每个请求的测试容器（授权服务器与受保护的 MCP 服务合在一起），bob 在 Workspace 里连接，令牌在桥的库里是密文，他的运行经 HOST→网关→桥调用第三方，第三方只收到 bob 的令牌；alice 没授权，得到事件 `idp.host/authorization.required`，第三方没有收到调用；令牌过期刷新一次，错误的 `iss` 什么也不存，断开后第三方看到撤销，任何日志、事件、数据库里都找不到第三方签发的秘密；PATH C EER Intelligence 经平台 CMI） | 本仓库 |
| `docs/` | 平台 ADR、建设记录与运维手册（`docs/reports/`）、架构蓝图、系统架构、仓库工程架构——只保存在本地，不纳入版本库（`.gitignore`） | 本地 |
| `brand/` | 品牌资源 | 本仓库 |

## 工作区布局

```sh
git clone https://github.com/idp-xyz/idp-telos-go.git
cd idp-telos-go
git clone https://github.com/idp-xyz/idp-mcp.git
git clone https://github.com/idp-xyz/idp-host.git
git clone https://github.com/idp-xyz/idp-eer.git
git clone https://github.com/idp-xyz/idp-cmi.git
git clone https://github.com/idp-xyz/idp-workspace.git
git clone https://github.com/idp-xyz/idp-sandbox.git
git clone https://github.com/idp-xyz/idp-web.git
git clone https://github.com/idp-xyz/idp-mcp-bridge.git
git clone https://github.com/idp-xyz/idp-browser.git
```

契约校验器默认在 `idp-contracts/` 的上级目录（即本仓库根目录）里寻找并排克隆的生产者仓库；若它们放在别处，用 `--producer-root` 指定。

命名：契约命名空间是 `idp.telos.<模块>`，`$id` 为 `urn:idp:telos:<模块>:<版本>`，所有者键为 `x-idp-telos-owner`。

契约校验：`uv run --no-project --with 'jsonschema[format]==4.26.0' python -I idp-contracts/tools/validate.py`（退出码 0 表示全部通过且发布副本与 idp-mcp、idp-host、idp-cmi、idp-workspace 的权威副本一致）。

平台：

```sh
uv run --no-project --with PyYAML==6.0.3 python -I tools/platform.py check    # 目录、发布清单、兼容性矩阵
uv run --no-project --with PyYAML==6.0.3 python -I tools/platform.py bom      # 组件移动后重新生成发布清单（matrix 同理）
bash deploy/compose/e2e.sh                                                     # 构建各组件镜像，跑 PATH A 到 N（含 M6：渲染读取）与 O 验收（KEEP=1 保留运行；MODEL=live 用本机真实模型）
uv run --no-project --with PyYAML==6.0.3 python -I tools/k8s_check.py          # 渲染各组件（七个）的 Kubernetes 清单，核对每条集成两端的 NetworkPolicy
bash tests/kind/smoke.sh                                                       # 在 kind 集群上按组件清单部署并跑 PATH A（CI: kind.yml）
bash tests/kind/full.sh                                                        # 整平台上 kind：八个组件的清单 + Keycloak（https 发行者）、PostgreSQL、Temporal，全部命名空间默认拒绝（需要 Calico 等强制执行 NetworkPolicy 的集群），经 port-forward 跑 PATH A、B、D、E、I、L、F、G、H、M、N、O、C（M、N 是联网检索与页面注入，用 idp-web 的清单加测试源站；O 是第三方 MCP 服务与成员各自的 OAuth 授权，用桥的清单加测试第三方，并用探测 Pod 验证网络策略），再装好"助手"应用，浏览器里可从登录、提问、跑代码到审批完整体验（SKIP_BUILD=1 用已有镜像；MODEL=live 用本机真实模型；TEARDOWN=1 结束时删除）
uv run --no-project --with PyYAML==6.0.3 python -I tools/k8s_check.py --full    # 完整覆盖层：每个命名空间 restricted + 默认拒绝，集成与 Keycloak / PostgreSQL / Temporal 的流量两端放行
```

整平台 kind 部署（`deploy/kubernetes/full`，驱动脚本 `tests/kind/full.sh`）与轻量覆盖层（`deploy/kubernetes/kind`，开发令牌，仅 CMI、HOST、Workspace，CI 用）并存；前者在组件自带清单之上只用补丁与额外的 NetworkPolicy，不改组件仓库。

沙箱的出口代理在两种部署里都接上了（idp-sandbox ADR-0001 D13、D21、D22）：`sandbox_run` 带 `network=egress` 的运行只能经代理访问到调用者所在租户名单上的主机，不带的运行没有网络。compose 版的名单是 `.local/platform/egress-tenants.yaml`，`e2e.sh` 每次运行重写它（本次的租户只能访问验收用的测试源站，其他人为空），改后 `docker restart idp-telos-platform-sandbox-egress-1`；代理接受私有地址的开关 `IDP_EGRESS_ALLOW_PRIVATE` 在 `platform.yaml` 里默认关闭，`e2e.sh` 为测试源站把它打开，所以 `KEEP=1` 留下的平台里它是开着的。kind 版见手册的"沙箱的出口代理"。

web 工具（`web__web_search`、`web__web_fetch`、`web__web_open_url`）只授给经 HOST 调用的角色（compose 验收里是 `host-user`），不授给自己直接调用网关的成员角色：`web_open_url` 的确认、敏感级别上限和"页面内容不可信"的标记都在 HOST 里，绕过 HOST 就没有这三道控制（idp-mcp 互通差距文档第 7 节；`deploy/compose/mcp_setup.py`）。compose 上的 idp-web 用 `fixture` 搜索适配器和两个会记录请求的测试容器（profile `web-test`，`IDP_WEB_TEST_ORIGINS` 只由验收设置）；kind 上还没有（任务 P2）。

手册：本机 Kubernetes 测试环境（kind、整平台 PATH A–D、gVisor / Kata）见 [`deploy/kubernetes/RUNBOOK.md`](deploy/kubernetes/RUNBOOK.md)；沙箱的运维见 `idp-sandbox/docs/RUNBOOK.md`。

CI：`ci.yml`（目录、清单、契约、Kubernetes 检查）、`kind.yml`（kind 上的 PATH A）与 `platform.yml`（整平台 PATH A 到 O 验收）。私有的 idp-eer 用只读部署密钥检出（secret `IDP_EER_DEPLOY_KEY`）。`platform.yml` 在 main 上把通过验收的镜像发布为 `ghcr.io/idp-xyz/<镜像>:<组件提交>`（`idp-cmi`、`idp-host`、`idp-workspace`、`eer-runtime`、`idp-mcp`、`idp-sandbox`、`idp-web`），并用 cosign 无密钥签名、附 SPDX SBOM 证明（`tools/publish_images.sh`）。校验签名：

```sh
cosign verify ghcr.io/idp-xyz/idp-cmi:<commit> \
  --certificate-identity-regexp '^https://github.com/idp-xyz/idp-telos-go/.github/workflows/platform.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

第三方镜像在使用前由 `tools/pull_image.sh` 显式拉取并重试（最多 5 次，间隔 15、30、60、120 秒；本机已有则跳过），因为公共 registry 对匿名拉取按来源地址限流，而 CI 的 runner 与许多项目共用地址。

### 第三方镜像的镜像库（mirror）

重试只治标。根治办法：我们依赖的每个第三方镜像在自己的 registry 里留一份副本，按 digest 复制，CI 和验收从那里拉。清单是 [`releases/mirror-images.yaml`](releases/mirror-images.yaml)，复制由 `mirror.yml` 做（手动触发，或 main 上清单、该工作流、`tools/mirror_images.py` 有改动时）。

- **命名规则（无例外）**：所有镜像都是**同一个包** `ghcr.io/idp-xyz/mirror` 的 tag（GHCR 新包默认私有且只能在网页上改可见性，所以只要点一次，以后加镜像不用再点）。tag 把源编码成 `<主机>__<路径各段用 __ 连接>__<源 tag>`（字符集 `[A-Za-z0-9_.-]`，最长 128；`check` 拒绝两个条目编码成同一个 tag）。例：`public.ecr.aws/docker/library/postgres:17-alpine` → `ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine`；`docker.io/rustfs/rustfs:1.0.4-glibc` → `ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc`；`quay.io/keycloak/keycloak:26.8.2` → `ghcr.io/idp-xyz/mirror:quay.io__keycloak__keycloak__26.8.2`。清单里每条都写着完整的源引用，不用反解。引用镜像写 `ghcr.io/idp-xyz/mirror:<tag>@sha256:<digest>`。Docker Hub 一律写 `docker.io/…`（官方镜像用 `docker.io/library/…`，或它在 ECR Public 上的同内容副本，见清单里的 `aliases`）。
- **范围**：CI 和验收匿名拉取的所有 registry：ECR Public 与 Docker Hub（限流的主因），quay.io（Keycloak、cert-manager、Calico）和 ghcr.io（CloudNativePG、trivy，同样按来源地址计数），一条规则覆盖全部，代价只是多复制几个镜像。`registry.k8s.io` 目前没有任何引用，所以没有条目。**不镜像**：我们自己的镜像（`ghcr.io/idp-xyz/<镜像>`，由 `platform.yml` 发布）；idp-cmi 本机嵌入评测用的 `ghcr.io/huggingface/text-embeddings-inference`（tag 由变量拼出，CI 不拉）；idp-eer 演示脚本里个人用的国内镜像站；运行时才下载的清单和 Helm chart（它们引用的镜像若 CI 用到，先加进清单）。
- **复制不改镜像**：用 crane（固定版本和压缩包 sha256）`crane copy 源@digest 目标:tag`，不重建、保留全部平台、digest 不变；目标已有该 digest 就跳过；从不删除。label/annotation 会改变 manifest 和 digest，所以什么都不加。首次复制某个条目之前，工作流还检查源上的这个 tag 此刻解析到清单里的 digest；不一致就让该条目失败并打印两个 digest（tag 被源端移动了，需要有意地改清单）。
- **公开**：工作流最后对每个目标做一次不带凭据的 manifest 请求，失败就报"包不是公开的"：到 github.com/orgs/idp-xyz/packages 打开 `mirror`，Package settings → Change visibility → Public（只此一次），再运行一次。工作流不用 API 改可见性。
- **`resolvedVia: mirrors`**：清单里带这个标记的条目，digest 是通过两个互相一致的 registry 镜像站（`resolve --via docker.m.daocloud.io docker.1ms.run`）查到的，因为当时到不了 Docker Hub。它由上面的"源上的 tag 此刻解析到该 digest"检查在 GitHub 上证实；一次运行通过后，删掉该条目的标记。
- **加或升级一个镜像**：先在清单里加条目或改 tag 和 digest（`python3 tools/mirror_images.py resolve` 可从 registry 补出写成 `sha256:TODO` 的 digest），合并后工作流复制，**再**改引用（`python3 tools/mirror_images.py plan --repos <各仓库目录>` 列出每一处）。`ci.yml` 跑 `python3 tools/mirror_images.py check --repos .`：本仓库出现清单外的第三方引用就失败，所以新基础镜像不先进清单就进不了 main；跨仓库的检查在本机跑（`check --repos` 后接八个目录，`--strict` 把未使用的条目和无法检查的引用也算失败）。
- **核对镜像与源相同**：`crane digest ghcr.io/idp-xyz/mirror:<编码后的 tag>` 应等于清单里 `source` 的 digest，也等于 `crane digest <源>:<tag>`（tag 可能已被源端移动，以清单的 digest 为准；`python3 tools/mirror_images.py resolve --verify` 逐条比对）。镜像是原样复制，`docker buildx imagetools inspect` 看到的平台列表与源一致。
- **本机**：`python3 tools/mirror_images.py retag` 把本机 Docker 里已有的源镜像打上镜像名（从不拉取），改引用后不必为慢网络重新下载。

## 许可证

本仓库采用 [PolyForm Noncommercial License 1.0.0](LICENSE)：允许非商业用途，禁止任何商业使用。
