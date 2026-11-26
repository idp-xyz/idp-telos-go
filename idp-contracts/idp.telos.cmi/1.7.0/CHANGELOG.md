# idp.telos.cmi 1.7.0

发布：2026-11-26。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.6.0 及更早版本：**只有向后兼容的新增**（知识库 KB8：读一份文档的指定位置、在一份文档里按原词找，idp-cmi ADR-0001 第 27 节 D71–D78，`tools/validate.py` 的不变量检查）。旧版本的文件保留。

| 变更 | 内容 |
|---|---|
| ContextItem 的 `match` 新增值 `exact` | `POST /v1/context/find` 的条目：原词命中（子串，空白规范化，拉丁字母不分大小写） |
| ContextItem 新增可选的 `hits` 与 `hitCount` | `hits`：`[{offset, length}]`，按字符（码点）数、相对条目的 `excerpt` 原样，只列完全落在摘录里的命中；`hitCount`：这一块里命中的总数 |
| KnowledgeDocument 新增写入字段 `awaitsOriginal` | true 时这一版一开始就是 `pending`，随后存进 PDF 或 Office 原件，文本转换提交时才生效；服务没有开转换时是 503 |

行为变化（契约之外，见 `docs/adr/kb8-cmi-api.md` 与 idp-cmi 的 ADR D77）：给已生效的版本（active 带用户给的文本、superseded、revoked、expired）存要转换的原件，1.6.0 会把它改成 `pending`，现在是 **409**；要带原件就写新版本、带 `awaitsOriginal`。`POST /v1/context/read`、`POST /v1/context/find` 是新的路由（请求体不在契约里，见 OpenAPI）。迁移 0019。
