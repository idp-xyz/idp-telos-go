# idp.telos.workspace 1.0.0

发布：2026-11-04。生产者 idp-workspace；权威副本 `idp-workspace/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

首个版本（idp-workspace ADR-0001 D8，M1）。

| 对象 | 类别 | 内容 |
|---|---|---|
| Workspace | CONTRACT | 工作空间（D1） |
| AppManifest | CONTRACT | 应用清单 `idp.workspace.app/v1`：H5 入口、桥能力、动作契约、配置 Schema（D2）；含 AppEntry、ConfigSpec、ActionContract、Target |
| AppRelease | CONTRACT | 不可变的发布：摘要（规范化 JSON 的 sha256）、状态、撤销记录（Revocation） |
| Installation | CONTRACT | 工作空间里的安装与配置、revision、所装版本的发布状态 |
| InboxItem | CONTRACT | 收件箱事项（D3）：权威对象引用 Ref、指派 Assignees、可执行动作 ItemAction、实体 Entity、生产者的结果 Resolution、调用者自己的标记 |
| WorkContext | CONTRACT | 成员的工作上下文（D5） |
| Invocation | CONTRACT | 动作调用记录（D4）：输入摘要、目标、结果引用或错误（failed 必带 error，succeeded 不带） |
| BridgeMessage | CONTRACT | 应用桥 `idp.bridge/v1` 的 request / response / event（D5），方法与事件为封闭枚举 |
| Problem | CONTRACT | 错误码与消息 |

实现侧一致性测试：`go test ./internal/workspace -run TestContract`（示例由 Go 值生成；每个定义闭合且与 Go 结构逐字段一致；清单、事项、桥消息、调用记录的拒绝用例；清单示例经 `Manifest.Validate`）；应用桥 SDK 的测试另外核对方法与事件枚举。
