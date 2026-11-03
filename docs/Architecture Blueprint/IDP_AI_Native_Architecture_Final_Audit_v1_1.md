# IDP AI-Native Enterprise Architecture — 最终全面审查与评估

**审查对象：** IDP AI-Native Enterprise Architecture Visual Blueprint v1.0（SVG / 4K PNG / 单文件 HTML / ZIP），及此前《IDP AI-Native Agent Architecture v1.1》所确立的职责边界。

**审查日期：** 2026-11-02  
**修订交付：** Visual Blueprint v1.1 Reviewed（**设计审查候选**，不等于用户正式批准、系统实施完成或生产验收）  
**审查性质：** 源码与静态架构一致性检查、真实 Chromium 浏览器交互测试、图形导出测试、解包完整性验证、视觉可读性评估。**未取得 EER/HOST/CMI/MCP/领域系统的可执行工程仓库、运行日志或生产指标，因此不包含真实后端集成、安全渗透或压力测试。**

## 1. 审查裁决

**结论：架构方向保留，交付图示经修订后可进入详细设计评审；尚不可作为生产上线验收通过的证据。**

- 既定 **四大子系统**：Workspace、HOST、CMI、MCP Infrastructure。
- 既定 **独立核心执行运行时**：EER（Event-Native Enterprise Execution Runtime）。
- 既定 **五个执行逻辑角色**：Actor、Validator、Planner、Executor、Verifier。角色不必一一部署为独立 Agent 或微服务。
- **业务权威数据和事务的最终所有者**仍为 Brant / PRISM / Parcel 等领域系统。
- 基础设施与安全治理为**横切能力**，并非另设一个与上述子系统重叠的业务权威中心。

**阶段性专家评估**（针对架构蓝图与交互交付，而非平台的软件质量）：

| 维度 | 修订后评分 / 10 | 评价 |
|---|---:|---|
| 核心职责与数据所有权 | 9.1 | 分层合理；仍需跨域授权与失败边界 ADR |
| 执行语义与架构表达 | 8.8 | EER 五角色及域提交链路清晰，异步结果/恢复语义需进一步形式化 |
| 安全和治理的图示完整性 | 8.3 | 已包含治理层，具体信任边界、威胁模型需单独详图 |
| 视觉质量与信息组织 | 9.0 | 深蓝专业，层级稳定；小字阅读有明显压力 |
| HTML 交互完整性 | 9.2 | 主要流程实测通过；移动触控手势和无障碍尚未系统验收 |
| 文件和导出可靠性 | 9.7 | SVG、PNG、HTML、ZIP 数据完整，浏览器导出正常 |
| **蓝图交付综合参考评分** | **9.0** | 可以用于架构宣讲及详细设计入口 |
| **真实工程生产就绪程度** | **未评定** | 缺少源码、端到端联测、压测与安全验证证据 |

以上分数属于审查者基于分项准则的**主观设计评分**，不是实验室认证、定量性能 Benchmark 或实际生产成熟度证明。

## 2. 已确认并完成修订的问题

| 编号 | 等级 | v1.0 原始问题 | v1.1 实施修正 | 复验 |
|---|---|---|---|---|
| V-01 | P1 | HOST → EER 单向箭头易被误读为不允许 EER 回调 HOST 推理或反馈结果 | 改为双向接口语义 | SVG 确认 |
| V-02 | P1 | 「企业任务执行路径」高亮只有 2 条连接，缺少 EER → Domain 最终业务提交 | 提交接口纳入 execution 逻辑路径，执行路径高亮 3 条连接 | Chromium 通过 |
| V-03 | P1 | 外部“MODEL CAPABILITY”包含 Harness Adapters / Model Gateway，与 HOST 所有权混淆 | 外部区域改为模型与推理服务端点；Harness 和 Model Router 仍归 HOST | 内容核对通过 |
| V-04 | P1 | 手机视口 390px 时，Zoom In/Out 被 `min-width` 与百分比计算抵消，按钮没有实效 | 更换为基于容器的图纸像素宽度计算，允许实际缩放 | Chromium 通过 |
| V-05 | P2 | 移动设备“适应宽度”提示与实际滚动操作不符 | 改为「重置视图」 | 文案核对通过 |
| V-06 | P2 | 模型连接有 `data-flow=model`，但没有对应筛选项 | 新增「模型推理路径」 | Chromium 通过 |
| V-07 | P2 | SVG 内交互节点状态没有统一同步 `aria-pressed`，且内嵌 SVG 标记可能妨碍辅助技术识别内部交互项 | 增加状态同步；内嵌 SVG 改为 `role=group`，保留详情按钮替代入口 | DOM 检查通过（未完成完整屏幕阅读器验收） |
| V-08 | P2 | 图上 “VERSION 1.0 / DESIGN BASELINE” 可能被误认为工程正式基线 | 标记 `VERSION 1.1 / REVIEW CANDIDATE`，HTML 标题同步调整 | 文件核对通过 |

