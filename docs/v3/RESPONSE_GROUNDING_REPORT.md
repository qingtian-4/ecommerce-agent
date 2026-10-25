# M7.1 业务回答证据合同

旧真实Badcase来源：`results/answer_stream_deepseek_20261024_run1/semantic_review.json`。模型说“此前耳机版本的预览已失效”，但前五阶段预览为空，最终SQL总共只有一个新预览。本轮先在冻结脚本输出中复现，未先付费重跑。

## 展示边界

新增`acs/answer_grounding.py`，不重写Agent循环、ReturnService或提交事务。Web只在原生Turn完成并核对工具回执后，以Pydantic Evidence/Review建立派生展示：

- SQLite的草稿、按实际创建顺序的预览、本轮preview_return回执与ReturnService.latest_preview提供事实；本轮新预览即使过期/提交也不能变成“旧预览”。
- 完整提交成功继续复用M6 read_submission_receipt；没有权威成功回执不展示当前申请成功。模型不能承诺提交、审核、退款或换货权限。
- 支持明确中文业务声明、草稿/预览结构字段、订单/数量绑定与明确预览期限。只在可确定矛盾的局部短语或字段降级/纠正；商品解释、政策咨询及其余正文保留。
- 否定、假设、疑问、引用及报告他人说法不是操作完成声明。字段范围在结构块结束时关闭，不能污染后面的其他合法订单。
- 期限从SQL expires_at转为北京时间UTC+08:00；历史预览的“失效”与时间“过期”分别判定。

原始AIMessage、ToolMessage、Graph与SessionStore.answer/messages_json均保持原文。Web的answer_presentations只是按session/turn/raw SHA绑定的展示缓存，不是第二份业务状态或成功凭证。刷新只取已冻结的投影，不按新状态重写旧回答；哈希不一致拒绝展示。

原生started事件拿到turn_id后立即写入NULL待核验占位，早于模型/工具执行。进程即使在原文finish_turn提交后退出，也不能把新原文按legacy直接发布。原文与证据在0700目录/0600文件私有留存，成功保存后才更新派生展示并持久answer_checked摘要。证据保存失败则保持待核验，不放行原始成功话术。原生恢复继续使用原Checkpoint，不把展示检查当业务写入，也不重放已成功事务。

## 能保证与不能保证

这不是任意自然语言语义验证器。coverage始终标记scoped_rules_only，不能把“无规则命中”叫作全回答事实通过。未知释义、跨会话历史、商品规格的任意改写和开放域知识仍需要人工复核/独立Evaluation。原始模型也可能继续输出错误；本轮验证的是已定义高风险声明的展示合同，原文与纠正率应分别报告。

展示层没有新增业务授权。UNKNOWN仅可查询，由当前应用请求重新收紧原生Tool集合；历史checkpoint中的未回执写请求在只读恢复时明确拒绝，不进入ToolNode重放。默认客服Runtime与官方τ工具/Reward不改变。

## 证据

`tests/test_answer_grounding.py`包含冻结错误、历史存在/不存在、过期原因、否定/假设/引用、能力承诺、混合咨询与跨段订单反例。`tests/test_web_grounding.py`使用真实FastAPI/Graph/SQLite验证原文保留、投影恢复、SQL唯一申请、证据失败、篡改、UNKNOWN服务端强制只读和待写Checkpoint恢复。旧M6专项和完整回归继续执行。

审查发现并修复：历史判定不能依赖当前valid_preview；本轮未审查原文不能落入legacy回退；同用户合法订单也必须符合当前草稿绑定；字段检查不能跨段。失败记录保留，不修改任务或放宽业务断言。
