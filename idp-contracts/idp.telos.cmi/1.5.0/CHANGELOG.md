# idp.telos.cmi 1.5.0

发布：2026-11-25。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.4.0 及更早版本：**只有向后兼容的新增**（知识库第一步，idp-cmi ADR-0001 第 24 节 D47–D58，`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新定义 KnowledgeBase | 知识库：名字、显示名、作用域（企业、部门、个人）、管理者、缺省敏感级别与上限、来源、文档数与字节配额、是否识别扫描页、状态 |
| KnowledgeDocument 新增可选字段 | `knowledgeBase`（一个版本的身份是租户、知识库、uri）、`validFrom`（晚于现在的版本不取代当前版本）、`docType`、`owner`、`reviewDue`、`original`（媒体类型、字节数、sha256；对象键不在契约里） |
| ContextItem 的来源新增 `locator` | 知识块在文档里的位置：标题路径、页码、表名 |

行为变化（契约之外，见 idp-cmi 的 README "Upgrade"）：撤销一个后继版本会让前一版本重新生效；迁移 0013–0016 对每个知识块重写并取排他锁。
