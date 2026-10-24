# M7 五状态卡片验收

范围与原始差异分类见 [RETURN_CARD_CONTRACT.md](RETURN_CARD_CONTRACT.md)。本轮复用原 M7 服务、M6 事务及回执，只补投影、界面和恢复缺口。

## 当前实验证据

原固定模型主线 run4 完成10阶段、0页面错误，见 [Scorecard](../../results/web_demo_return_card_20261024_run4/scorecard.json)。它只证明该提交 `fab6cae` 的主线，不替代后续故障交互测试。

新增 `tests/test_return_card.py` 首轮6项为5失败/1通过，修复后6项通过。独立新增故障检查包括：原幂等键回放、历史拒绝后新预览、GET前后SQL/Graph完全一致、运行中及中断确认、Graph损坏、权威SQL不一致、数据库缺失、真正的业务owner变化、重启后预算/任务不重派发。测试器从不调用付费模型。

`scripts/eval_return_card.py` 使用真实 Chromium、FastAPI、原生 Graph、全新合成 SQLite；每案独立Fixture。旧UI在同一服务器下复现断线后不能维持UNKNOWN；候选UI的10场景已通过开发期重验。最终提交指纹、Scorecard与CI结果随最终证据一并记录。

|场景|实际触发方式|SQL/HTTP判据|
|---|---|---|
|READY → PENDING_REVIEW|真实页面双击接受|申请/确认/审计/幂等各1；仅1次决定POST|
|拒绝、取消|真实按钮|REJECTED；正式业务四表各0|
|到期|只修改测试Fixture的过期时间，再GET|EXPIRED；接受不可用，关闭旧等待无申请|
|更正|通过固定模型与业务工具更正商品并生成新预览|旧等待失效，关闭旧等待后新预览可打开；无申请|
|请求未送达|浏览器中止该POST，不转发|UNKNOWN；刷新及服务重启不重发，SQL各0|
|COMMIT后丢响应|真实转发POST，SQL成功后丢响应|先UNKNOWN，GET权威回执后PENDING_REVIEW；各1|
|COMMIT前异常|M6真实事务中的故障注入|UNKNOWN；issued未消费，正式四表各0|
|Graph镜像损坏|只破坏测试Graph文件|完整SQL回执仍显示PENDING_REVIEW，不重提|
|数据库不可用|暂时重命名隔离业务库|503/UNKNOWN；只读、不建空库，恢复后重新核验|
|CSRF及跨会话|真实浏览器发缺失/伪造CSRF，另一浏览器读他人SID|403；SQL不变|
|SSE恢复、Last-Event-ID、重复订阅|既有TestClient合同及真实浏览器刷新/重连|事件只补读，工具和申请不增加|

业务成功仅由完整 M6 SQL 回执证明。五种状态均在真实 HTTP/SQLite 条件下触发；没有仅靠前端枚举切换来冒充业务结果。徽标色彩、加载提示、未回执HTTP标识是界面表现，不是业务数据库权威。

## 失败记录与边界

- 初次主线刷新后没有历史Trace：保留 `web_demo_return_card_20261024_run1`，改为先补读后续订。
- 早期CI因改动共享 `LocalConfirmation.open()` 破坏 M6 旧 accepted 的语义：共享文件已恢复；防重复限定在Web入口，保留原断言，不修改M6事务。
- 故障矩阵 run1 的丢响应注入器未真正转发请求，源于 Playwright 第二回调参数覆盖默认场景名；保留失败，将绑定改为 keyword-only，再用实际POST和SQL各1证明触发路径。
- UI未知结果只允许读取；原M6显式操作员恢复入口仍存在。请求是否到达始终无法核实时，可持续UNKNOWN，不能擅自制造成功或盲目重放。
- 本轮没有付费DeepSeek请求，费用0；不复制上一轮真实模型成绩。模型虚构旧预览等历史语义风险仍未由本轮固定模型测试解决。

**Go：本机合成数据Demo和继续本地评测。No-Go：生产身份、真实平台申请、审核或退款。** 不自动合并main、替换默认Runtime或启动Benchmark。
