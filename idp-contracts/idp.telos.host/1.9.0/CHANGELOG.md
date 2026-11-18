# idp.telos.host 1.9.0

发布：2026-11-17。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.8.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| FileAttachment 新增可选的 `sensitivity` | 文件的所有者系统给它定的敏感级别：`public`、`internal`、`confidential`、`restricted`，省略即 `internal`（idp-host ADR-0001 D43，平台 ADR-0002 F8）。发布件的模型、回退、路由候选里有运维没有声明为企业自有的 provider，或引用了远端 A2A 子 agent 时，级别高于租户上限的附件不交给这次运行：票据丢弃，模型只被告知文件名与级别，事件流记 `idp.host/attachment.withheld {ref, name, sensitivity, ceiling, externalModels}` |

不带 `sensitivity` 的 1.8.0 请求含义不变。只认识 1.8.0 的 HOST 会以 422 拒绝带 `sensitivity` 的附件（FileAttachment 不允许多余字段）；idp-workspace 因此只在级别不是 `internal` 时才发送它，旧 HOST 仍能收下 internal 的文件，而对级别更高的文件是拒绝请求，不是忽略级别。

实现侧一致性测试新增：带 `sensitivity` 的示例选项；3 种新拒绝用例（未知级别、空字符串、数字）。