原始文件未被覆盖。v1.1 是新增的修订交付。

## 3. 真实技术检查证据

### 3.1 静态与打包检查

- 原始 ZIP：4 个成员；CRC 全量验证通过；每个成员解包内容与原文件逐字节一致。
- 修订 ZIP：HTML、SVG、PNG 以及桌面/手机预览共 5 个成员；CRC 验证通过；解包与原文件逐字节一致。
- SVG：原生 `2560 × 1440` 的 XML 矢量文档，可解析；由独立文本、线条、矩形、圆形和连接节点构成，不是将 PNG 封装在 SVG 中。
- PNG：实际像素尺寸 `5120 × 2880`，16:9，RGB。
- v1.1 浏览器 SVG 和 PNG 导出均取得实际下载结果；导出 SVG 可以再次按 XML 解析；导出 PNG 实际尺寸为 `5120 × 2880`。
- 独立 HTML 不引用外部 JavaScript、远程图片或 CSS，因此离线静态阅读与交互不依赖网络。

### 3.2 Chromium 实测

| 检查 | v1.1 结果 |
|---|---|
| 模块详情选择 | Workspace、HOST、EER、CMI、MCP：5/5 通过 |
| 路径切换 | 全图、交互、执行、上下文、工具、模型、领域：7/7 通过 |
| 执行路径连接高亮 | 3 条，包含到 Domain 的提交连接 |
| HTML 的 SVG 导出 | 成功，下载文件可解析 |
| HTML 的 PNG 导出 | 成功，5120 × 2880 |
| 桌面缩放 | 宽度发生正确变化 |
| 390px 手机缩放 | 放大和缩小均有实际宽度变化 |
| 页面级横向溢出 | 320、390、768、1024、1440px 均未出现；图纸可在独立容器中横向滚动 |
| JavaScript `pageerror` | 0 个 |
| 图纸模块键盘焦点 | 五个节点存在 `tabindex=0` 与操作入口；尚未进行端到端读屏验收 |
| 浏览器打印 | 原始按钮能触发打印行为；**未验证各系统的纸质分页效果** |

**测试边界：** 测试使用 Chromium 和预生成内容；没有证明 Firefox、Safari、所有移动手势、WCAG AA、实际云部署或安全性验收均已通过。生成的 SVG 依赖用户设备上的中文字体；不同操作系统可能有字体替换和细微版式差异。

## 4. 架构审查重点

### 4.1 职责划分基本一致

- Workspace：UI 状态、对话/任务/审批呈现、配置入口；不得冒充 EER 执行权威。
- HOST：AgentDefinition、AgentRun、Session、Harness、Model Router、Skills、A2A 委派；不能再建第二套企业 Durable Workflow Engine。
- EER：Goal / Task / Plan / Execution / Checkpoint / Approval / Verification，持有业务执行权威状态，推理工作可通过 HOST 委派。
- CMI：ContextPackage、Memory Record、Knowledge Index、来源、版本与过期；不能把历史记忆当作当前业务事实。
- MCP Infrastructure：现代 MCP Gateway、Server / Tool Registry、工具身份与权限校验、联邦路由和审计；不取代领域业务 API 的事务权威。
- 领域系统：订单、库存、轨迹、清关、财务等最终事务提交和业务规则。

### 4.2 EER 的“执行”和 HOST 的“Agent 协调”必须长期分离

Agent 可以提出计划、选择 Skill 和解释任务，但只有 EER 校验、持久化且版本化的执行计划能成为企业任务的正式执行计划。EER 的重试、恢复、超时及审批状态必须独立于 HOST Session 和 LLM 对话上下文；否则一旦模型运行崩溃、连接断开或模型服务切换，企业执行事务就丧失权威状态。

### 4.3 直接 Domain API 是受控路径，不得意味着跳过治理

总图保留 `EER ↔ Domain API` 和 `EER / HOST ↔ MCP ↔ Domain` 两种访问方式，这是允许的逻辑区分。**但任何写操作即使没有经过 MCP，也必须受到 EER 的审批/执行记录约束，并在领域系统重新完成授权、幂等、前置条件检查和事务提交。** 是否强制所有工具写操作都由 MCP 统一转发，需要 ADR 进一步决策。

