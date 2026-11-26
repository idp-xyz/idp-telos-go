# idp.telos.host 1.12.0

发布：2026-11-26。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.11.0 及更早版本：**只有向后兼容的新增**（知识库 KB8：HOST 的知识检索工具与问题改写，idp-host ADR-0001 第 47、48 节 D65–D69，`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| `ContextPolicy` 新增可选的 `tools` | 发布件开启的 HOST 内置知识工具：`knowledge__search`、`knowledge__read`、`knowledge__find`（至多 3 项、不重复；要 `context.cmi: true`）。工具以成员的身份（令牌交换）调 CMI，不经网关；`skill` 与 `tool.allow` 里不能写 `knowledge__` 前缀的工具 |
| `ContextPolicy` 新增可选的 `rewrite` | `off`（缺省，与以前相同）或 `model`：线程里已有前文时，运行开始那次检索之前，由发布件自己的模型把问题改写成不依赖上文的一句 |

事件 `idp.host/context.package`、`idp.host/context.rewrite` 与 `context.resolved` 的新字段不属于 schema。新增的部署设置（`IDP_HOST_MEMBER_ROLES`、`IDP_HOST_REWRITE_TIMEOUT`、`cmi.auth.sessionBound`、`providers[].reasoningEffort`）见平台的 RUNBOOK；迁移 0014 到 0016。
