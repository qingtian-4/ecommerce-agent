# 电商客服 Agent P0 状态完整性验收

日期：2026-10-20。基线与 HEAD：`3a4de7c2d9e1da5dd74335b8e5a72d6c1daead21`；
分支：`fix/order-draft-integrity`。审查的是该分支尚未提交的完整 P0 改动。

**验收建议：可以启用现有窄入口。默认开关仍为 false，本轮未自动开启。**
修复了最终工程审查发现的异常页面状态不同步和未绑定订单提示问题，完整回归通过。
真实持久写入有可重复的提交开销：补存加读回预热 p95 为 240.26 ms；不能承诺 20 ms。
这不构成放宽权限、事务或业务条件的理由，也不是整体模型成功率或端到端提速证明。

本轮没有付费 API、真实模型评测、完整 τ Benchmark、后训练、P1/P2、提交、推送、
合并或发布。所有业务数据为既有合成场景，测试与探针使用独立临时数据库。
用户已有 TODO、skill 修改和冻结原始结果保持不变。

## 1. 审查发现与实际修正

| 严重程度 | 有证据的问题 | 局部修正与验证 |
| --- | --- | --- |
| 中 | 草稿提交后 `finish_turn` 抛异常，原 on_send 仍展示先前的“尚无退货草稿”卡片 | [app.py](../../app.py) 异常分支按用户重读实际任务和相符的已持久化历史；读回失败显示未知。新增参数化 UI 集成测试复现两条路径 |
| 低 | 已有未绑定订单的草稿正确返回 conflict，但最终提示“订单 None” | [state_notice](../../src/acs/return_state_check.py) 明确提示尚未绑定订单；不改变任何写入判据 |

同时防止相同问题在 `begin_turn` 失败时误用上一轮同文回答；恢复历史须匹配长度、
当前原话和已结束状态。用户切换测试确认不复用别人的会话、历史或草稿。

回归中发现恢复的持久化中断文字漏掉原有“没有人工接入”说明，已在
[ChatService](../../src/acs/chat_service.py) 的中断持久化文字补回，原能力边界测试不变。
两项旧 UI 测试由固定文案改为 UI 历史等于持久化历史、interrupted 及明确未完成；
partial、实际异常、错误面板与失败状态断言保留，没有放宽业务判据。

Standards、Spec 两轴及安全、架构复核分别记录于 [REVIEW.md](acceptance_20261020/REVIEW.md)。
未发现需要继续修改的阻塞项；没有因无证据的复杂度猜测进行重构。

## 2. P0 根因与不变约束

`orders-02 / trial=1` 首轮为 `我想退S20017。`。真实 get_order 返回本人订单，模型却
只追问商品，四轮都没有调用 update_return_task。旧 ChatService 在 answered 后直接
finish_turn，缺少持久化后置检查；SessionStore 本来就支持只有订单的部分草稿。

冻结证据：[原始轨迹](../iteration_v2_recovery/local_after/raw.jsonl) 第 21 行，SHA-256：
`151d1419169076d50b95cdeed30c9533c06f69f03740953d7f50a372d902e9aa`。

- [return_state_check.py](../../src/acs/return_state_check.py) 只完整匹配当前原话中有限的“我想退/我要退＋订单号”，核对同轮实际 get_order 调用与成功回执的订单、用户。模型最终回答、历史结果、候选列表不构成授权。
- [sessions.py](../../src/acs/sessions.py) 的 only_if_absent 在原 BEGIN IMMEDIATE 内检查归属、当前轮次和任务存在性。只允许 order_id；新增、已满足、冲突可区分，失败由异常及读回说明。任意已有任务都不覆盖、不复活、不增加 revision。
- [chat_service.py](../../src/acs/chat_service.py) 在正常终轮检查，补存后再次读取权威任务，同步 AgentResult.answer、最终 assistant 和 final Trace，再 finish_turn。错误或预算过期不虚报正常完成；读取失败明确未知。
- [app.py](../../app.py) 独立呈现 state_check，不伪造模型工具调用；流式文字已经展示时只作显式终态更正，不能撤回先前文字。

不选择商品、数量或原因，不创建申请。ReturnService、公开工具、明确确认、权限、
事务、幂等、预览、审核、schema、RAG、模型提示和技术栈均保持原约束。
本轮验收仅额外改动异常恢复与提示，没有更改原匹配或条件事务算法。

## 3. 红绿、配对与回归证据

