# idp.telos.host 1.6.0

发布：2026-11-04。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.5.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| Definition 新增 `context`（ContextPolicy） | 运行创建时从 CMI（`idp.telos.cmi`）取上下文（ADR-0001 D32）：`cmi`、`purpose`、`maxTokens`（≤ 32000）、`maxItems`（≤ 100）、`kinds`（memory / knowledge）、`sources`（来源名）、`onError`（skip / fail）。全部可选；实现要求其余设置只在 `cmi = true` 时出现 |
| AgentRun 新增 `contextPackageId` | 运行的 context 来自哪个 CMI ContextPackage；未要求或被跳过时没有。事件 `idp.host/context.resolved` / `idp.host/context.skipped` 记录解析结果 |

实现侧一致性测试新增：示例发布件带 context 策略、示例运行带 contextPackageId；5 种新的拒绝用例（onError 值、kinds 值、预算上限、来源名、未知字段）。
