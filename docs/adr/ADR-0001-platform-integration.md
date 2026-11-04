# ADR-0001：IDP TELOS 平台集成与交付

状态：已接受（2026-11-04）
范围：本仓库（idp-telos-go）作为仓库工程架构 v1.0 中的 idp-telos：组件目录、平台发布清单（BOM）、兼容性矩阵、整平台组装与跨系统验收
依据：IDP TELOS 系统架构 v1.0、仓库工程架构 v1.0（idp-telos "仅编排发布件、产品级架构与集成验收；不放置 HOST/EER/CMI/MCP/Workspace 源码"）、各组件的 ADR-0001 与 idp-eer ADR-036/037/038

## 1. 背景

五个运行单元已各自交付（idp-mcp、idp-host、idp-eer、idp-cmi、idp-workspace），契约在 `idp-contracts/` 登记。每个仓库都有自己的测试与 CI，也做过两两联调（HOST↔CMI、EER↔HOST、EER↔CMI、Workspace↔HOST、EER→Workspace）。但没有一处回答这些问题：

- 平台由哪些组件的哪个版本组成（提交、镜像摘要、契约版本）？
- 谁消费谁的哪个契约版本，消费方钉住的版本与生产方当前发布的是否兼容？——实际已出现偏差：工作区 README 写 idp-eer 依赖 `idp.telos.host` 1.2.0，而 idp-eer 实际钉住的是 1.5.0。
- 系统架构的"典型路径"在所有相关组件同时运行时是否成立？

## 2. 决策

| # | 决策 | 理由 |
|---|---|---|
| T1 | **本仓库担任 idp-telos**，并继续承载 `idp-contracts/`（工程架构建议契约另立仓库；在五个组件与一位维护者的规模下，契约与集成验收放在一起可以在同一次提交里登记契约并更新兼容性矩阵）。组件源码不进入本仓库，组件仓库照旧并排克隆、被忽略 | 不新建空壳仓库；保持"一个产品根"的同时不制造源码耦合 |
| T2 | **组件目录** `catalog/`：`components.yaml`（仓库、语言、可执行物、镜像、端口、产出与消费的契约命名空间、ADR）、`integrations.yaml`（系统架构允许的每条跨系统调用：调用方、被调方、接口、凭据方式、证明它的测试或 ADR）、`ownership.yaml`（状态权威归属表）。目录由工具校验，不是装饰 | 架构文档中的边界与依赖成为可检查的数据 |
| T3 | **平台发布清单** `releases/platform-bom.yaml` 由 `tools/platform.py bom` 从并排的组件仓库生成：每个组件的提交、工作树是否干净、本地镜像摘要（有则记录）、它产出的契约版本；**兼容性矩阵** `releases/compatibility-matrix.yaml` 由 `tools/platform.py matrix` 从各组件仓库里钉住的契约副本（`tests/contracts/<命名空间>/<版本>/`）生成：消费方、命名空间、钉住的版本、当前登记版本、是否兼容（同主版本且登记版本 ≥ 钉住版本，并且登记表中存在钉住版本的文件，校验器的"只做新增"检查覆盖了两者之间的每一步） | 版本组合由工具得出而不是手写；漂移在 CI 里暴露 |
| T4 | **`tools/platform.py check`** 校验：目录中的组件、契约与登记表一致；每条集成的两端都在目录里、引用的契约已登记；BOM 与并排仓库的当前提交一致（否则提示重新生成）；兼容性矩阵中没有不兼容项；ownership 中每类数据恰有一个权威。缺少某个仓库的检出（如 CI 中无权访问的私有 idp-eer）报告为 NOT_RUN 而不是通过 | 与契约校验器同样的原则：不能运行就不算通过 |
| T5 | **整平台组装** `deploy/compose/platform.yaml` 与 **跨系统验收** `deploy/compose/e2e.sh`：一个 PostgreSQL（每个组件一个库）、Keycloak（为全部组件生成的 realm）、idp-cmi、idp-host（含 worker 与模型替身）、idp-workspace，全部用各组件仓库构建的镜像（BOM 记录其摘要）。验收场景为系统架构的 **PATH A（普通问答、辅助决策：Workspace → HOST → CMI，不经过 EER）**：成员经 Keycloak 登录得到的令牌在 Workspace 调用动作，Workspace 以令牌交换调用 HOST，HOST 运行一个 `context.cmi = true` 的 agent，从 CMI 取得包含预先登记文档的上下文包，结果回到成员的收件箱；验收核对 HOST 的运行、CMI 中的包及其条目、Workspace 的收件箱三处一致 | 证明系统架构的典型路径在真实进程、真实身份提供方、真实契约之间成立 |
| T6 | **EER 暂不进入整平台组装**：其镜像构建需要从 Docker Hub 拉取基础镜像（本环境不可达），其身份校验要求 https 发行者。PATH B（高可靠执行）的各段已在组件仓库内分别证明（EER ADR-036/037/038）；纳入整平台需要 EER 的离线镜像构建与 https 发行者，记为后续里程碑 | 不伪造：缺的部分明确列出 |

