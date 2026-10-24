# M7 最终退货确认卡片浏览器验收

最终源码 `fab6caeccba1b8f3efe8be65608690e7f385bd7b`，固定模型、隔离合成 SQLite，无付费请求。真实 Chromium 完成10阶段、0 pageerror，包含多轮草稿、插问、更正、取消、最新预览、服务重启、刷新、SSE补读、应用确认和重复点击。

最终卡片状态为 `PENDING_REVIEW`，仅在SQL核验申请、确认、审计和幂等回执后显示；截图显示模拟申请编号，不代表真实平台受理。固定模型仅证明页面协议和业务状态。

证据：[scorecard](scorecard.json)、[SQL终态](sql_final.json)、[桌面截图](submitted-desktop.png)、[移动截图](submitted-mobile.png)。
