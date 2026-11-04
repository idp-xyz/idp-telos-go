# idp.telos.cmi 1.2.0

发布：2026-11-04。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.1.0 及更早版本：**只有向后兼容的新增**（M5，`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| MemoryRecord 新增 `version`、`disputedBy`、`sharedFrom`；status 增 `candidate`、`disputed` | 预期版本（D19）、候选与存疑及其转换（D21）、显式共享（D22） |
| KnowledgeDocument 新增 `revision` | 该文档行的状态版本（D19） |
| Scope kind 增 `organization`、`team`、`project` | 租户目录里的单元（D22） |
| ContextItem 新增 `scope` | 记忆条目的作用域（D22） |
| 新定义 Verification、ItemStatus（Verification 为根文档） | 消费时复验（D24）：包引用各项此刻的状态、版本与可用性 |

实现侧一致性测试新增：示例含 disputed 与 shared 记录、带 scope 的条目、一份 Verification；`scope bad kind` 用例改为 squad。
