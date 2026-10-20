# 电商客服 Agent P0 状态完整性检查结果

日期：2026-10-20。范围仅为架构提案的 P0 方案 A。
基线 `3a4de7c2d9e1da5dd74335b8e5a72d6c1daead21`，功能分支
`fix/order-draft-integrity`。本轮未提交、推送、合并或发布，未实施 P1/P2。

**功能修复已通过离线对照；延迟目标尚未达到，保持默认关闭。**
没有付费 API、真实模型评测、完整 τ Benchmark 或后训练。
所有业务数据均来自既有合成场景，实验使用独立临时数据库；原始历史结果保持不变。

## 1. 根因与修复

`orders-02 / trial=1` 首轮原话为 `我想退S20017。`，`get_order` 已返回本人订单，
模型却只追问明细，四轮都未更新草稿。Loop 将无工具回复标记为 `answered`，
ChatService 随即 `finish_turn`，没有独立检查草稿是否落库。
SessionStore 本身已经支持只有订单、商品明细未知的草稿，缺失的是运行时结束检查。

冻结轨迹为 [原始文件](../iteration_v2_recovery/local_after/raw.jsonl) 第 21 行，SHA-256：

```text
151d1419169076d50b95cdeed30c9533c06f69f03740953d7f50a372d902e9aa
```

实际改动：

| 落点 | 改动与约束 |
| --- | --- |
| [return_state_check.py](../../src/acs/return_state_check.py) | 只完整匹配当前原话的有限退货表达；核对同轮真实调用参数、订单结果、用户与订单标识 |
| [sessions.py](../../src/acs/sessions.py) | 内部 `only_if_absent` 在既有 `BEGIN IMMEDIATE` 中检查；只允许订单单字段，不覆盖任何既有任务 |
| [chat_service.py](../../src/acs/chat_service.py) | 终轮、finish_turn 前检查；补存后独立重读实际状态，更新 TurnUpdate 和最终三处文本 |
| [app.py](../../app.py) | 独立服务端状态检查标题和样式；读取失败显示状态未知，不能显示陈旧的空草稿或成功 |
| [service_agent.py](../../src/acs/service_agent.py) | 仅增加检查异常的摘要展示，不改 Loop 的推理与工具执行算法 |
| [config.py](../../src/acs/config.py)、[.env.example](../../.env.example) | 独立开关，默认 false，不改用户 .env |
| [正式测试](../../tests/test_return_state_check.py) | 原始遗漏回放、正反例、事务并发、异常、恢复、预览与 UI 集成 |
| [离线探针](../../scripts/probe_order_draft_integrity.py) | 固定假模型，走真实 on_send、ChatService、Loop、工具和 SQLite，生成配对结果与 UI HTML |

正常 answered 终轮才允许补写；中断或错误终轮仅重读状态并说明未完成，中途关闭生成器
不会补写。仅当当前没有任何草稿记录、当前原话明确办理、同轮查询本人订单成功时，
通过既有存储层补 `order_id`。不补商品、数量、原因、资格、凭证、预览或确认。
模型最终回答、历史查询和候选列表均不能授权补存。

事务结果区分 `created/satisfied/conflict`，检查轨迹对应 `repaired/satisfied/conflict`；
验证或存储异常记录 error。获取写锁后和 INSERT 前都检查原运行预算，不重置 deadline。
权限、字段验证、原话来源与 revision 沿用现有方法，普通模型工具调用的默认语义不变。

草稿已经保存但响应丢失时，重新读取实际任务后再说明状态；读取也失败则明确无法核实。
不自动重试写入。检查异常使用 `state_check_error`，预算过期使用 `budget_exceeded`，
不会显示为正常完成。已存在、不同订单、cancelled、submitted 或仅缺订单的已有草稿
都不自动覆盖或复活。

最终 `AgentResult.answer`、最后 assistant message、已有 final trace 同步后才保存轮次。
更正明确标注以重新读取的后台记录为准。它可以纠正终态，不能撤回先前已经流出的文字，
也不保证任意自然语言事实正确。

