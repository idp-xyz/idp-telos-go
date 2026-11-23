# idp.telos.workspace 1.4.0

发布：2026-11-22。生产者 idp-workspace；权威副本 `idp-workspace/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.3.0 及更早版本：**只有向后兼容的新增**（idp-workspace ADR-0001 D30，外联桥计划 W1，配合 idp-mcp-bridge 与 idp-host D61）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新的根对象 `ConnectionList` | `GET /v1/me/connections` 的答案：成员可用的第三方连接，每项一个 `Connection`（至多 200 项） |
| 新的辅助对象 `Connection`、`ConnectionAccount` | 成员在一个第三方服务上的连接状态：连接编号、标题、状态（`not_required`、`not_connected`、`connected`、`reauthorization_required`）、需要重新授权的原因（只在需要时）、成员授予的范围；有桥的评审角色的运维另有评审账号的状态（`ConnectionAccount`）。没有令牌、没有授权服务器的地址 |
| 新的根对象 `ConnectionAuthorization` | `POST /v1/me/connections/{id}/authorization` 的答案：第三方授权页的地址（桥构造，Workspace 核对过：https，没有用户信息与片段，query 里恰有一个 `state`），壳把浏览器送去那里 |
| 新的根对象 `ConnectionResult` | `POST /v1/me/connections/callback` 的答案：连接已连上，以及开始时记下的、Workspace 自己的相对返回路径；`reviewer` 为 true 表示授权的是评审账号 |

升级顺序：先桥（idp-mcp-bridge），后 Workspace（`targets.connections` 设了才有这些路由；没设时没有页面、没有路由）。没有已有对象被改动；`idp.telos.host`、`idp.telos.mcp`、`idp.telos.cmi` 不变。
