# M7 退货确认卡片浏览器验收

源码 `0c32165d1a932529fa90ce36acffe910aca23ba0`，固定模型、隔离合成 SQLite，无付费请求。真实 Chromium 完成10阶段，0 pageerror，包含多轮草稿、插问、更正、取消、最新预览、服务重启、刷新、SSE补读、应用确认和重复点击。

最终状态卡片为 `PENDING_REVIEW`，仅在SQL核验申请、确认、审计和幂等回执后显示；截图中显示模拟申请编号，不代表真实平台受理。固定模型只证明页面协议和业务状态，不证明真实模型语义质量。

证据：[scorecard](scorecard.json)、[SQL终态](sql_final.json)、[桌面截图](submitted-desktop.png)、[移动截图](submitted-mobile.png)。
