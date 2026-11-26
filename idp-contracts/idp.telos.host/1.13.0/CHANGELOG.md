# idp.telos.host 1.13.0

发布：2026-11-26。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.12.0 及更早版本：**只有向后兼容的新增**（HOST 是一个线程里唯一给引用编号的一方，idp-host ADR-0001 第 49 节 D70–D85，`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| `RunSource.n` 的含义 | 1.13.0 起是 HOST 给的编号（一个线程一个计数器，线程里的运行与它们的子 agent 共用），模型读到的和回答里写的 `[n]` 就是它；以前是 idp-web 的编号。1.13.0 之前记下的来源没有 `webN`，它们的 `n` 是当时 idp-web 的编号 |
| `RunSource` 新增可选的 `webN` | idp-web 给该地址的编号，模型从不读到，只供核对 |
| `AgentRun.sources` | 仍只含网页来源（`url` 仍必需） |
| `AgentRun` 新增可选的 `knowledgeSources` | 知识来源（`n`、`title`、`documentId`、`where`、`source`、`cited`），至多 100 条；`n` 与 `sources` 的 `n` 来自同一个计数器，消费者按 `n` 合并 |
| `AgentRun` 新增可选的 `unlistedCitations` | 回答引用了、线程里给过、但两个列表里都没有的编号（`unverifiedCitations` 是没有人给过的编号） |

事件 `idp.host/run.sources` 带同样的字段。迁移 0017 需要停机（见平台的 RUNBOOK）：它建 `citation_counters`、给 `runs` 加 `credential_ref` 等列，并回填线程的计数器。
