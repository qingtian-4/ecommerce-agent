# 电商客服 Agent 执行完整性代码审查

日期：2026-10-23。基线29c603f；审查独立分支fix/order-action-completeness的完整工作差异。
按仓库code-review技能进行独立Standards/Spec双轴只读审查，审查意见已反馈并复验。
[范围与结果](ACTION_COMPLETENESS_REPORT.md)；没有依靠报告代替源码检查。

## Standards

| 发现 | 程度 | 处理与证据 |
| --- | --- | --- |
| 评分只检测item/quantity/reason，漏掉condition等额外字段 | P1，违反AGENTS“判据必须能识别反例” | grade比较全部七个非订单可写字段；七组校准反例及来源不符反例通过 |
| orders-02场景输入未纳入原冻结哈希 | P2 | 冻结scenario.sql并写入freeze_v2，四臂逐例业务快照一致。原生成器日期本来来自catalog，无动态日期缺陷 |
| 错订单写入未计入未授权率 | P2 | 新增先红后绿校准，grade将实际订单与冻结goal_order比较；176条原始观测重新计算分数变化0，不重写raw |
| 以第3次读取注入读回失败对实现调用顺序敏感 | 非阻塞，判断性 | 固定条件及当前调用序列下，candidate-on/readback-failed确实为state_read_failed，持久任务存在且界面未知；这是专项fixture，不是生产机制 |

生产修改限定于一个现有模块，没有新表、新依赖、事务改造、重复Runtime或无依据重构。
评测驱动较长，但用于固定任务、异常注入、独立数据库和指标取证；本轮不拆成新框架。

## Spec

| 发现 | 程度 | 处理与证据 |
| --- | --- | --- |
| D05首轮保存被误计为整个多轮取消目标成功 | P1 | v1保留，v2新增final goal_status；action_complete与strict_complete分离，D05最终partial |
| 否定退货任务仍要求政策咨询，却未进入咨询分母 | P1 | v2按用户原话预置咨询清单，分母34；保留原脚本决定，不为了提高分数改答案 |
| 仅字符串同步不足以识别draft假提交/退款 | P1 | 固定提交、退款、无草稿假保存、来源/字段校准；如实标记有限声明检测，不称通用事实验证 |
| DOM仅采样保存未断言 | P2 | 最终driver断言采样中无预置错误完成语句，另有on_send全yield测试；50ms DOM采样不等于穷尽实际渲染帧 |

剩余局限：D05取消未执行，D06开启P0后政策丢失，复杂授权不支持；未将它们标为已修复。
state_consistent=null代表未知，报告明确43例可核实一致+1未知，不把未知算观察到的业务失败。

## 生产安全边界复核

- 授权来自当前用户原话整句有限协议；最终模型答复、历史提及、引用/否定不授权。
- 成功同轮get_order回执与订单/用户绑定；矛盾错误回执拒绝；数据层再次验证订单归属。
- only_if_absent仍在BEGIN IMMEDIATE内核验owner/current turn/budget；已有草稿、不同订单、取消/提交终态均不覆盖。
- 仅order_id，字段来源保留原话/turn/user_claim，重复进入和恢复不加revision；不自动选商品、不提交。
- ChatService原有补存后读回、TurnUpdate/assistant/final Trace同步不变；未知写不重放，读取失败显示未知。
- 返回页面的任务卡、执行事件和业务状态通过处理器及实际Gradio流程分别验证；未实现撤回已展示文字。

结论：已发现的Standards/Spec问题均已在本轮证据及评分实现中处理；没有发现需要扩大生产改动的阻塞项。
默认开启及宣称整题全部修复仍No-Go。最终CI结果以本轮推送SHA为准，不沿用29c603f的绿灯。

双轴汇总：Standards三项实质问题已处理、一项非阻塞判断性建议；Spec四项已处理。两轴原最高严重度均P1，当前没有未解决生产代码阻塞。
