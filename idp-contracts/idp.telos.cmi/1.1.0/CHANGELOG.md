# idp.telos.cmi 1.1.0

发布：2026-11-04。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.0.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新定义 Entity | 显式的业务对象引用（ADR-0001 D13）：type（名字）、id |
| MemoryRecord / KnowledgeDocument 新增 `entities`、`flags` | entities 是写入者声明的引用；flags 是写入时注入扫描的标记（D12，`injection:<规则>`），由 CMI 设置，被标记的内容默认不进上下文包 |
| ContextItem 新增 `entities`、`match`、`flags` | match 是命中方式（lexical / semantic / both，D11 混合检索）；flags 只在租户策略 includeFlagged 为真时出现 |

实现侧一致性测试新增：示例包含带实体与三种 match 的条目、一条被标记的记录、带实体的文档；5 种新的拒绝用例（match 值、实体类型、实体 id 为空、flags 非扫描结果）。