## 3. 里程碑

- **P1**（本 ADR）：T1–T6；本仓库的 CI 运行契约校验器（检出公开的组件仓库）与 `tools/platform.py check`（idp-eer 的项为 NOT_RUN）。
- **P2**：EER 进入整平台（离线镜像、https 发行者），PATH B 验收：EER T2 审批推入 Workspace、成员在 Workspace 批准、EER 继续执行。
- **P3**：部署模板（Kubernetes / Helm）与环境配置模板；镜像推送、签名与 SBOM。

## 4. P1 实施结果（2026-11-04，WSL 实测）

- T2：`catalog/components.yaml`（五个组件；`platform` 标明是否在整平台组装里）、`catalog/integrations.yaml`（七条跨系统调用，每条带凭据方式与证据；Workspace→EER 决定标为未与真实 EER 运行）、`catalog/ownership.yaml`（六类权威）。整理时更正了一处：HOST 只经 MCP 协议使用 idp-mcp，不读 `idp.telos.mcp` 的文档，不再列为该契约的消费方。
- T3：`releases/platform-bom.yaml`（五个组件的提交与干净状态、平台内三个组件的镜像 id、产出的契约版本、登记表的契约版本）；`releases/compatibility-matrix.yaml`：首次生成时发现 HOST→CMI 与 Workspace→HOST 两处"未钉住"（消费却没有消费方契约测试）。已在两个仓库补上钉住副本与消费方测试（idp-host `internal/cmiclient/contract_test.go` 钉 `idp.telos.cmi` 1.4.0；idp-workspace `internal/dispatch/host_contract_test.go` 钉 `idp.telos.host` 1.7.0：读取的字段在契约里必填、发布的示例能解码）。现在四行全部兼容：EER 钉 cmi 1.2.0、host 1.5.0（落后但中间只有新增），HOST 与 Workspace 钉当前版本。
- T4：`tools/platform.py check`（单元测试 6 项：判定钉住、改过的副本、比登记更新的钉住、未钉住、BOM 漂移、重复权威、悬空集成、缺检出为 NOT_RUN、`--allow-missing`）。
- T5：`deploy/compose/e2e.sh` 从并排仓库构建 idp-cmi、idp-host、idp-workspace 镜像并运行 PATH A：bob（Keycloak 口令授权）在 Workspace 调用动作 → Workspace 以 RFC 8693 交换得到 HOST 受众的令牌 → HOST 运行 `context.cmi = true` 的 agent，以自己的服务账号（客户端凭据，`cmi-reader`）向 CMI 解析上下文 → CMI 包含预先登记的"Pickup policy"文档 → 结果回到 bob 的收件箱；三处核对：HOST 运行 COMPLETED 且带 `contextPackageId`，CMI 中该包含该文档且 purpose 为 assist，Workspace 的调用记录指向同一运行。
- 配置上的一处现实约束：HOST 只在开发模式下允许以明文 http 连接模型（此处为确定性替身），平台组装里 HOST 因此开启开发模式；令牌仍全部由 Keycloak 签发并以 JWKS 校验。