初次 P0 实施：既有 398 passed、1 skipped；迁入正式 orders-02 红测试后 1 failed、
3 passed；最小实现后同四项 4 passed。原日志保留在本目录。

本次工程审查：新增的响应丢失/读回失败和未绑定订单测试，修复前 **3 failed**，
修复后 **3 passed**。再补相同问题的预留失败和用户切换，共新增 5 项 P0 测试，
P0 测试总数由 55 增至 60。
证据：[红测试](acceptance_20261020/review_red.log)、[首次绿测试](acceptance_20261020/review_green.log)。

首次扩大回归的失败也保留：[针对性](acceptance_20261020/targeted_pytest.log)、
[完整回归](acceptance_20261020/full_pytest.log)。两项旧措辞断言和一项真实能力说明
遗漏分别处理后，相关故障路径 [7 项复验通过](acceptance_20261020/review_regression_green.log)。
没有挑选成功模型重试、删失败记录或修改原始任务判据。

| 最终检查 | 本轮实际结果与证据 |
| --- | --- |
| P0、UI、能力边界定向测试 | [108 passed](acceptance_20261020/final_targeted_pytest.log)，含禁止 socket 的 ChatService/SQLite 集成测试 |
| 隔离工作树完整 pytest | [458 passed、1 skipped、4 warnings](acceptance_20261020/final_full_pytest.log)；缺 tau2 的可选适配跳过，Gradio/Pandas 弃用告警保留 |
| Ruff、差异检查 | [ruff check . 通过](acceptance_20261020/final_ruff.log)、git diff --check 通过 |
| 配对无网络探针 | [8 组、16 段](acceptance_20261020/paired_final/summary.json)，固定原始模型决策、输入及业务快照 |
| UI 与恢复 | 任务卡、历史、final Trace、on_restore 定向断言通过；[离线 DOM](acceptance_20261020/ui_verification.json) 及[截图](acceptance_20261020/ui.png)核对通过 |

配对结果：原始 orders-02 关闭时无草稿，开启后 S20017、item=null、revision=1；
6 组纯查询、否定、假设、转述、错误订单读取或他人订单负例，无误补存。
模型正常保存组两侧均 revision=1。错误覆盖、额外 revision、新申请、新审计均为 0。
业务表指纹和申请/确认/审计逐行记录前后相等；场景已有种子申请，报告的是新增量。

测试还覆盖：重复进入、同/不同订单竞争、已有同/异订单、cancelled、submitted、
用户切换、插问、写入失败及已提交后回执丢失、读回失败、断流、运行预算耗尽、
finish_turn 前中断、重启/重试、预览保持有效，以及原有提交、权限与审核安全边界。

完整测试在冻结基线的临时 detached 工作树中应用本次交付，排除用户无关修改；
[源码指纹](acceptance_20261020/tested_source_manifest.json) 与当前仓库相等。
UI 截图来自真实处理器产出的静态 HTML，用 Chromium 离线渲染并人工查看，
不是本轮重新进行了完整在线 Gradio 点击、真实模型或容器发布验收。
中断错误页面保留实际错误，不伪造已丢失的执行 Trace。

## 4. 性能定位与启用判断

独立无网络微基准预先固定：每路径 10 个新进程冷试验、5 次预热、30 个热试验；
竞争每次四线程。共 360 个有效测量操作、45 个保留的预热操作，失败 0。
同一 WSL2、CPU 和 SQLite 设置，未与本轮 pytest、探针或浏览器并发。
完整六路径冷/热 p50、p95、最大值、N 与阶段拆分见
[PERFORMANCE.md](acceptance_20261020/PERFORMANCE.md)。

| 预热路径 | 有效 N | p50 / p95 / max（ms） |
| --- | ---: | ---: |
| not_applicable | 30 | 0.016 / 0.028 / 0.031 |
| blocked | 30 | 0.450 / 0.941 / 1.001 |
| satisfied | 30 | 0.660 / 1.031 / 1.111 |
| repaired，真实提交 | 30 | 150.264 / 203.031 / 241.593 |
| repaired 后独立读回 | 30 | 150.592 / 240.255 / 258.217 |
| 四线程条件竞争，30 次试验 | 120 | 233.424 / 488.460 / 734.573 |

补存加读回的提交 p50/p95 为 148.98/238.30 ms，意图识别 p95 0.020 ms，
非竞争写锁 p95 0.021 ms，条件写入残差 p95 0.929 ms，读回 p95 0.911 ms。
冷启动同路径 p50/p95 为 142.27/222.18 ms，说明写入代价不只发生在冷启动。
竞争的 40 次冷/热试验全部恰有一个 repaired、三个 satisfied，revision 始终为 1；
热竞争写锁调用 p95 430.11 ms。以上竞争操作相关，不能当作 120 个独立会话。

