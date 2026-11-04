# idp.telos.host 1.7.0

发布：2026-11-04。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.6.0 及更早版本：**只有向后兼容的新增**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| ContextPolicy 新增 `onStale` | 后续步骤复验 CMI 包发现失效时的处理（CMI ADR-0001 D24）：continue（缺省）/ refresh / fail（CONTEXT_STALE）；事件 `idp.host/context.stale` |

实现侧一致性测试新增：示例发布件 `onStale: refresh`；1 种新拒绝用例（onStale 值）。
