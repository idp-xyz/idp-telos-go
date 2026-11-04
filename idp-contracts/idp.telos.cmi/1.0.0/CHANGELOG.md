# idp.telos.cmi 1.0.0

发布：2026-11-04。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

首个版本（idp-cmi ADR-0001 D9）：

| 对象 | 类别 | 内容 |
|---|---|---|
| ContextPackage | CONTRACT | 一次 resolve 的不可变、内容寻址的结果：请求者、用途、查询、作用域、按信任级别 / 新鲜度 / 相关度排序并按预算装入的条目、预算用量、组装与过期时间（`schemaVersion` = idp.cmi.package/v1） |
| ContextItem | HELPER | 包里的一项：kind（memory / knowledge）、来源与信任级别、引用、摘录、观察与失效时间、敏感级别、得分、token |
| MemoryRecord | CONTRACT | 记忆记录：作用域、种类、内容、来历、有效期、状态、撤销、敏感级别、ACL |
| Source | CONTRACT | 登记的来源与其信任级别（agent / user 永远 informational） |
| KnowledgeDocument | CONTRACT | 知识文档的元数据（文本只进索引） |
| Envelope | CONTRACT | ContextPackage 加摘要 |
| Scope、Budget、Provenance、ACL、Validity、Revocation | HELPER | 组成部分 |

实现侧一致性测试：`go test ./internal/cmi -run TestContract`（示例由 Go 值生成并往返；每个定义的属性与 Go 结构一一对应；9 种包的拒绝用例与 8 种记忆的拒绝用例 schema 与实现同样拒绝；只有实现能判定的交叉规则：tenant 作用域不带 id、agent 来源不得 authoritative）。

消费方：idp-host（计划中：作为 cmi-reader 解析上下文，把条目作为 developer 消息交给模型并记录 packageId）。
