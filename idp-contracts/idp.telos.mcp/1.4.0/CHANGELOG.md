# idp.telos.mcp 1.4.0

发布：2026-11-23。生产者 idp-mcp；权威副本 `idp-mcp/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.3.0：**只有向后兼容的新增，而且文档的 JSON Schema 没有变**：Release、Envelope、InvocationRecord 的形状与 1.3.0 完全相同，只有版本号（`$id`、标题、示例说明）升到 1.4.0。1.3.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 注册服务的 API 1.4.0（不在本契约内） | 登记 `class: external` 的服务器时可带 `maxDescriptionBytes`（描述长度上限，字节；不带用部署缺省 `IDP_EXTERNAL_MAX_DESCRIPTION`，1024 不变；不得超过部署的上限 `IDP_EXTERNAL_MAX_DESCRIPTION_CAP`，缺省 8192）；`PATCH /api/v1/servers/{id}` 修改它（有变化时服务器退回 `DRAFT`，要重新发现、重新批准）；服务器多 `maxDescriptionBytes`；评审视图 `GET /api/v1/servers/{id}/catalog` 与快照多 `descriptionLimit`（批准即钉住）（idp-mcp ADR-0001 15.3，D53 的修订；迁移 `0010_server_description_limit.sql`） |

升级：先升级注册服务（迁移 0010），再升级网关；没有设置 `maxDescriptionBytes` 的服务器行为与此前完全一致。起因：真实的 GitHub 托管 MCP 服务有一个 1546 字节的参数描述，被缺省的 1024 字节拒绝；按服务器放宽，不放宽整个部署。
