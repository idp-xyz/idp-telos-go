# idp.telos.cmi 1.4.0

发布：2026-11-04。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.3.0 及更早版本：**只有向后兼容的新增**（M6，`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新定义 Omitted；ContextPackage 新增 `omitted` | 解析命中但未进包的候选数，按原因（D28）：budget、flagged、candidate、disputed、forbidden、lowSimilarity；全为 0 时省略。ACL、敏感上限与租户隔离排除的不计 |

M6 的 D29（`externalModelCeiling`）、D30（审批核验、`requireApproval`）只改管理接口与配置，D31 只改评估，均不进契约。

实现侧一致性测试新增：示例包带 omitted；拒绝负计数、缺字段与未知原因。
