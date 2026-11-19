# idp.telos.mcp 1.2.0

发布：2026-11-19。生产者 idp-mcp；权威副本 `idp-mcp/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.1.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。1.1.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| InvocationRecord 新增可选 `reason` | 拒绝的原因（`client not listed`、`client not allowed`、`host-only`、`role`、限流要等多久），或运维的"只许 HOST"例外所写的理由；只含网关自己的措辞和运维写的理由，从不含客户端发来的内容 |
| InvocationRecord 新增可选 `bytes` | 事件流已转发的字节数；只写在结束一条流的记录里 |
| `outcome` 新增三个取值 | `STREAM_STARTED`（流式应答的第一条记录）、`RATE_LIMITED`、`UNAUTHENTICATED` |
| `method` 新增六个取值 | `tools/list`、`prompts/list`、`resources/list`、`resources/templates/list`、`subscriptions/listen`、`server/discover`（后者只在被拒绝时记）。没有单个对象的记录，`tool` 是 `*`；未认证的请求，`subject` 是 `(none)` |

对应 idp-mcp ADR-0001 第 14 节（D46 至 D49）；迁移 `0008_invocation_reason_bytes.sql`。注册服务按 `DisallowUnknownFields` 解码网关推来的记录，所以先升级注册服务，再升级网关。
