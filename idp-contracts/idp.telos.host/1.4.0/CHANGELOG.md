# idp.telos.host 1.4.0

发布：2026-11-04。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.3.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查：定义都在、没有新增必填、没有删属性、根文档都在）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| Definition 新增 `guards`（Guards、InputGuard、OutputGuard） | 内容护栏（ADR-0001 D27、D28）。`input.toolResults` / `input.context`：off、wrap（缺省：以 `<untrusted>` 定界并检测注入模式，命中发 `idp.host/guard.flagged`）、block（另把命中内容替换为说明）。`output.patterns`（与租户策略的 blockedPatterns 并用，命中则运行以 `GUARD_BLOCKED` 失败并发 `idp.host/guard.blocked`）、`output.redact`（email、phone、card）、`output.classifier` 与 `onError`、`output.strict` |
| Definition 新增 `evaluation`（Evaluation、EvalCase、Expectation） | 固定评估集（D26）：`required` 为 true 的发布件在最近一次评估 PASSED 前不能被批准（409 EVALUATION_REQUIRED）。期望：contains、notContains、matches、toolCalled、noToolCall、maxSteps |
| AgentRun 新增 `purpose` | HOST 为评估集创建的运行标为 `evaluation`，不计入租户并发 |

实现侧一致性测试（`go test ./internal/run -run TestContract`）新增：示例发布件带护栏与评估集；评估运行示例；10 种新的拒绝用例与只有实现能判定的交叉规则（禁用模式必须是合法 RE2、onError 需要 classifier、用例名唯一、期望非空、toolCalled 与 noToolCall 不得同时出现、required 需要用例）。
