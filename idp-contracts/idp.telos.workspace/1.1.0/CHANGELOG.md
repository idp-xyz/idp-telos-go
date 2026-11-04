# idp.telos.workspace 1.1.0

发布：2026-11-04。生产者 idp-workspace；权威副本 `idp-workspace/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.0.0：**只有向后兼容的新增**（M3，idp-workspace ADR-0001 D15）。1.0.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| AppManifest 的 `capabilities` 增加枚举值 `identity.token` | 应用可申请由 Workspace 签发给其后端的短期应用令牌 |
| BridgeMessage 的 `method` 增加枚举值 `identity.token` | 应答 `{token, expiresAt}`：ES256、受众 `idp-workspace-app:<app>`，以 Workspace 的 `/.well-known/jwks.json` 校验；不是用户令牌 |

注意：对按 1.0.0 严格校验枚举的消费者，带 `identity.token` 的清单与桥消息是新值；只读取 1.0.0 能力的壳与应用不受影响。
