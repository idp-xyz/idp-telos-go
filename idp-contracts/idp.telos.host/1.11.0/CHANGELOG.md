# idp.telos.host 1.11.0

发布：2026-11-19。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.10.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| `AgentRun` 新增可选的 `sources` | 运行的工具返回过的网页来源（idp-host ADR-0001 D49，平台 ADR-0003 D9 与附录 D 的 4.1），按 `n` 升序，至多 100 项，没有来源时没有这个字段。本地子 agent 运行找到的来源也记入父运行 |
| 新定义 `RunSource` | `{n, title, url, fetchedAt?, sha256?, cited}`：`n` 是 idp-web 的引用编号（按调用者递增，同一次运行里唯一但不连续）；`title` 至多 300 个字符，来自网页，是不可信的文字，只能当纯文本显示；`url` 是 http 或 https 地址；`sha256` 是第一次读取时交给模型的那一段正文的摘要，不是整页的；`cited` 表示最终回答是否写了这个编号。只保存标题与地址，不保存摘录与页面正文 |
| `AgentRun` 新增可选的 `unverifiedCitations` | 运行完成时，最终回答里写了、但不在 `sources` 里的引用编号（1 到 999，唯一，升序）。只是标记，回答不被拦截或修改；只发现编号不存在，发现不了来源存在却不支持那句话 |

事件 `idp.host/run.sources`（运行结束时一次，在 `RUN_FINISHED` 之前，带完整列表与丢弃、冲突的计数）不属于 schema。

`AgentRun` 对生产者是封闭的（`additionalProperties: false`）：按 1.10.0 的 schema 严格校验 HOST 返回的运行的消费者会拒绝带 `sources` 的运行。idp-workspace 不这样做（它读运行只取 `deadlineAt`，跟随事件流时忽略不认识的事件），所以升级顺序是先 HOST，后 Workspace。

实现侧一致性测试：`contractID` 改为 1.11.0，示例重新生成（新增一个带来源与无法核对编号的已完成运行），新增 `TestContractRejectsRunSourcesTheHostWouldNotRecord`。
