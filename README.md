# idp-telos-go

IDP TELOS 的 Go 实现。本仓库是工作区：承载 `idp.telos.*` 契约登记、架构文档和品牌资源。三个运行时组件各自是独立仓库，按约定并排克隆在本目录下，并被 `.gitignore` 忽略。

| 目录 | 内容 | 仓库 |
|---|---|---|
| `idp-mcp/` | MCP 基础设施：`mcp-gateway`（数据面）与 `registry-service`（控制面）。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-mcp-infrastructure.md` | [idp-xyz/idp-mcp](https://github.com/idp-xyz/idp-mcp) |
| `idp-host/` | Agent Hosting & Coordination：Agent 与 Skill 发布、Run 状态机、harness、模型适配器（OpenAI 兼容 / Anthropic / Gemini / 外部 harness worker）与路由、经 idp-mcp 的工具调用（含 Keycloak 委派）、子 agent、租户配额、内容护栏、评估门禁、AG-UI 与 A2A。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-host-design.md` | [idp-xyz/idp-host](https://github.com/idp-xyz/idp-host) |
| `idp-eer/` | Enterprise Execution Runtime：持久化 Agent Loop，Agent Turn 由 idp-host 提供（ADR-036），依赖契约 `idp.telos.host` 1.2.0（1.5.0 对其只有新增） | [idp-xyz/idp-eer](https://github.com/idp-xyz/idp-eer) |
| `idp-contracts/` | `idp.telos.*` 契约登记：`idp.telos.mcp` 1.0.0、`idp.telos.host` 1.5.0（各由其生产者产出并由测试钉住）；四条规则见其 README | 本仓库 |
| `docs/` | 架构蓝图、系统架构 v1.0、仓库工程架构 v1.0 | 本仓库 |
| `brand/` | 品牌资源 | 本仓库 |

## 工作区布局

```sh
git clone https://github.com/idp-xyz/idp-telos-go.git
cd idp-telos-go
git clone https://github.com/idp-xyz/idp-mcp.git
git clone https://github.com/idp-xyz/idp-host.git
git clone https://github.com/idp-xyz/idp-eer.git
```

契约校验器默认在 `idp-contracts/` 的上级目录（即本仓库根目录）里寻找并排克隆的生产者仓库；若它们放在别处，用 `--producer-root` 指定。

命名：契约命名空间是 `idp.telos.<模块>`，`$id` 为 `urn:idp:telos:<模块>:<版本>`，所有者键为 `x-idp-telos-owner`。

契约校验：`uv run --no-project --with 'jsonschema[format]==4.26.0' python -I idp-contracts/tools/validate.py`（退出码 0 表示全部通过且发布副本与 idp-mcp、idp-host 的权威副本一致）。

## 许可证

本仓库采用 [PolyForm Noncommercial License 1.0.0](LICENSE)：允许非商业用途，禁止任何商业使用。
