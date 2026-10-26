# V3 产品化客服前端执行证据

基线：b1e7f5576b805af21df5b9554f8422eff806605f。
独立分支：feat/v3-shop-frontend。精确执行源码和未改动核心模块指纹见
[source_manifest.json](source_manifest.json)，不是将基线SHA冒充新代码执行SHA。
最终提交由本分支Git记录与GitHub CI绑定。

- 完整 Pytest：1144 passed、0 skipped、18 warnings，140.16秒。
- 新增 HTTP/SQLite 专项：14项，纳入全量。
- 真实 Chromium：47/47，五种宽度；[完整Scorecard](scorecard.json)。
- 供应商传输：SDK MockTransport，28次模拟请求；新增付费HTTP0，费用0。
- SQL：1份pending_review申请、1份确认、1份审计、1份幂等回执，完整关联1行。
- 反馈：2份持久化事件，最终意见unhelpful/incorrect；刷新、服务重启后保留。
- 既有 LangGraph、业务事务、默认UI和历史评测源码未改动。

[SQL对账](sql_reconciliation.json)、[响应式尺寸](geometry.json)、
[失败与修复历史](failure_history.json)、[执行锁红例](busy-red.json)、[绿例](busy-green.json)。
预期注入的协议错误导致一项作业失败，页面显示错误并撤回未核验正文，不能当作业务成功。
不删除失败证据、不改业务评分，也不宣称模型表现提升。

## 页面截图

| 场景 | 修改前 | 修改后 |
|---|---|---|
| 桌面订单 | [旧页面](before-desktop.png) | [聊天订单卡片](orders-desktop.png) |
| 移动订单 | [旧页面](before-mobile.png) | [单列聊天](orders-mobile.png) |

[商品网格](products-desktop.png) · [移动服务抽屉](service-drawer.png) ·
[结果未知](unknown.png) · [权威待审核](pending-review.png) · [移动端过期](expired-mobile.png)。

这些图片来自真实 Chromium 与合成数据库，产品图标明确标注为类别示意图，非商品实拍。
用户HTML原型中的假消息、假提交和内存反馈未被当作真实业务实现。

## 复现及限制

启动命令和 REST 合同见 [前端README](../../frontend-v3/README.md)，
设计、业务复用及风险见 [验收报告](../../docs/v3/SHOP_UI_ACCEPTANCE.md)。
CI 在相同提交执行完整 Python 与 Chromium 检查，原始SDK、SQLite和Graph不上传。
私有实验目录保留于本机，定位见 source_manifest.json，不应拷贝进公共仓库。

本机隔离 Demo：Go。生产、真实退款、人工坐席转接：No-Go。
未授权新的模型实验；旧授权下的手动页面接入不代表本轮新增真实DeepSeek验收。
