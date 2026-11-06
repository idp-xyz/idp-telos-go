# idp.telos.mcp 1.1.0

发布：2026-11-06。生产者 idp-mcp；权威副本 `idp-mcp/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.0.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。1.0.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| Target 新增可选 `identity` | 取值只有 `forward`；省略表示不向该目标转发调用者（此时规范化 JSON 里没有这个字段，已有 Release 的摘要不变）。`forward` 的目标，网关把经过认证的调用者放进发给它的每个请求的 `_meta["io.idp.telos/caller"]`（`subject`、`tenant`、`roles`，以及 `client`、`actor`），并总是先删除客户端自带的同名键（idp-mcp ADR-0001 §12，D40–D43）。它是 Release 的一部分，所以由审批人批准 |

`_meta["io.idp.telos/caller"]` 的内容不是本契约的文档（它不出现在 Release、Envelope 或 InvocationRecord 里），由 idp-mcp ADR-0001 D40 定义。
