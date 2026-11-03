# idp.telos.host 1.1.0

发布：2026-11-03。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.0.0：**只有向后兼容的新增**（`tools/validate.py` 的不变量 `idp_telos_host_1_1_0_is_additive_over_1_0_0` 检查：定义都在、没有新增必填、没有删属性、根文档都在）。1.0.0 的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| AgentRelease 新增可选 `skills` | 编译时嵌入的 SkillRelease（EmbeddedSkill：名字、版本、摘要、内容），与 `definition.skills` 的引用一一对应（ADR-0001 D17） |
| Definition 新增可选 `skills` | SkillRef 列表：按名字引用 skill，可钉版本 |
| Limits 新增可选 `maxTokensPerRun` | 每个 Run 的 token 上限，缺省 = 租户策略的默认值（ADR-0001 D15） |
| 新对象 SkillRelease（CONTRACT） | skill 的不可变发布件（`idp.host.skill/v1`），四眼审批，审批人参与摘要 |
| 新对象 SkillRef、SkillDefinition、EmbeddedSkill（HELPER） | 上述结构的组成部分 |
| Envelope 的 `document` | 现在是 AgentRelease 或 SkillRelease |

实现侧一致性测试（`go test ./internal/run -run TestContract`）同 1.0.0，另覆盖 SkillRelease 示例（`agent.OpenSkill`）、嵌入 skill 的 AgentRelease 示例，以及 5 种 skill 相关的拒绝变体。
