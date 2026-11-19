# idp.telos.mcp 1.3.0

发布：2026-11-19。生产者 idp-mcp；权威副本 `idp-mcp/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.2.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。1.2.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| Release 的目标新增可选 `class` | 只有一个取值 `external`：第三方的服务器（ADR-0001 D50）。省略 = internal（自己的服务器），所以已有 Release 的摘要不变。进入 Release，由审批人批准；网关据此对该目标强制：不转发身份、`_meta` 白名单、不转发上游发起的请求、限制结果大小、按钉住的 schema 校验参数、目录无法核对时拦截调用、在 `tools/list` 里给每个工具加 `_meta["io.idp.telos/external"]: true`（D51），且其工具、prompts、资源只许 HOST 调用（D52）。external 的目标不能有 `identity`，也不能带 `tasks`、`resources.subscribe`、`resources.listChanged` 能力（由实现的校验判定，JSON Schema 不描述） |
| InvocationRecord 的 `outcome` 新增两个取值 | `RESULT_TOO_LARGE`（external 目标的结果超过上限，调用以工具错误结束）、`REVERSE_REQUEST_REFUSED`（external 目标向客户端发了请求——采样、征询、`input_required`——没有转发） |

对应 idp-mcp ADR-0001 第 15 节（D50 至 D53）；迁移 `0009_server_class.sql`。注册服务的 API（不在本契约内）随之有 `class`、`GET /api/v1/servers/{id}/catalog` 和批准 external 服务器时必带的目录摘要。先升级注册服务，再升级网关，最后登记第一台 external 服务器：旧网关遇到带 `class` 的 Release 会因未知字段拒绝激活。
