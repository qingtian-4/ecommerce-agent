# 电商客服 Agent 执行完整性专项验收

日期：2026-10-23。基线 **29c603f**，独立分支 `fix/order-action-completeness`。
本轮没有付费请求、τ重跑、PR #4更新、main合并或默认开关变更。
[四臂汇总](../results/action_completeness_20261023/comparison.json) ·
[CSV](../results/action_completeness_20261023/ACTION_COMPLETENESS_SCORECARD.csv) ·
[JSON](../results/action_completeness_20261023/ACTION_COMPLETENESS_SCORECARD.json) ·
[审查](CODE_REVIEW.md) · [简历证据](ACTION_COMPLETENESS_RESUME_EVIDENCE.md)。

## 验收结论

受限订单草稿补存有可复核净收益；保持实验开关默认关闭。**不是所有多轮用户目标都已修复。**
固定决策下，旧P0开启→候选开启，必要动作 **6/13→13/13**，严格整题 **4/27→10/27**，
咨询均 **22/34**；未授权和重复写入均0/44，没有新增模型或工具调用。
P0关闭对照→候选开启仍有D06咨询回归，不能宣称无回归或默认启用安全。
本轮结果仅为已见失败的离线执行完整性证据，不代表自然模型成功率或生产可靠性。

## 基线与根因

远端main=f34981f；PR #4仍为旧V2 f6c3387、OPEN/draft；集成基线29c603f的
[CI成功](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39819291387)。
主工作树的TODO等三项用户修改未动，新工作树从集成提交建立。
[保护记录](../results/action_completeness_20261023/preservation.json)保存原结果树及用户文件哈希。

D01/D02真实轨迹已经读取本人O10024，却没有update_return_task；当时P0关闭。
旧P0开启也只完整匹配“我想退/我要退+订单”，所以“记录订单草稿，同时介绍产品参数”及
“我想退订单，同时说明期限”仍遗漏。D05/D06首轮单句则在旧P0开启后已能保存。
历史orders-02属于同一类模型遗漏，但原P0已有修复，本轮保留其回归，不重新宣称首次解决。
原轨迹、来源哈希和执行决定见[冻结清单](../results/action_completeness_20261023/freeze.json)、
[原D轨迹](../results/v21_integration_20261022/paired_live/)及[P0历史报告](../results/p0_order_draft_integrity/REPORT.md)。
这不是已证明的Memory缺失；SessionStore原本支持仅有订单、商品未知的草稿。

## 最小修改和授权范围

生产变更只有[return_state_check.py](../src/acs/return_state_check.py)：

- `_authorized_order`完整匹配当前原话中的“我想退X”“我要退X”“记录X的退货草稿”。
  可跟随最多两段逗号分隔的“同时说明退货政策/期限/运费”或“同时介绍产品ID的参数”。
  不剥离否定、引用、假设、暂停或其他动作；不支持的组合直接退出，不扩充无限同义词。
- `_order_read`继续配对同轮真实get_order调用/回执、订单和应用用户；新增拒绝同时携带
  error/error_type的矛盾成功回执。模型最终回答、旧轮读取和候选列表均不构成授权。
- 事件版本升为2；复用原ChatService结束核对与读回、SessionStore事务内only_if_absent，
  不改变任何模型Prompt、Router、ReturnService或UI生产逻辑。

仅允许order_id。商品、数量、原因等一律不自动选择；不覆盖任何已有任务，不复活cancelled/submitted，
不创建申请、确认、审核或退款。事务BEGIN IMMEDIATE、owner/running turn/budget、revision及sources
保持原语义。关闭 `ACS_RETURN_STATE_CHECK_ENABLED` 即退出补存，原有权限/确认仍保留。

## 评分契约与校准

[v1](../results/action_completeness_20261023/GRADING_CONTRACT.md)先于实现冻结；审查发现
D05取消目标及否定任务中的咨询漏计、字段/事实校准不足，故保留v1及全部中断结果，另冻结
[v2](../results/action_completeness_20261023/GRADING_CONTRACT_V2.md)、
[cases_v2](../results/action_completeness_20261023/cases_v2.json)和
[补充哈希](../results/action_completeness_20261023/freeze_v2.json)。
**评分修正不是Agent改进**，四臂最终统一v2，不使用v1的D05虚高分。

