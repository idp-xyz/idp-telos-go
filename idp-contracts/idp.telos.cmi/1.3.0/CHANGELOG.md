# idp.telos.cmi 1.3.0

发布：2026-11-04。生产者 idp-cmi；权威副本 `idp-cmi/api/contracts/`，这里的 `schema.json` 与 `examples.json` 必须与之逐字节相同。

相对 1.2.0 及更早版本：**只有向后兼容的新增**（M5，`tools/validate.py` 的不变量检查）。旧版本的文件保留，状态 SUPERSEDED。

| 变更 | 内容 |
|---|---|
| 新定义 AsOf；ContextPackage 新增 `asOf` | 双时态视图（D25）：valid 业务时点、recorded 登记时点；包记录它代表的视图 |
| ContextPackage 新增 `projectionLag` | 组装时向量投影落后规范行的秒数（D26） |

实现侧一致性测试新增：示例包带 asOf；2 种新拒绝用例（asOf 时间格式、asOf 未知字段）与负的 projectionLag。
