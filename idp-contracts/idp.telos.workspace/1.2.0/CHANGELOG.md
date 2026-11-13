# idp.telos.workspace 1.2.0

发布：2026-11-13。生产者 idp-workspace；权威副本 `idp-workspace/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.1.0 及更早版本：**只有向后兼容的新增**（idp-workspace ADR-0001 D23，平台 ADR-0002 阶段 1）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新的根对象 `MemberFile` | 成员的一个文件：`id`、所在工作空间、名称、媒体类型、大小、SHA-256、来源（`upload` 或 `run:<runId>`）、敏感级别、创建与过期时间；不含内容。只给所有者 |
| 动作输入 Schema 的 `format: idp-file` | 声明接收成员文件的字段（字符串或其数组，值为 `MemberFile.id`）；Workspace 在调用时校验它们是调用者在该工作空间的文件，`host.run` 把它们连同票据交给 idp-host（idp.telos.host 1.8.0 的 RunOptions） |