可重复瓶颈位于 SQLite 原生提交边界，竞争进一步增加锁等待；提交包含持久化同步、
文件系统工作和宿主调度，未作系统调用/磁盘追踪，不能武断归因某块物理磁盘。
原先 404.82 ms 只有一个真实写入样本且无阶段计时，无法事后确定其精确原因。
不再用当时四个混合样本的最近秩 p95 判断稳定性能，也不宣称这次实现已提速。

没有发现意图、条件 SQL 或读回中的可消除重复工作，因此不作性能代码改动。
保留原 DELETE journal、FULL 同步与原子事务；不为候选 20 ms 目标牺牲数据可靠性。
**可以启用的依据是窄入口安全、原子性、恢复和回归证据均通过，而非已达到 20 ms。**
若部署另有 20 ms 的硬性持久写入 SLO，当前环境不满足，应保持关闭并独立评估存储环境。
默认启用仍是单独的配置决定，本轮没有改动用户 .env 或默认值。

## 5. 调用、上下文与成本

原始四轮两侧均 5 次假模型请求，额外模型/embedding 请求为 0；检查本身只使用
本地原话、实际工具回执与 SQLite。首轮两次模型输入的字符差为 0，检查发生在模型
输出之后；服务器追加说明不属于新的模型生成请求。

后三轮每次增加 **1,207 个序列化输入字符**：已有草稿系统消息 1,138，
首轮更正说明在历史中的增量 69。复用现有草稿投影，其中不含 sources；
这不是新 Fact Ledger。证据：[逐调用拆分](acceptance_20261020/context_analysis.json)、
[可复现离线分析](acceptance_20261020/context_probe.py)。分析工具参数使用紧凑 JSON，
与配对探针的空格格式有 1 字符绝对值差异，开关差值与拆分不受影响。

ScriptedLLM 的每调用 10/5/15 Token、usage latency 与 cost 是硬编码占位，
不代表真实模型 Token、价格或耗时。实际 Token、真实模型延迟和费用收益均未测。
真实后续模型可能改变工具选择，不能由固定模型探针推断总 Token 零增加或整体质量提升。
历史 140/160 没有变成本轮改进成绩；所有已见材料仍是开发/回归证据。

## 6. 交付、复现、回退与下一阶段

本轮输入冻结与保护记录：[input_manifest.json](acceptance_20261020/input_manifest.json)；
最终完整性与源码/产物校验：[verification.json](acceptance_20261020/verification.json)。
初次报告已完整归档为 [previous_REPORT.md](acceptance_20261020/previous_REPORT.md)；
旧 verification.json 的报告哈希对应归档版本。本轮仅按用户要求更新本 REPORT，
其余旧原始结果、失败日志和冻结评分不改写。

```bash
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest tests/test_return_state_check.py tests/test_app_handlers.py tests/test_catalog_knowledge_modes.py
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest -rs
.venv/bin/ruff check .
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python scripts/probe_order_draft_integrity.py --out /tmp/p0-paired-new
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python scripts/bench_return_state_check.py --out /tmp/p0-bench-new --cold 10 --warm 30 --warmup 5
```

输出目录必须尚不存在。启用设 `ACS_RETURN_STATE_CHECK_ENABLED=true` 并重启；
回退设为 false 并重启，不删除合法草稿，无迁移或索引重建。若以后提交，按
[提交标题](acceptance_20261020/COMMIT_MESSAGE.txt) 和 [PR 草稿](acceptance_20261020/PR_DESCRIPTION.md)
将实现、必要测试、文档与证据作为一个完整改动；当前未执行 git commit 或创建 PR。

最值得继续的具体 Badcase 是 `orders-01 / trial=1`：原始 list_orders 中 S20008
为 V201-2 白色，S20003 为 V201-1 黑色，回答却合并成“S20008、S20003 黑色”。
证据：[原始记录提取](acceptance_20261020/next_badcase.json)，冻结 raw 第 23 行。
下一阶段应先冻结这组订单、明细与颜色绑定的反例和正确例，验证对象与回答事实绑定，
再决定最小改进；不把它强行纳入本次订单遗漏检查。本轮未实施 P1/P2、
Context Engineering 重构或 Fact Ledger，后续由用户决定。