### 4.4 MCP Modern Stateless 与 EER Durable Execution 不冲突

MCP `2026-07-28` 取消旧式协议会话与初始化握手；这只是通信层的特征，不表示领域业务操作和 EER 执行状态必须无状态。需避免把可选 MCP Tasks 扩展错误等同于 EER 企业 Task 状态机。**协议采用声明不等于已通过官方 conformance 测试。**

## 5. 剩余开放问题和发布门禁

以下不是“页面坏了”，而是尚未形成完整实施证据或正式 ADR 的架构议题。

| 优先级 | 待完成事项 | 应提交的验证证据 |
|---|---|---|
| P0 | 跨服务身份传播、委派授权、租户隔离与 Policy Decision / Enforcement 归属 | IAM/Policy ADR、端到端权限测试、跨租户负面用例 |
| P0 | EER → Domain 写操作协议 | Command Schema、业务幂等键、Outbox/Inbox、审计和失败补偿实测 |
| P0 | EER ↔ HOST 的 AgentRun / Execution 映射与恢复边界 | Execution API + AgentRun Contract、断点恢复和重试测试 |
| P0 | CMI 业务来源优先级、记忆失效、证据及敏感信息治理 | ContextPackage Schema、污染测试、撤销传播、时效评估 |
| P0 | MCP 2026-07-28 真实一致性（仅支持 Modern Stateless） | Conformance、Discover、错误码、Mcp-Method/Mcp-Name、权限测试记录 |
| P1 | Agent / Skill 生命周期和供应链信任模型 | 版本签名、沙箱隔离、资源预算、审批及回滚测试 |
| P1 | EER 运行指标和故障恢复目标 | SLO/RTO/RPO、错误预算、负载测试、重复消息与 Worker 崩溃注入 |
| P1 | 多模型/多 Harness 接入一致性 | OpenAI/Claude/Google/本地模型适配回归测试 |
| P2 | 图纸小字与会议投影阅读优化 | 另交付简化的演示版，减少注释密度、统一文本分级 |
| P2 | 专业无障碍与跨浏览器验证 | 屏幕阅读器、WCAG 对比度、Safari/Firefox、触控可用性 |

## 6. 视觉和交互专业度评估

**优点：** 视觉层级清晰；四大子系统和 EER 得到明确区分；中央执行闭环形成强视觉焦点；颜色系统稳健；图文与交互详情一致。图纸可以作为架构白皮书、技术介绍和项目评审的主视觉。

**保留意见：** 127 个 SVG 文本元素中，73 个使用了低于 16 个 SVG 单位的字号。整图缩放到普通 1080p 的 1920px 横向显示后，部分标注约为 9–11 屏幕像素；在手机或会议投影中的可读性较差。建议分为**高层概览版（少字）**和**技术细节版（当前总图）**两种版式，而不是继续把所有细节挤入同一张图。

SVG 字体使用 Inter、Noto Sans CJK SC、Microsoft YaHei 等本地字体回退链。跨操作系统发布时应人工复查中英文换行及字宽；如需交付给印刷厂，可单独制作轮廓化发行版本，但不应覆盖可编辑原始 SVG。

## 7. 最终裁决与推荐用途

- **可以：** 对内架构评审、投资人/客户技术介绍、白皮书主图、Detailed Design 入口、ADR 讨论和团队职责划分。
- **暂不可以：** 声称各系统已经开发完成；作为 MCP conformance 证明；作为安全审计通过证明；作为高可用/性能 SLA 证明；将概念架构图当作详细部署图和网络防火墙图。
- **建议冻结的部分：** “4 大子系统 + 独立 EER + Domain 业务权威”的结构原则、五单元基本状态所有权。
- **需要进一步锁定的部分：** 具体调用契约、跨系统策略治理、事件可靠性协议、长期任务与 AgentRun 的边界，以及 CMI 事实与记忆冲突处理。

**最终结论：Visual Blueprint v1.1 Reviewed 可作为“设计审查候选基线”发给架构组评审。通过上述 P0 契约及安全 ADR 评审后，才适合晋级为正式工程基线；生产验收必须另行开展实际系统测试。**

## 8. 参考

- MCP 2026-07-28 官方发布：https://blog.modelcontextprotocol.io/posts/2026-07-28/
- 官方 MCP SDK Modern 迁移和协议差异：https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28
- 本次浏览器测试程序：`test_idp_visual_v11_final.py`（仅在审查工作环境执行）
