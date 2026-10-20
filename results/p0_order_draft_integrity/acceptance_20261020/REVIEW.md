# 电商客服 Agent P0 工程审查

比较基线和 HEAD 均为 `3a4de7c2d9e1da5dd74335b8e5a72d6c1daead21`，实际审查对象为功能分支的完整未提交 P0 改动，包含未跟踪源码、测试与脚本。审查前清单、源码指纹及原差异在 [input_manifest.json](input_manifest.json)、[review_input.diff](review_input.diff)。`docs/TODOS.md`、`skills-lock.json` 归用户既有修改，排除在本交付外。没有待审提交或远程操作。

## Standards

原审查发现 1 项中严重度问题：订单补存提交成功后，`finish_turn` 抛异常，没有最终 TurnUpdate；原 `app.on_send` 异常处理复用先前空草稿卡片，与实际数据库不一致。修复为先按当前用户重新读任务，再恢复相符的持久化轮次；读取失败纯渲染未知状态。历史长度及用户原话检查防止重复问题的旧答案被当成本轮结果。位置：[app.py](../../../app.py) 的 on_send 通用异常分支；回归：[test_return_state_check.py](../../../tests/test_return_state_check.py) 的 `test_ui_finish_failure_rereads_committed_draft_or_shows_unknown` 和重复问题测试。

原有异常文案测试逐字匹配“系统出现异常”，与本轮恢复 SQLite 权威历史的行为不符；改为更强的 UI=持久化历史、interrupted 及明确未完成断言。partial、实际异常、错误面板与失败状态原断言保留。完整回归还发现恢复的中断历史漏掉既有“没有人工接入”能力说明，已在 ChatService 中断持久化文字补回，原有能力边界测试保持不变。

原问题已修复；增量静态复核未发现仍待处理的规范阻塞项。微基准计时仅在独立脚本中，不进入生产逻辑。

## Spec

原审查发现 1 项低严重度问题：已有 reason-only 草稿被正确拒绝覆盖，但提示“订单 None”。补充未绑定订单的明确提示，不改变 conflict 判据或写入行为。位置：[return_state_check.py](../../../src/acs/return_state_check.py) 的 state_notice；回归：`test_unbound_existing_draft_notice_does_not_print_none`。

原问题已修复；增量静态复核未发现缺失或错误实现的阻塞项。没有新增 Fact Ledger、模型调用、业务 API、数据库抽象、商品选择或申请创建。

## 安全与架构复核

- 触发只用当前原话 fullmatch，不能从模型回答、历史订单、候选列表提取授权。`_order_read` 配对同轮实际调用和成功结果，核对调用订单、返回订单和当前用户；结果来自真实工具执行轨迹。
- `SessionStore.update_return_task` 在 `BEGIN IMMEDIATE` 之后校验用户、当前 running 轮次，再判断任何任务是否存在；存在则只返回 satisfied/conflict，不覆盖或增加 revision。不存在才走既有字段与本人订单校验并插入，连接退出时提交；异常回滚或由读回确认不确定的提交。
- 预算在获取写锁后和 INSERT 前检查。SQLite 获取锁或同步提交本身不可被 Python deadline 抢占，不能承诺严格毫秒级停止；已经提交的状态只能如实读回，不能报作没有发生。
- ChatService 检查结束后单独读回，再同步 answer、最后 assistant、final Trace；出错或未知时不显示正常完成。UI 正常轮次有独立 state-check，模型工具计数不增加。
- ReturnService、权限、确认、预览、幂等、审核与 schema 未改。普通模型工具更新默认行为保持原样。根模块边界足够，无证据支持进一步抽象或重构。

## 保留的限制

中断恢复沿用既有 `interrupt_turn` 的精简记录，不持久化完整执行 Trace；异常页面显示实际错误，不伪造已丢失的 state_check 事件。恢复后的草稿与消息可以核对，不能声称恢复了全部轨迹。数据库暂不可读时只显示未知，之后须重新载入；已流式展示的文字不能撤回，只能在终态显式更正。窄入口不识别任意同义句，不处理模型主动保存错误订单或多商品回答的事实串用。

两轴统计：Standards 原 1 项中严重度、Spec 原 1 项低严重度，均已修复；安全专项未发现有证据的新增高危问题，架构专项建议保留现有 P0 路线。专项复核为静态审查，运行测试与性能验收以主报告日志为准。