ReturnService、提交/审核权限、数据库 schema、模型提示词、公开工具与 RAG 均未更改。
没有新增 Fact Ledger、审查模型或任务平台。

## 2. 红测试与配对结果

已有测试先运行：398 passed、1 skipped。随后把提案回放迁入正式测试，生产代码未改时
得到 **1 failed、3 passed**；失败点就是开启检查后期望草稿存在，但实际仍为 None。
开关关闭的用例通过，明确保留并证明原始遗漏。
最小实现后的同四项测试得到 **4 passed**，之后扩展边界与集成测试。
日志：[基线](baseline_pytest.log)、[红测试](red_pytest.log)、[首次绿测试](initial_green_pytest.log)。

固定 8 组、开关两侧共 16 段对话，每组使用相同输入、模型决策及业务快照。
没有修改任务判据、挑选重试或对真实模型进行重复采样。

| 探针 | 关闭 | 开启 |
| --- | --- | --- |
| orders-02 原始四轮 | 无草稿 | S20017、item=null、revision=1 |
| 纯查询、否定、假设、转述，4 组 | 均无新草稿 | 均无新草稿 |
| 查询了另一订单、他人订单，2 组 | 均无新草稿 | blocked，均无新草稿 |
| 模型已正常保存 | revision=1 | satisfied，revision 仍为 1 |

本批合格遗漏只有 1 个：修复数 0/1 → 1/1；6 组负例误补存 0。
不存在任务覆盖、额外 revision、新申请或新审计。运行前后的订单等业务表指纹、
既有申请/确认/审计的逐行记录相等。场景原本有种子申请，报告的是增量而非总数零。

两侧每组假模型请求数相同；原始四轮均为 5 次。state_check 是独立服务端事件，
不增加模型工具计数。真实网络尝试、付费模型和 embedding 请求均为 0。
假模型的 Token usage 为占位数据，不作为真实 Token 消耗或节省证据。

原始四轮后三轮的模型输入分别从 10,291/10,480/10,641 字符变为
11,498/11,687/11,848 字符，每次增加 1,207 字符，包含恢复的草稿上下文和状态说明。
因此零额外调用不等于真实后续 Token 为零，也不能宣称节约 Token。

证据：[summary.json](paired_verified/summary.json)、[逐轮原始记录](paired_verified/raw.jsonl)。
这些是固定模型决策下的运行时开发/回归探针，不是盲测、模型整体正确率，
也不能把历史 140/160 当作本轮已提升成功率。

## 3. 安全、恢复与 UI 验证

正式测试覆盖以下边界，完整结果见 [针对性日志](final_targeted_pytest.log)：

- 不同用户、不同订单、缺少当前查询、只有历史查询、只有候选列表、回执标识不一致。
- 单独提及、否定、引用、假设、多对象与“先别保存”不授权补存。
- 正常模型写入不补第二次；重复检查、六线程同订单与不同订单竞争最多新增一次，
  revision 保持 1，原子条件写入不覆盖其他获胜任务。
- 不同订单、空订单的已有草稿、取消态和提交态均不被覆盖或复活；正常改草稿的旧规则保留。
- 补存前 SQL 异常回滚；提交成功但回执丢失后读回；读回失败显示未知；读回不满足
  后置条件则不能报告 repaired。
- 预算在最终回复后过期、在事务获取后过期、在 INSERT 前过期均不能补写。
- 流式中途关闭、模型断流、补存后 finish_turn 响应丢失，重启与重试不重复办理。
- 同订单 no-op 不增加 revision，已有预览仍有效且可在明确确认后提交；旧确认、超量、
  缺材料、越权和非法审核继续由原测试验证。

UI 定向测试走实际 `on_send`、`on_restore`，核对任务卡片、聊天历史、持久化 assistant
消息、final trace 和独立 state_check 一致，模型工具计数仍为 1。
将真实处理器生成的 HTML 用 Chromium 离线渲染，DOM 检查为 1 个 state-check、
两侧各 1 个最终回答，开启侧任务卡显示 S20017；完整截图已人工查看。

