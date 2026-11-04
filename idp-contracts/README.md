# IDP TELOS 契约登记

各模块对外发布的机器契约都登记在这里：**按模块划分命名空间 `idp.telos.<模块>`、各自独立版本**；所有权跟随命名空间，不跟随仓库。

| 命名空间 | 版本 | `$id` | 内容 | 生产者 | 状态 |
|---|---|---|---|---|---|
| `idp.telos.mcp` | 1.0.0 | `urn:idp:telos:mcp:1.0.0` | Release、Envelope、InvocationRecord（MCP 基础设施 idp-mcp） | `idp-mcp/api/contracts`，`go test ./internal/release -run TestContract` | 已发布（2026-11-03） |
| `idp.telos.host` | 1.5.0 | `urn:idp:telos:host:1.5.0` | AgentRelease（可嵌入 Skill、引用本地或远端 A2A 子 agent，带护栏、评估集与模型路由）、SkillRelease（可带资源）、Envelope、AgentRun（含子 agent 运行与评估运行）、RunOptions（创建运行时的调用方选项）（Agent Hosting & Coordination idp-host） | `idp-host/api/contracts`，`go test ./internal/run -run TestContract` | 已发布（2026-11-04）；1.4.0 及更早版本 SUPERSEDED，文件保留，`tools/validate.py` 检查 1.5.0 对它们都只做了向后兼容的新增 |

| `idp.telos.cmi` | 1.0.0 | `urn:idp:telos:cmi:1.0.0` | ContextPackage、ContextItem、MemoryRecord、Source、KnowledgeDocument、Envelope（Context, Memory & Knowledge Infrastructure idp-cmi） | `idp-cmi/api/contracts`，`go test ./internal/cmi -run TestContract` | 已发布（2026-11-04） |

其他模块在其生产者发布契约并带上一致性测试时登记。

## 四条规则

1. **契约由生产者拥有并生成。** 权威副本在生产者仓库里（`idp.telos.mcp`：`idp-mcp/api/contracts/`；`idp.telos.host`：`idp-host/api/contracts/`；`idp.telos.cmi`：`idp-cmi/api/contracts/`）；这里只放发布副本，必须与权威副本逐字节相同。改契约先改生产者，再把副本搬过来。
2. **登记的前提是实现侧的一致性测试。** 生产者仓库必须有测试证明：实现产出的文档满足 schema、发布的示例是实现真实产出、实现拒绝的 schema 也拒绝。没有这样的测试，就不登记。
3. **兼容性由工具检查。** 次版本只做向后兼容的新增（可选字段、新对象）；主版本可以不兼容，但要在 CHANGELOG 中写明有无迁移路径。
4. **消费者只用这里发布的版本。** 不从生产者的工作副本取契约；跨命名空间用 URN 引用（`urn:idp:telos:<模块>:<版本>#/$defs/<对象>`）。

## 登记方式

`registry.json` 登记命名空间（`$id`、版本、状态、schema 与示例文件、生产者仓库 / 路径 / 一致性测试命令）和对象（名称、所有者、版本、类别、状态、文件）。对象类别：CONTRACT 是独立文档（出现在 schema 根部的 `oneOf` 里），HELPER 是组成部分。命名空间状态：`PUBLISHED`、`DRAFT`（尚未发布）。

每个命名空间一个目录 `<命名空间>/<版本>/`，内含 `schema.json`（Draft 2020-12，`$id` 为 `urn:idp:telos:<模块>:<版本>`，每个定义带 `title`、`description`、`x-idp-telos-owner`）、`examples.json`（由生产者生成，每条示例 `{"definition": <对象>, "value": <文档>}`）和 `CHANGELOG.md`。

## 校验

```bash
uv run --no-project --with 'jsonschema[format]==4.26.0' python -I idp-contracts/tools/validate.py
```

也可以在独立的 Python 环境中安装 `requirements.txt`，再运行 `python idp-contracts/tools/validate.py`。默认在 `idp-contracts` 的上一级目录找生产者仓库（`idp-mcp/`）；不在那里时传 `--producer-root`。

校验内容：
- 每个命名空间的 schema 是否符合 Draft 2020-12，引用能否解析，每个定义是否带 `title`、`description`、`x-idp-telos-owner` 且所有者等于所在命名空间；
- 生产者生成的示例全部有效；手写的正例与反例按预期通过或被拒绝，每个反例都记录了是哪条规则拒绝的（`idp.telos.mcp` 的反例镜像 idp-mcp 的 `TestContractRejectsWhatValidateRejects`）；
- 结构不变量：每个独立文档都有示例；`idp.telos.mcp` 只有钉住的 MCP 对象（Tool、Prompt、Resource、ResourceTemplate）允许额外字段；
- registry：每个定义都登记且文件、版本、类别一致，每个条目都有定义，命名空间的 `$id`、状态、生产者信息完整；
- **发布副本与生产者副本逐字节相同**（`idp_telos_mcp_published_matches_producer`）。

结果写入 `checks/validation_report.json`。退出码：0 全部通过；1 有检查失败；2 找不到生产者仓库（该检查未运行，不会降级）。

这里只做文件级校验，不运行任何生产者；运行时行为由各生产者的测试负责。
