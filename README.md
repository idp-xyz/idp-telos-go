# idp-telos-go

IDP TELOS 的 Go 实现。本仓库是平台仓库（仓库工程架构中的 idp-telos）：承载 `idp.telos.*` 契约登记、组件目录、平台发布清单与兼容性矩阵、整平台组装与跨系统验收、架构文档和品牌资源（见 [`docs/adr/ADR-0001-platform-integration.md`](docs/adr/ADR-0001-platform-integration.md)）。五个运行时组件各自是独立仓库，按约定并排克隆在本目录下，并被 `.gitignore` 忽略；本仓库不含它们的源码。

| 目录 | 内容 | 仓库 |
|---|---|---|
| `idp-mcp/` | MCP 基础设施：`mcp-gateway`（数据面）与 `registry-service`（控制面）。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-mcp-infrastructure.md` | [idp-xyz/idp-mcp](https://github.com/idp-xyz/idp-mcp) |
| `idp-host/` | Agent Hosting & Coordination：Agent 与 Skill 发布、Run 状态机、harness、模型适配器（OpenAI 兼容 / Anthropic / Gemini / 外部 harness worker）与路由、经 idp-mcp 的工具调用（含 Keycloak 委派）、子 agent、租户配额、内容护栏、评估门禁、AG-UI 与 A2A。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-host-design.md` | [idp-xyz/idp-host](https://github.com/idp-xyz/idp-host) |
| `idp-eer/` | Enterprise Execution Runtime：持久化 Agent Loop，Agent Turn 由 idp-host 提供（ADR-036），Decision 从 idp-cmi 取上下文并留下记忆（ADR-037），T2 审批等待推入 idp-workspace 收件箱（ADR-038）；钉住契约 `idp.telos.host` 1.5.0 与 `idp.telos.cmi` 1.2.0（登记版本对其只有新增，见兼容性矩阵） | [idp-xyz/idp-eer](https://github.com/idp-xyz/idp-eer) |
| `idp-cmi/` | Context, Memory & Knowledge Infrastructure：来源与信任级别、记忆记录（来历、有效期、撤销）、知识文档与全文索引、密封的上下文包、敏感级别与 ACL。M1–M6 完成并实测（混合检索、写入时注入隔离、实体引用、整合 Worker、连接器、评估套件、Kubernetes 清单、compose e2e 含与 idp-host 的联调；幂等与版本、受控擦除、候选 / 存疑、单元作用域与共享、外部 PDP、消费时复验、双时态、outbox 投影、标注评估集；未进包报告、外部模型敏感上限、审批核验、来源可追溯率）；设计见其 `docs/adr/ADR-0001-cmi-design.md` | [idp-xyz/idp-cmi](https://github.com/idp-xyz/idp-cmi) |
| `idp-workspace/` | 企业工作入口：工作空间与成员、应用注册与安装（不可变发布、可撤销）、工作收件箱（生产者推入、只作视图）、工作上下文、动作契约（转给 idp-host 运行、idp-eer 审批决定或签名的应用 webhook）与应用桥 SDK（`packages/bridge`，idp.bridge/v1）、Web 壳（`apps/web`，React，收件箱实时推送，Agent 运行结果回到收件箱）。OIDC 登录、应用令牌、下游令牌交换。M1–M3 完成并实测（含与真实 idp-host、Keycloak 的联调与 Chromium 冒烟）；设计见其 `docs/adr/ADR-0001-workspace-design.md` | [idp-xyz/idp-workspace](https://github.com/idp-xyz/idp-workspace) |
| `idp-contracts/` | `idp.telos.*` 契约登记：`idp.telos.mcp` 1.0.0、`idp.telos.host` 1.7.0、`idp.telos.cmi` 1.4.0、`idp.telos.workspace` 1.1.0（各由其生产者产出并由测试钉住）；四条规则见其 README | 本仓库 |
| `catalog/` | 组件目录、允许的跨系统调用及其证据、状态权威归属 | 本仓库 |
| `releases/` | 平台发布清单 `platform-bom.yaml`（组件提交、镜像、契约版本）与兼容性矩阵 `compatibility-matrix.yaml`，由 `tools/platform.py` 生成与校验 | 本仓库 |
| `deploy/` | 整平台组装（`deploy/compose/platform.yaml`：Keycloak（https 发行者）、idp-cmi、idp-host、idp-workspace、idp-eer 与 Temporal）与跨系统验收（`deploy/compose/e2e.sh`：PATH A 问答经 Workspace→HOST→CMI；PATH B T2 审批经 EER→Workspace 收件箱→成员批准→EER 完成） | 本仓库 |
| `docs/` | 平台 ADR、架构蓝图、系统架构 v1.0、仓库工程架构 v1.0 | 本仓库 |
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
```

契约校验器默认在 `idp-contracts/` 的上级目录（即本仓库根目录）里寻找并排克隆的生产者仓库；若它们放在别处，用 `--producer-root` 指定。

命名：契约命名空间是 `idp.telos.<模块>`，`$id` 为 `urn:idp:telos:<模块>:<版本>`，所有者键为 `x-idp-telos-owner`。

契约校验：`uv run --no-project --with 'jsonschema[format]==4.26.0' python -I idp-contracts/tools/validate.py`（退出码 0 表示全部通过且发布副本与 idp-mcp、idp-host、idp-cmi、idp-workspace 的权威副本一致）。

平台：

```sh
uv run --no-project --with PyYAML==6.0.3 python -I tools/platform.py check    # 目录、发布清单、兼容性矩阵
uv run --no-project --with PyYAML==6.0.3 python -I tools/platform.py bom      # 组件移动后重新生成发布清单（matrix 同理）
bash deploy/compose/e2e.sh                                                     # 构建四个组件镜像，跑 PATH A 与 PATH B 验收（KEEP=1 保留运行）
```

## 许可证

本仓库采用 [PolyForm Noncommercial License 1.0.0](LICENSE)：允许非商业用途，禁止任何商业使用。
