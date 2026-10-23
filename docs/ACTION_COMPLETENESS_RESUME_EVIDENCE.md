# 电商客服 Agent 执行完整性简历证据

日期：2026-10-23。[完整报告](ACTION_COMPLETENESS_REPORT.md) ·
[冻结契约v2](../results/action_completeness_20261023/GRADING_CONTRACT_V2.md)。

可以写：

> 针对真实客服轨迹中已核对订单却遗漏草稿写入的问题，在自定义Agent Runtime中扩展受限后置状态检查，
> 复用SQLite条件事务、字段来源与权威读回；44例固定决策专项对照中，旧P0开启到候选开启的必要动作
> 完成由6/13至13/13，严格整题4/27至10/27，未观察到未授权/重复写入，未新增模型调用。

讲解时必须补充：案例已见、离线脚本模型、同一冻结SQL/KB及执行决定；不代表自然模型总体成功率。
开关关闭完全复现原行为；P0关闭对照→候选开启仍有D06咨询保留回归，咨询23/34→22/34。
原P0开启→候选开启咨询22/34保持，后续输入平均多196.09字符；不是Token或成本降低。

不能写：真实模型100%成功、整体业务100%完成、通用自然语言授权、生产级可靠性、零回归、
降低真实Token/费用/延迟、已补齐正式120段Benchmark、V2/V2.1普遍优于官方。
此前684段、119/120正式、11题真实6/11→11/11及8题真实配对仍分别引用原报告，不合并本轮离线分母。

面试最值得解释：为什么“安全拒绝”不是任务成功；为什么只保存订单而不能替用户选商品；
为什么未知写不能重试；为什么D05保存成功但取消失败仍整题失败；为什么评分器修正不能算Agent能力提升。

页面流程（本轮脚本模型、实际Gradio与SQLite，非真实LLM）：

```bash
# 独立、全新目录；客户就绪后才启动管理员，避免并行初始化
uv run python scripts/check_action_completeness_ui.py --serve \
  --scenario /tmp/action-ui-fresh --port 7940
uv run python scripts/check_action_completeness_ui.py --serve --admin \
  --scenario /tmp/action-ui-fresh --port 7941
# 安装Playwright的独立验收环境中运行，不作为应用运行依赖
python scripts/check_action_completeness_ui.py --url http://127.0.0.1:7940 \
  --admin-url http://127.0.0.1:7941 --out /tmp/action-browser-fresh
```

数据库不可复用已提交过相同商品的演示库，否则预览可能按剩余可退数量正确拒绝；不得放宽规则。
演示顺序为混合退货+期限→仅订单草稿→刷新恢复→明确更正到S20002/明细3/1件→否定取消→预览→
页面确认→认证管理员批准→用户查询；实际退款不实现。
本轮截图/采样与后台回执见[验收证据](../results/action_completeness_20261023/browser-accepted/summary.json)
和[后台记录](../results/action_completeness_20261023/ui_backend_receipts.json)。

下一步只建议先补D06咨询保留和D05取消确认的独立任务契约与红测，不继续扩关键词或新建Memory框架。
如进行自然模型验证，须冻结未见任务、提供费用估算并取得新的预算授权。
