# idp.telos.host 1.5.0

发布：2026-11-04。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.4.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| ModelProfile 新增 `routing`（Routing、Candidate） | 模型路由（ADR-0001 D29）：strategy 为 ordered / cost / latency / capability；candidates 带单价、能力（tools、json、reasoning、vision、longContext）与上下文窗口。决定记为 `idp.host/model.routed` 事件 |
| SubagentRef / EmbeddedSubagent 新增 `a2a`（A2ARef） | 远端 A2A agent（D30）：`cardUrl` 指向其 agent card；远端引用没有钉住的版本、agentId 或摘要。EmbeddedSubagent 的 agentId / version / digest 因此不再必填（本地引用仍由实现要求齐全） |

实现侧一致性测试新增：示例发布件带 cost 路由与一个远端 agent；6 种新的拒绝用例（策略值、空候选、能力名、负单价、card URL）与 4 条交叉规则（候选去重、远端引用不得钉版本、远端嵌入不得带摘要、本地嵌入不得带 a2a）。
