# idp.telos.host 1.2.0

发布：2026-11-03。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.1.0 和 1.0.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量 `idp_telos_host_1_2_0_is_additive_over_1_1_0`、`idp_telos_host_1_2_0_is_additive_over_1_0_0` 检查：定义都在、没有新增必填、没有删属性、根文档都在）。1.1.0 与 1.0.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新对象 RunOptions（CONTRACT） | 创建运行时的调用方选项（ADR-0001 D19）：`RunAgentInput.forwardedProps` 中 `idp.host/` 开头的键。`idp.host/toolExecution`（`server` / `caller`：调用方执行工具，模型只看到请求里的前端工具，HOST 不接触网关、只调一次模型、把调用提议交回）、`idp.host/releaseDigest`（运行这一份已批准或已被取代的发布件，否则 409）、`idp.host/executionRef`（记录为 `AgentRun.executionRef`）、`idp.host/maxOutputTokens`（只能压低发布件的模型输出上限）。未知的 `idp.host/` 键与不合规的值 → 422；其他命名空间的键属于调用方，不在本契约内 |
| AgentRun 的 `executionRef` | 结构不变；说明从"预留"改为"由 RunOptions 的 `idp.host/executionRef` 设置" |

实现侧一致性测试（`go test ./internal/run -run TestContract`）新增：RunOptions 示例经 `run.ParseOptions` 解析后原样往返；`run.Options` 的 JSON 字段与 schema 一一对应；12 种 API 拒绝（422）的选项值 schema 同样拒绝。

消费方：EER 的 IDP HOST 适配器（EER ADR-036）每个 Agent 回合发送一个 RunOptions（`caller` + 钉住的发布件摘要 + execution id + 剩余输出预算）。
