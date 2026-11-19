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
| `idp-contracts/` | `idp.telos.*` 契约登记：`idp.telos.mcp` 1.1.0、`idp.telos.host` 1.7.0、`idp.telos.cmi` 1.4.0、`idp.telos.workspace` 1.1.0（各由其生产者产出并由测试钉住）；四条规则见其 README | 本仓库 |
| `catalog/` | 组件目录、允许的跨系统调用及其证据、状态权威归属 | 本仓库 |
| `releases/` | 平台发布清单 `platform-bom.yaml`（组件提交、镜像、契约版本）与兼容性矩阵 `compatibility-matrix.yaml`，由 `tools/platform.py` 生成与校验 | 本仓库 |
| `deploy/` | 整平台组装（`deploy/compose/platform.yaml`：Keycloak（https 发行者）、idp-cmi、idp-host、idp-workspace、idp-eer、idp-mcp 的注册服务与网关、idp-sandbox 及其出口代理、idp-web 与 Temporal）与跨系统验收（`deploy/compose/e2e.sh`：PATH A 问答经 Workspace→HOST→CMI；PATH B T2 审批经 EER→Workspace 收件箱→成员批准→EER 完成；PATH D 成员让 Agent 运行代码：Workspace→HOST→idp-mcp 网关（HOST 为成员换来的令牌）→idp-sandbox（网关转发调用者）→隔离容器；PATH L 出口：带 `network=egress` 的运行只能经沙箱的出口代理访问到成员所在租户名单上的主机；PATH E 记忆：成员说过的话由 HOST 写进 CMI（情景记忆，作用域为该成员），新对话的上下文包里能取回，同一对话保留历史；PATH F 成员在壳里查看并删除自己的记忆（经 Workspace 以成员身份访问 CMI，别人删不了）；PATH M 成员让 Agent 联网检索：Workspace→HOST→网关→idp-web（搜索用 `fixture`，源站是会记录请求的测试容器），回答引用 `[1]`，网关记录名为该成员，idp-web 的审计有查询词与读取的地址，未经网关签名的调用被拒；PATH N 页面里的指令让模型把数据发往"攻击者"：idp-web 拒绝没有对应 `ref` 的地址，`web_open_url` 由 HOST 的配置停下等成员确认（发布件没有要求），攻击者容器的访问日志为空，高于敏感级别上限的 CMI 条目不给带联网工具的运行（idp-host D44）；PATH C EER Intelligence 经平台 CMI） | 本仓库 |
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
```

契约校验器默认在 `idp-contracts/` 的上级目录（即本仓库根目录）里寻找并排克隆的生产者仓库；若它们放在别处，用 `--producer-root` 指定。

命名：契约命名空间是 `idp.telos.<模块>`，`$id` 为 `urn:idp:telos:<模块>:<版本>`，所有者键为 `x-idp-telos-owner`。

契约校验：`uv run --no-project --with 'jsonschema[format]==4.26.0' python -I idp-contracts/tools/validate.py`（退出码 0 表示全部通过且发布副本与 idp-mcp、idp-host、idp-cmi、idp-workspace 的权威副本一致）。

平台：

```sh
uv run --no-project --with PyYAML==6.0.3 python -I tools/platform.py check    # 目录、发布清单、兼容性矩阵
uv run --no-project --with PyYAML==6.0.3 python -I tools/platform.py bom      # 组件移动后重新生成发布清单（matrix 同理）
bash deploy/compose/e2e.sh                                                     # 构建各组件镜像，跑 PATH A 到 N 验收（KEEP=1 保留运行；MODEL=live 用本机真实模型）
uv run --no-project --with PyYAML==6.0.3 python -I tools/k8s_check.py          # 渲染各组件（七个）的 Kubernetes 清单，核对每条集成两端的 NetworkPolicy
bash tests/kind/smoke.sh                                                       # 在 kind 集群上按组件清单部署并跑 PATH A（CI: kind.yml）
bash tests/kind/full.sh                                                        # 整平台上 kind：七个组件的清单 + Keycloak（https 发行者）、PostgreSQL、Temporal，全部命名空间默认拒绝（需要 Calico 等强制执行 NetworkPolicy 的集群），经 port-forward 跑 PATH A、B、D、E、I、L、F、G、H、M、N、C（M、N 是联网检索与页面注入，用 idp-web 的清单加测试源站），再装好"助手"应用，浏览器里可从登录、提问、跑代码到审批完整体验（SKIP_BUILD=1 用已有镜像；MODEL=live 用本机真实模型；TEARDOWN=1 结束时删除）
uv run --no-project --with PyYAML==6.0.3 python -I tools/k8s_check.py --full    # 完整覆盖层：每个命名空间 restricted + 默认拒绝，集成与 Keycloak / PostgreSQL / Temporal 的流量两端放行
```

整平台 kind 部署（`deploy/kubernetes/full`，驱动脚本 `tests/kind/full.sh`）与轻量覆盖层（`deploy/kubernetes/kind`，开发令牌，仅 CMI、HOST、Workspace，CI 用）并存；前者在组件自带清单之上只用补丁与额外的 NetworkPolicy，不改组件仓库。

沙箱的出口代理在两种部署里都接上了（idp-sandbox ADR-0001 D13、D21、D22）：`sandbox_run` 带 `network=egress` 的运行只能经代理访问到调用者所在租户名单上的主机，不带的运行没有网络。compose 版的名单是 `.local/platform/egress-tenants.yaml`，`e2e.sh` 每次运行重写它（本次的租户只能访问验收用的测试源站，其他人为空），改后 `docker restart idp-telos-platform-sandbox-egress-1`；代理接受私有地址的开关 `IDP_EGRESS_ALLOW_PRIVATE` 在 `platform.yaml` 里默认关闭，`e2e.sh` 为测试源站把它打开，所以 `KEEP=1` 留下的平台里它是开着的。kind 版见手册的"沙箱的出口代理"。

web 工具（`web__web_search`、`web__web_fetch`、`web__web_open_url`）只授给经 HOST 调用的角色（compose 验收里是 `host-user`），不授给自己直接调用网关的成员角色：`web_open_url` 的确认、敏感级别上限和"页面内容不可信"的标记都在 HOST 里，绕过 HOST 就没有这三道控制（idp-mcp 互通差距文档第 7 节；`deploy/compose/mcp_setup.py`）。compose 上的 idp-web 用 `fixture` 搜索适配器和两个会记录请求的测试容器（profile `web-test`，`IDP_WEB_TEST_ORIGINS` 只由验收设置）；kind 上还没有（任务 P2）。

手册：本机 Kubernetes 测试环境（kind、整平台 PATH A–D、gVisor / Kata）见 [`deploy/kubernetes/RUNBOOK.md`](deploy/kubernetes/RUNBOOK.md)；沙箱的运维见 `idp-sandbox/docs/RUNBOOK.md`。

CI：`ci.yml`（目录、清单、契约、Kubernetes 检查）、`kind.yml`（kind 上的 PATH A）与 `platform.yml`（整平台 PATH A 到 N 验收）。私有的 idp-eer 用只读部署密钥检出（secret `IDP_EER_DEPLOY_KEY`）。`platform.yml` 在 main 上把通过验收的镜像发布为 `ghcr.io/idp-xyz/<镜像>:<组件提交>`（`idp-cmi`、`idp-host`、`idp-workspace`、`eer-runtime`、`idp-mcp`、`idp-sandbox`、`idp-web`），并用 cosign 无密钥签名、附 SPDX SBOM 证明（`tools/publish_images.sh`）。校验签名：

```sh
cosign verify ghcr.io/idp-xyz/idp-cmi:<commit> \
  --certificate-identity-regexp '^https://github.com/idp-xyz/idp-telos-go/.github/workflows/platform.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## 许可证

本仓库采用 [PolyForm Noncommercial License 1.0.0](LICENSE)：允许非商业用途，禁止任何商业使用。
