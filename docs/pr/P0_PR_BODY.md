用户明确说“我想退 S20017”，同轮订单查询成功，模型却连续追问明细而没有保存草稿。
本改动在 Loop 正常结束后做受限完整性检查，仅补存不存在的本人订单草稿，不选商品、不创建申请。

- 当前用户原话与同轮有效工具回执才是授权来源；无证据、否定、已有任务或预算过期不补存。
- `only_if_absent`在 SQLite 事务中判定新增、已满足、冲突或失败，不覆盖现有草稿、不重复增加 revision。
- 补存后读回真实状态，同步 ChatService、assistant message、final Trace、任务卡及恢复历史。
- `state_check`独立轨迹，不冒充模型调用；预览、明确确认、权限、幂等和管理员审核边界保留。
- 开关`ACS_RETURN_STATE_CHECK_ENABLED`默认 false。

验证：P0 阶段 458 项离线测试、8 组配对探针、异常/用户切换/恢复与真实 Gradio 页面多轮操作；
脚本模型不当作真实模型能力评价。写入加读回预热 p95 约 240.26ms，不能承诺 20ms，
未以放宽事务或权限换耗时。

证据：[P0 报告](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/results/p0_order_draft_integrity/REPORT.md)、
[显式启用页面验收](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/results/p0_order_draft_integrity/gradio_acceptance_20261020_verified/REPORT.md)。
后续依赖 PR 还修复了继承的取消授权、完整性协议和政策日期等问题；请结合后续 PR 审阅，
不要因本 PR 的窄入口验收就默认开启全部实验保护。

合并建议：保留提交祖先关系的 merge commit。后续`feat/order-candidate-facts`基于本分支，
合并后将后续 PR 基线改为 main；不需要重写历史或重复 cherry-pick。