44例：27例含业务目标，17例授权反例；正常允许补存/已满足任务13例；咨询事实清单34例。
D案例保留原话、有序工具决定和最终模型文字，按每轮一个工具批次加最终答复回放，不是逐Token或完整API时序重放。
专项orders-02是首轮同构病例，原始四轮轨迹继续由既有test_return_state_check.py测试覆盖。
目标独立于工具实际执行，来自原始用户诉求及冻结SQL/KB。首轮保存与后轮取消分别检查，
拒绝、部分完成、未判定、异常退出与业务成功分开。
所有七个非订单可写字段、来源、错订单、无草稿假保存、draft假提交和假退款均有校准反例。
最后一次错订单评分器校准后，[176行重算](../results/action_completeness_20261023/grader_recalibration.json)
确认实际分数变化0，原raw未修改。有限文本声明检测不等于通用语义裁判。

## 固定决策对照

| 指标 | 基线P0关闭 | 旧P0开启 | 候选关闭 | 候选开启 |
| --- | ---: | ---: | ---: | ---: |
| 必要动作完成 | 2/13 | 6/13 | 2/13 | 13/13 |
| 漏执行 | 11/13 | 7/13 | 11/13 | 0/13 |
| 严格整题完成 | 2/27 | 4/27 | 2/27 | 10/27 |
| 咨询全部完成 | 23/34 | 22/34 | 23/34 | 22/34 |
| 未授权写入 | 0/44 | 0/44 | 0/44 | 0/44 |
| 重复写入 | 0/44 | 0/44 | 0/44 | 0/44 |
| 模型决定/工具调用 | 92/72 | 92/72 | 92/72 | 92/72 |
| 后续输入平均字符 | 7708.55 | 7809.77 | 同基线P0关闭 | 8005.86 |

没有把异常/拒绝删出严格整题分母。补存后响应丢失虽有订单状态，但异常轮不计整题成功；
读回失败1例单列未判定。候选状态结果为**43例可核实一致、1例未知**，不是1例已知不一致；
其余臂的已知/未知细项可从raw的state_consistent=null区分。
重复专门子集same-order/model-saved/repeat-restored均无额外revision，终态/异订单不变；申请、确认、审计及业务订单明细逐行保持。

D01/D02保存修复；D05/D06首轮保存均成立，但D05混合取消被后端拒绝，D06第二轮
取消保护摘要未保留没有新工具证据的政策说明，仍是整题失败。
D06已在旧P0开启组复现：相比旧开启组，候选未新增咨询回归；相比P0关闭对照组则保留这一回归。
咨询原正确项保留为22/22（旧P0开启）及22/23（P0关闭对照），不能合并口径。
其余17个整题未完成ID及三种终止类别保存在每臂summary，不挑选成功重试。

## 调用、上下文与延迟

四臂使用同一初始SQL快照、静态订单/KB事实、有序脚本工具决定和120秒/16请求预算，除P0之外开关相同；
任务/取消工具回执按真实当前状态产生，补存后的差异正是测量对象，不伪造为相同回执。
写回执保护显式开启，Router/Recovery关闭，不引入路由混杂；表中的P0关闭臂不是全部八个开关关闭的产品默认配置。
原始默认单句关闭行为和旧P0单句开启行为另由既有orders-02四轮测试覆盖；混合入口的红测使用默认其余开关。
每臂另有44次统一的下一轮上下文探针，单列，不计入92次任务决定；实际API/Token/费用分别0/null/0。
fake usage不是供应商Token，不能据此发布成本下降。
关闭消融在回答、状态判据、调用和字符数上完全相同，四臂初始业务快照逐例哈希一致。

