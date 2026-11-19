# idp.telos.host 1.10.0

发布：2026-11-19。生产者 idp-host；权威副本 `idp-host/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.9.0 及更早版本：**只改描述，没有改动任何形状**（`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| `ContextPolicy` 的描述 | 写明 HOST 对 CMI 条目按敏感级别筛选（idp-host ADR-0001 D44，平台 ADR-0003 D7）：发布件可能使用外部模型，或可能调用会把内容送出企业的工具（HOST 配置 `gateways[].externalTools`）时，级别高于租户上限（`files.externalModelCeiling`）的条目、以及级别为空或不认识的条目，都不交给这次运行；模型只被告知条数与级别。事件 `idp.host/context.resolved` 的 `items` 仍是 CMI 返回的条数，新增 `appended`（实际追加到运行 context 末尾的条数）；新事件 `idp.host/context.withheld {count, byLevel, ceiling, trigger}`。运行中刷新上下文按创建运行时记下的同一上限筛选；子运行不继承没有筛选过的父运行的 CMI 条目 |
| 描述首段 | 增加 1.10.0 的说明 |

没有新增字段：自定义事件的名字与字段不属于 schema，请求与响应的形状不变。1.9.0 的使用者不需要任何改动。

实现侧一致性测试：`contractID` 改为 1.10.0，示例重新生成（只有描述行变化）。
