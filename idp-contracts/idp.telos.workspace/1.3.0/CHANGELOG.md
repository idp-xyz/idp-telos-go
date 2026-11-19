# idp.telos.workspace 1.3.0

发布：2026-11-19。生产者 idp-workspace；权威副本 `idp-workspace/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.2.0 及更早版本：**只有向后兼容的新增**（idp-workspace ADR-0001 D29，平台 ADR-0003 附录 D 任务 WS1）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| `InboxItem` 新增可选的 `sources` | 已完成的运行读过或找到的网页来源（`RunSource` 数组），按 `n` 升序，至多 100 项，来自 idp-host 的 `idp.host/run.sources` 事件（idp.telos.host 1.11.0），Workspace 逐项重新校验后才存。只有 `kind` 为 `run` 的事项可能有；运行没有来源、失败、取消或跟随中断时没有这个字段（不是空数组）。看得到事项的人看得到它，别人看不到。标题与地址来自网页，是不可信的数据 |
| `InboxItem` 新增可选的 `unverifiedCitations` | 运行完成时，最终回答里写了、但不在这次运行来源里的引用编号（1 到 999，唯一，升序，至多 100 个）。只是提示，回答不被拦截或修改。没有则没有这个字段 |
| 新定义 `RunSource` | `{n, title, url, fetchedAt?, sha256?, cited}`，封闭，与 idp.telos.host 1.11.0 的 `RunSource` 同形：`n` 1 到 1 000 000；`title` 至多 300 个字符，已去掉控制字符与双向文字符，只能当纯文本显示；`url` 是 http 或 https 地址，不含用户信息；`sha256` 是第一次读取时交给模型的那一段正文的摘要，不是整页的；`cited` 表示最终回答是否写了这个编号。不保存摘录与页面正文 |

升级顺序：先 HOST（idp.telos.host 1.11.0），后 Workspace；在 HOST 发出 `idp.host/run.sources` 之前这个版本什么也不改变。
