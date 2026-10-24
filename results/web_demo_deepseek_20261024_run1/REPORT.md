# M7 真实浏览器 / DeepSeek Development Smoke

运行源码：`15498f29158de93c382c49aee63657a6719158f7`；运行期间源码指纹不变。付费前门禁为 [968项完整CI](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40068447544)、真实浏览器离线run5及价格核验。此运行只使用全新合成formal Fixture，不连接生产数据。

浏览器真实输入6阶段、显式点击应用确认、重复提交、刷新及SSE重连完成，0 pageerror。自动业务状态检查全部通过；人工回答核验有失败，不能据此宣称完整语义正确或任务总体成功率。

|阶段|业务检查|请求|输入/输出Token|阶段耗时ms|usage估算元|
|---|---|---:|---:|---:|---:|
|1 订单草稿与政策咨询|通过|3|10021/365|22555|0.006839720|
|2 耳机数量及不提前预览|通过|2|12081/331|14461|0.005125960|
|3 商品插问且保留revision|通过|2|14960/175|14319|0.003366880|
|4 更正充电宝并预览|通过|3|28550/427|19254|0.008556880|
|5 政策插问且保留revision|通过|1|11450/213|8843|0.002643120|
|6 更正回耳机并生成最新预览|通过|3|39595/431|15060|0.009331800|

最终草稿revision=4，item=4，quantity=1。申请 `R488aea9dac8a493cb3fc34b26643b6bf` 为 `pending_review`；正式申请、确认、created审计、提交幂等回执各1，审核0。应用重复点击没有新增任何记录。[SQL终态](sql_final.json)、[完整M6权威回执与Graph对账](reconciliation.json)证明业务成功；Graph镜像matched、无待执行聊天节点，不能单凭模型回答认定提交成功。

共 **14次HTTP**，Agent 14 / User Simulator 0 / Judge 0；输入116657、输出1942、缓存命中90368 Token，模型请求延迟合计25122ms。保守累计预留 **1.907844元**，按已核实usage及实际时段价格估算 **0.035864360元**（峰值口径0.07172872元）。供应商实际账单未获取，不能称已扣款金额。全部usage已知，未知请求0，未触及3元/24次限制，无自动重试。

逐请求见 [requests.csv](requests.csv)、[公共账本](ledger.public.jsonl)、[费用汇总](cost_summary.json)、[分阶段口径](stage_costs.json)。分阶段归属采用同机预留时间与阶段SQL快照时间，完整账本是费用权威记录。模型为deepseek-flash、temperature=0、non-thinking、max output=1024、SDK重试0，复用原Episode预算和SDK取证。价格核验在第一次HTTP前完成，官方价格页快照/原始账本仅保存在私有目录。

## 回答质量限制

阶段4虚构了不存在的“之前耳机预览失效”；阶段4/6说实验不提供提交，与应用确认可创建模拟申请的能力不符。另有过期时钟未注明时区。结构化SQL与实际权限没有因此改变。见 [人工结构化核验](human_review.json)。本次结果保留，不重跑、不改任务、不覆盖scorecard。

后续最小修复仅让Formal Runtime的预览工具描述、当前事实和新回执准确区分模型权限与应用能力，保留草稿Fixture限制；历史回执恢复逐字保持。该修复由离线专项和完整CI验证，**没有再次真实模型验证**，不能把本运行算作修复后的模型成绩。虚构旧预览的问题仍留待独立语义评测。

## 证据与重现

桌面：[提交结果](submitted-desktop.png)；窄屏：[移动布局](submitted-mobile.png)。本轮截图已人工查看。真实浏览器与固定脚本结果分开保存，固定脚本不是模型质量成绩。

```bash
# 默认不会调用付费模型；新目录运行固定浏览器验收：
python scripts/eval_web_demo.py --out /tmp/v3-web-new-browser-run --port 8768
# 真实运行需显式 --deepseek --env-file 与事先核实的 --price-review。
# 本轮已完成并停止，不自动重跑付费。
```

完整SDK请求响应、浏览器Cookie/CSRF、Graph Checkpoint、逐阶段SQL和Trace仅保存在本目录 `.private-evidence/`，目录受限且Git忽略；公开只保留合成业务SQL、白名单事件及脱敏摘要。

**结论：隔离浏览器业务闭环Go；完整回答质量、生产使用及正式Benchmark No-Go。**