**验证**：`tools/platform.py check` 通过；工具测试通过；契约校验器 275/275；PATH A 验收通过（真实 Keycloak 26.8.1 与三组件镜像）。CI 在 GitHub 上按 BOM 检出公开仓库运行前三项。

**局限（不标 PASS）**
- EER 不在整平台里（T6），PATH B 未做整平台验收；CI 中 idp-eer 的钉住检查为 NOT_RUN（允许）。
- BOM 的镜像 id 是本机构建的结果，没有推送到镜像仓库，也没有签名与 SBOM；"发布"仍是"这些提交"。
- 只有 compose 组装；Kubernetes / Helm 与环境模板属 P2。

## 5. P2 实施结果（2026-11-04，WSL 实测）

- **EER 进入整平台**（T6 解除）：idp-eer PR #24 增加 `tools/development/runtime_image_offline.sh`（宿主机编译五个二进制、与 `runtime/Dockerfile` 相同的 ldflags，scratch + CA + pilot Actors/Plans，`--network none`）；平台组装新增 Temporal（本地 `local/temporal:1.7.3`）、eer-migrate、eer-configure、eer-api、eer-worker（`conformance` 调用器：EER 自带的确定性 Actor 替身）、eer-projector（ADR-038 收件箱推送，生产者 `svc-eer` 以客户端凭据取令牌）。EER 的角色由其 `deploy/postgres/bootstrap-runtime-roles.sql` 在共用 PostgreSQL 的 `eer` 库中建立。
- **https 发行者**：每次运行生成一个测试 CA 与 Keycloak 证书（SAN 127.0.0.1、keycloak），Keycloak 同时开 https 7443（浏览器与发行者）与 http 7080（容器内取令牌与 HOST/CMI/Workspace 的 JWKS）；所有令牌的 `iss` 为 `https://127.0.0.1:7443/realms/idp`。EER 只接受 https 的 JWKS（http 仅限 `.invalid` 测试发行者），因此 EER 容器以 `SSL_CERT_FILE` 信任本次的测试 CA，从 `https://keycloak:7443` 取公钥——EER 代码未改。
- **身份映射**：realm 固定用户 id，脚本据此生成 EER 的 `EER_PRINCIPAL_MAPPINGS`（operator → actor operator，bob → actor bob）与授权策略（`execution.approve` 只给映射了 actor 的人）。Workspace 以成员名（`preferred_username`）作为 `actor_id`，与 EER 的映射一致。
- **PATH B 验收通过**：operator 发布 `parcel.address.rejected` 事件并提交 T2 意图（计划 `address-error-actions`）→ 执行进入 Waiting → EER 投影器把审批推入 us-ops 收件箱（指派 approver）→ bob（工作空间角色 approver）调用 `eer-approvals/approval.approve` → Workspace 以 RFC 8693 交换得到 `eer-runtime` 受众的令牌，调用 `POST /v1/executions/{id}/approval`（202 accepted）→ 执行 Completed，EER 审计记录 `approval.approved` 的 actor 为 bob → 投影器把事项结束为 resolved / approved。PATH A 在 https 发行者下同样通过。
- 端口：本机已有的 idp-pulsar 占用 18080，EER 改用 18180–18182。

**局限（不标 PASS）**
- EER 的 Actor 由 `conformance` 替身执行，没有真实连接器；EER 的 Intelligence（Python）worker 未进入整平台（Decision 与 Agent Turn 的联调在 EER 仓库内已证明）。
- 测试 CA 每次运行生成、有效期两天，只用于验收。
