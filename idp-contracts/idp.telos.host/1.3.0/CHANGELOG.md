# idp.telos.host 1.3.0

发布：2026-11-04。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.2.0、1.1.0 和 1.0.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量 `idp_telos_host_1_3_0_is_additive_over_1_2_0` 等检查：定义都在、没有新增必填、没有删属性、根文档都在）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| Definition 新增 `subagents`（SubagentRef） | 本 agent 可委派的其他 agent（ADR-0001 D22）：名字、可选钉住的版本、给模型看的说明。模型看到的工具名为 `agent__<name>` |
| AgentRelease 新增 `subagents`（EmbeddedSubagent） | 编译时解析出的子 agent 身份：名字、agentId、版本、该 AgentRelease 的摘要。只嵌入身份，不嵌入内容 |
| Definition 新增 `skillMerge`（SkillMerge） | skill 合并规则（D24）：`instructions` append / prepend / placeholder，`tools` union / ceiling，`confirmation` strictest / agent。缺省 append / union / strictest，与 1.2.0 的行为相同 |
| Limits 新增 `maxSubagentDepth` | 委派嵌套深度（1–5，缺省 1） |
| SkillDefinition 新增 `resources`（Resource） | 随 skill 发布的参考文档（D23）：名字、媒体类型（text/plain、text/markdown、application/json）、内容（≤ 256 KiB，合计 ≤ 1 MiB）、`onDemand`。内联资源附在 skill 指令之后；按需资源由模型通过 HOST 内部工具 `resource__read` 读取 |
| ToolPolicy.allow 与 SkillDefinition.tools | 不得使用 HOST 保留前缀 `agent__`、`resource__`（schema 用 `not.pattern` 表达） |
| AgentRun 新增 `parentRunId`、`depth`；status 新增 `AWAITING_SUBAGENT` | 子 agent 运行记录其父运行与深度；委派中的父运行状态为 AWAITING_SUBAGENT（不发 RUN_FINISHED，流保持打开） |

实现侧一致性测试（`go test ./internal/run -run TestContract`）新增：示例发布件带子 agent 引用、合并规则与内联 / 按需资源；运行示例含等待子 agent 的父运行与深度为 1 的子运行；12 种新的拒绝用例（保留前缀、合并规则值、深度范围、资源媒体类型与内容、子 agent 摘要与引用、运行的 depth 与 parentRunId），以及只有实现能判定的交叉规则（子 agent 引用与嵌入一一对应、不得引用自身、ceiling 下 skill 工具不得越界）。

消费方：EER 的 IDP HOST 适配器仍钉在 1.2.0（它只用 RunOptions 与 AgentRun 的 1.2.0 字段）；1.3.0 对它只有新增。
