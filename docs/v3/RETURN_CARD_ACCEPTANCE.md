# M7 退货确认卡片验收

本轮基于 `27eec1b` 的 Web Demo，只补状态投影、前端卡片和重复打开保护。固定模型浏览器验收与业务 SQLite 终态分别记录。

## 场景矩阵

|场景|HTTP/SQLite 验证|结果|
|---|---|---|
|有效预览，无等待|`GET /return-card`|`READY`，仅 `open/refresh`|
|有效等待|确认打开后 GET|`READY`，允许 `accept/reject/cancel`|
|拒绝或取消|决策后读卡片|`REJECTED`，申请/确认/审计计数为0|
|过期或更正失效|修改隔离预览后 GET|`EXPIRED`，无接受按钮|
|事件已发出、回执未知|故障注入后 GET|`UNKNOWN`，只有 `refresh`，不重发 POST|
|权威申请已提交|完整 M6 SQL 回执后 GET|`PENDING_REVIEW`，显示真实 request_id|
|重复打开同一预览|第二个 POST|HTTP 409，确认等待和 SQL 不增加|
|CSRF/跨会话|实际 TestClient|403，SQL 不变|
|SSE 断开/Last-Event-ID|真实事件表与浏览器重连|只补读，不重复模型/工具/写入|
|Graph 镜像损坏|只读回执查询|以 SQL 为准；未知时 `UNKNOWN`|

新增 FastAPI 回归测试覆盖上述五状态和重复打开；M6 原子提交专项及原有 Web 回归保持不变。浏览器测试使用真实 Chromium、隔离 Fixture 和固定模型，不使用付费 DeepSeek。

## 证据

- [固定模型浏览器 Scorecard](../../results/web_demo_return_card_20261024_run2/scorecard.json)
- [SQL 终态](../../results/web_demo_return_card_20261024_run2/sql_final.json)
- [提交结果截图](../../results/web_demo_return_card_20261024_run2/submitted-desktop.png)
- [移动截图](../../results/web_demo_return_card_20261024_run2/submitted-mobile.png)
- 本轮真实模型结果仍只引用上一轮 `results/web_demo_deepseek_20261024_run1`；没有为卡片改动重新付费。

## 边界

五状态、按钮权限和 HTTP/SQLite 终态已实际验收。真实生产认证、外网部署、审核、退款、换货和正式平台回执仍未实现；这些不属于本轮页面演示能力。