证据：[DOM 断言](ui_verification.json)、[完整截图](ui_full.png)、
[渲染 HTML](paired_verified/ui.html)、[浏览器 DOM](ui_dom.html)。
这验证处理器与渲染产物，不等于重新跑了线上模型、完整 Gradio 浏览器点击或容器验收。
浏览器启动的 D-Bus/GPU 告警保留在 browser.log 和 browser_full.log，DOM 检查成功。

同类边界检查还覆盖了三个 finish_turn 使用面：ChatService 的模型终轮已加检查；
app 的本地售后操作和 task_eval 的可信操作直接调用真实后台并验证回执，不需要
用自然语言重新推断意图。旧 eval_runner 直接调用 Agent，不提供本机制；τ 适配也不
走本店 ChatService，本轮不改它们，不能声称这些路径已经获得草稿检查。

## 4. 延迟、限制与保留的失败

本批 4 次触发检查：补存加读回 **404.82 ms**；另外 3 次为 blocked/blocked/satisfied，
分别约 0.59/0.57/0.91 ms。按这 4 项计算 p50 约 0.75 ms，最近秩 p95 为 404.82 ms，
**未达到提案的 p95 ≤ 20 ms 候选目标**。样本过少且与 pytest 并发，不是隔离性能实验。
尚未拆分锁等待、磁盘同步与读回耗时，不能直接归因于某一项，也没有证明端到端提速。
不调整 SQLite 事务或持久化安全参数来消除这项失败；默认仍关闭。

受限入口只承诺完整的 `我想退/我要退 + 订单号` 表达。不识别更广表达、不恢复已取消
任务、不处理模型主动写错字段、不保证引用蕴含、也不处理 P1/P2 的全量语义问题。
后续若扩大覆盖，必须增加独立正反例及证据，不能通过放宽匹配获得表面修复率。

首次配对探针误把已有种子申请当成新增副作用而失败，日志
[paired_probe.log](paired_probe.log) 保留。修正为逐行前后对照后使用新目录
paired_verified 完成，没有删除种子、改 Agent、放宽业务条件或挑选成功模型尝试。

## 5. 工程检查、复现与回退

最终针对性测试 **179 passed**；完整测试 **453 passed、1 skipped**，包含新增 55 项
P0 探针；Ruff 和差异空白检查通过。跳过项为未安装 tau2 的可选适配测试；
4 项既有 Gradio/Pandas 弃用告警保留。没有重新进行外部 τ 验收。
工程检查结果与候选源码/结果指纹汇总于 [verification.json](verification.json)。日志分别为：

- [最终针对性 pytest](final_targeted_pytest.log)，含禁止网络的 ChatService/SQLite 与 UI 测试。
- [完整 pytest](final_pytest.log)，可选 τ 适配依赖缺失按原规则跳过，不冒充外部验收。
- [ruff check](final_ruff.log) 和 git diff --check。

复现命令在仓库根目录执行，使用已安装的开发依赖，不需要 API key：

```bash
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest tests/test_return_state_check.py
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest
.venv/bin/ruff check .
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python scripts/probe_order_draft_integrity.py --out /tmp/选择一个尚不存在的目录
```

探针拒绝覆盖输出目录，显式禁止 socket 连接，用 ScriptedLLM 走真实业务链路。
不要用关闭 judge 的真实多轮评测来代替无网络测试，它仍会调用模型。

手动启用：在本地环境设置 `ACS_RETURN_STATE_CHECK_ENABLED=true` 后重启应用。
回退：设为 false 并重启，恢复原执行路径，不删除已经合法保存的草稿。
不需要迁移数据库或重建索引。代码保持一个完整改动，未自动提交；后续若提交，应将
实现、测试与必要文档作为同一个 Conventional Commit，不混入用户原有 TODO/skill 修改。

下一步先由用户审阅本轮结果、窄入口与延迟未达目标的问题，再决定是否继续。
未经后续明确授权，不做付费验证、不实施 P1/P2，不推送、合并或发布。
