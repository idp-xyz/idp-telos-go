# idp.telos.host 1.0.0

首次发布（2026-11-03）。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同（`tools/validate.py` 的不变量 `idp_telos_host_published_matches_producer`）。

| 对象 | 类别 | 内容 |
|---|---|---|
| AgentRelease | CONTRACT | 一个 agent 的一次发布（`schemaVersion: idp.host.agent/v1`）：编号、创建与审批信息、固定下来的定义；审批人参与摘要 |
| Envelope | CONTRACT | AgentRelease 加规范化 JSON 的 SHA-256 摘要 |
| AgentRun | CONTRACT | 一次运行的记录（HOST 的权威状态）：线程、agent 与 release 摘要、所有者、状态、步数、失败原因、给 EER 预留的 `executionRef`、追踪 id 与时间；其他系统用 `runId` 引用，不复制状态 |
| Definition、ModelProfile、ModelRef、ToolPolicy、ConfirmationPolicy、Limits | HELPER | AgentRelease 的组成部分，全部封闭（不允许额外字段） |

实现侧一致性测试（idp-host，`go test ./internal/run -run TestContract`）：
- 示例由 Go 值生成并在每次测试时比对（`-update` 重新生成），每个 AgentRelease / Envelope 示例都能被 `agent.Open` 接受，每个 AgentRun 示例都能无损往返 `run.Run`；
- 每个定义的属性集合等于 Go 结构体的 JSON 字段，`required` 等于无 `omitempty` / `omitzero` 的字段；
- 实现拒绝的 13 种 AgentRelease 变体与 8 种 AgentRun 变体，schema 同样拒绝。

JSON Schema 只描述形状与可本地判定的规则；摘要是否匹配由 `agent.Open` 判定，运行时不变量（状态迁移、租约、确认）由 HOST 的状态机判定。
