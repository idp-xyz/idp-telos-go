# IDP TELOS · 系统架构与组成 v1.0

五大独立子系统：Workspace、HOST、EER、CMI、MCP Infrastructure。

- `IDP_TELOS_System_Architecture_Interactive.html`：离线交互式完整架构说明（图形内嵌）。
- `IDP_TELOS_System_Architecture_4K.svg`：可无限放大矢量主图。
- `IDP_TELOS_System_Architecture_3840x2160.png`：4K 横屏主图。
- `IDP_TELOS_Execution_Paths.svg`：两种典型协作场景。
- `TELOS_Primary_Locked_Transparent.svg`：原始锁定字形引用副本。

架构注意：五个独立模块不等于五层串行调用链；EER 的企业执行状态与业务领域最终事务权威必须分离；MCP Infrastructure 不等于另行规划的独立通用 Gateway。

此文件为架构设计说明，不代表生产环境功能或集成测试已完成。
