# idp.telos.cmi 1.6.0

发布：2026-11-25。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.5.0 及更早版本：**只有向后兼容的新增**（知识库第三步，原件的转换，idp-cmi ADR-0001 第 26 节 D61–D70，`tools/validate.py` 的不变量检查）。旧版本的文件保留。

| 变更 | 内容 |
|---|---|
| KnowledgeDocument 新增可选字段 `ingest` | 原件（PDF、Word、PowerPoint、Excel）转成文本的进展：`state`（queued、running、succeeded、truncated、failed）、`attempts`、`error`（固定的原因码，不是转换器的原话）、`notice`（给上传的人的说明，例如被截掉的工作表、没有文字层的页）、`updatedAt`、`nextAttemptAt` |
| KnowledgeDocument 的 `status` 新增值 `pending` | 这一版的原件正在第一次转成文本：不是现行、不取代任何版本、不被检索；转换成功时在一个事务里变为 `active`。失败时仍是 `pending`，前一版照常可取 |
| ContextItem 的来源的 `locator` 新增 `slide`、`sheet`、`range` | PPTX 的幻灯片序号、XLSX 的工作表名与这一块覆盖的单元格范围（例如 `A12:F40`）；`page` 对 Word 文档是第几个显式分页符之后 |

行为变化（契约之外）：`POST /v1/knowledge/documents/{id}/ingest` 重新排队一次转换；迁移 0018；新增内部文件端点与票据（不在契约里，见 idp-cmi ADR-0001 D62）。消费者读 `status` 时要容忍 `pending`。
