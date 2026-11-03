# idp-telos-go

IDP TELOS 的 Go 实现。

| 目录 | 内容 | 版本控制 |
|---|---|---|
| `idp-mcp/` | MCP 基础设施：`mcp-gateway`（数据面）与 `registry-service`（控制面）。M1–M5 完成并实测；设计见其 `docs/adr/ADR-0001-mcp-infrastructure.md` | 独立仓库（本仓库忽略它） |
| `idp-host/` | Agent Hosting & Coordination：Agent 与 Skill 发布、Run 状态机、harness、模型适配器（OpenAI 兼容 / Anthropic / Gemini）、经 idp-mcp 的工具调用（含 Keycloak 委派）、租户配额、AG-UI。M1、M2 完成并实测；设计见其 `docs/adr/ADR-0001-host-design.md` | 独立仓库（本仓库忽略它） |
| `idp-contracts/` | `idp.telos.*` 契约登记：`idp.telos.mcp` 1.0.0、`idp.telos.host` 1.1.0（各由其生产者产出并由测试钉住）；四条规则见其 README | 本仓库 |
| `docs/` | 架构蓝图、系统架构 v1.0、仓库工程架构 v1.0 | 本仓库 |
| `brand/` | 品牌资源 | 本仓库 |

命名：契约命名空间是 `idp.telos.<模块>`，`$id` 为 `urn:idp:telos:<模块>:<版本>`，所有者键为 `x-idp-telos-owner`。

契约校验：`uv run --no-project --with 'jsonschema[format]==4.26.0' python -I idp-contracts/tools/validate.py`（退出码 0 表示全部通过且发布副本与 idp-mcp 的权威副本一致）。
