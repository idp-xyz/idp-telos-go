# idp.telos.host 1.8.0

发布：2026-11-13。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.7.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| RunOptions 新增 `idp.host/attachments` | 成员附在这次请求上的文件（idp-host ADR-0001 D37，平台 ADR-0002 F2、F3）：每项 `{ref, name, mediaType, size, sha256, ticket}`，至多 10 项。模型只知道名称、类型与大小；HOST 在 `format: idp-attachment` 的工具参数里把 `attachment:N` 换成票据。票据加密保存，不进运行的 input、事件、模型与工具调用记录 |
| RunOptions 新增 `idp.host/delivery` | `{ticket}`：运行可以把文件交给所有者；`format: idp-delivery` 的工具参数里的 `output` 换成写票据，交付的文件记为事件 `idp.host/file.delivered` |
| 新定义 `FileAttachment`、`FileDelivery` | 上面两项的形状（附件在选项里额外要求 `ticket`） |

实现侧一致性测试新增：带附件与交付的示例选项；8 种新拒绝用例（空数组、缺票据、名称含路径或换行、多余字段、11 个附件、交付缺票据或票据畸形）。
