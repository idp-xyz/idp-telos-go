# idp.telos.mcp 1.0.0

首次发布（2026-11-03）。生产者 idp-mcp；权威副本 `idp-mcp/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同（`tools/validate.py` 的不变量 `idp_telos_mcp_published_matches_producer`）。

| 对象 | 类别 | 内容 |
|---|---|---|
| Release | CONTRACT | 网关唯一运行的不可变配置（`schemaVersion: idp.mcp.release/v1`）：钉住的上游目录、授权、数据面认证配置 |
| Envelope | CONTRACT | Release 加规范化 JSON 的 SHA-256 摘要，注册服务与网关之间、导出时的形态 |
| InvocationRecord | CONTRACT | 网关对每次调用的判定记录：主体（含 `azp` / `act`）、Release 摘要、方法与名字、判定、结果、时延、参数摘要；从不含参数 |
| Target、Tool、Prompt、Resource、ResourceTemplate、Grant、Auth、APIKey、JWT | HELPER | Release 的组成部分；Tool / Prompt / Resource / ResourceTemplate 允许额外字段（网关不解释的 MCP 字段原样钉住） |

实现侧一致性测试（idp-mcp，`go test ./internal/release -run TestContract`）：
- 示例由 Go 值生成并在每次测试时比对（`-update` 重新生成），每个 Release 示例都能被 `release.Open` 接受，每个 InvocationRecord 示例都能无损往返 `invocation.Record`；
- 单元测试夹具密封出的文档（含 nil 集合）都满足 schema，且不含 `null`；
- 封闭对象的属性集合等于 Go 结构体的 JSON 字段，`required` 等于无 `omitempty` 的字段，`additionalProperties: false`；
- 实现拒绝的 24 种 Release 变体与 11 种记录变体，schema 同样拒绝。

JSON Schema 只描述形状与可本地判定的规则；摘要是否匹配、名字是否跨目标唯一、授权是否引用存在的工具等由 `release.Document.Validate` 判定。