旧P0开启→候选开启，后续输入平均增加196.09字符；P0关闭对照→候选开启增加297.32字符。
已经发出的本轮模型请求不会被后置补存追改；后续轮（包括同一多轮任务的下一问）输入会随状态改变。
变化包含任务状态和明确纠正回答，不能全解释为新系统Prompt。
候选Episode离线耗时p50/p95/max=966.97/1637.11/2499.43ms，旧P0开启为1029.90/2007.58/2494.94ms，n各44。
运行时有并发本机负载，这些数字不能证明提速；SQLite持久写代价及历史冷/热分解仍见P0报告。

## 验证与全部失败保留

- 原P0与新测试先红：6项失败；矛盾成功回执另2项失败，修复后绿；错误订单评分器先红后绿。
| 最终检查 | 实际结果与证据 |
| --- | --- |
| P0/专项定向 | 89 passed；补充错订单校准和正确短答fixture各1 passed，见targeted-final.log及两个green日志 |
| 串行完整pytest | **943 passed、4 skipped、4 warnings，924.29秒**；full-pytest-serial.log |
| Ruff / diff | ruff-final.log通过；git diff --check通过 |
| 全追踪密钥扫描 | secrets.log通过，20条既有测试fixture豁免；未新增豁免，不等于穷尽隐私审计 |
| 页面 | browser-accepted/summary.json通过；161个50ms DOM采样无预置错误完成句，另有on_send逐yield测试 |
| 后台 | ui_backend_receipts.json：S20002一条approved申请，真实页面确认与审核，未退款 |
| 历史与用户文件 | 4043份旧结果追踪文件未改，三项原工作树修改保持，preservation.json及提交前核对 |
| 最终提交CI | 提交/推送后按准确SHA另行读取Actions；最终run链接与状态在本轮交付回复中报告，不用基线绿灯替代 |

完整序列运行收集后，正确短答fixture校准为场景中真实存在的V201并独立复验；生产源码不变，最终提交CI会完整覆盖该fixture。
四项跳过为可选外部依赖/环境检查，Pandas/Gradio警告保留；测试数量不是业务成功率。
- 首次完整回归931通过/4跳过/1失败，800ms流式预算用例未收到首段即超时，发生于并行回放/浏览器负载。
  保留full-pytest.log；不改变断言或业务超时，随后串行回归单列。
- 初版驱动依次在读取异常、缺trace和错误的旧草稿订单fixture处退出；所有部分raw和日志保留。
  正式四臂均44例完整执行，新驱动正确记录注入异常，不把它算业务成功。
- 浏览器初次完整通过；重复使用已提交数据库导致预览拒绝，及错误假设管理员选项含“待审核”导致等待超时，
  均为验收驱动/环境问题，失败截图和日志保留。最终用全新库、实际选项协议复验，不修改生产页面。

## 复现与回退

```bash
uv sync --extra dev --frozen
uv run pytest tests/test_action_completeness.py tests/test_return_state_check.py
uv run python scripts/eval_action_completeness.py --out /tmp/action-off-unique
uv run python scripts/eval_action_completeness.py --enabled --out /tmp/action-on-unique
uv run python results/action_completeness_20261023/analyze.py
uv run pytest
uv run ruff check .
uv run python scripts/check_secrets.py --all-tracked
```

旧源码对照须在29c603f独立worktree复制当前评测脚本及本轮冻结目录后运行；原历史结果不用覆盖。
每个out必须全新，driver拒绝已有目录。Gradio流程命令见[简历证据](ACTION_COMPLETENESS_RESUME_EVIDENCE.md)，
使用全新scenario目录，客户建库就绪后才启动管理员。
回退可关闭已有P0开关，或revert本轮完整提交；没有新迁移/依赖/公开API。

## 后续建议

先解决D06“真实业务状态摘要与咨询子诉求共存”及D05明确取消确认交互，分别冻结反例；
不要扩大授权正则来让分数好看。本轮不继续开发这些能力。
若要验证自然模型收益，另冻结未见任务、预算和评分契约后等待新授权；当前无付费授权。
默认开启、全目标修复、通用授权/事实保障均No-Go；窄范围可审查修复和离线证据Go。
