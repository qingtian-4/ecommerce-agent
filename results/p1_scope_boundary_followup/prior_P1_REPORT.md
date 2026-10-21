# 电商客服 Agent P1 订单候选事实验收

日期：2026-10-20；分支 `feat/order-candidate-facts`，基于 P0 页面补验提交 `4b10dbe`。
P0 的 [PR #1](https://github.com/qingtian-4/ecommerce-agent/pull/1) 已创建并更新真实 Gradio
补验证据，base=main，仍 OPEN，未合并。P1 改动只在本地独立分支，未提交或推送。
用户原有 TODO、skill 修改与冻结历史结果保留。没有付费 API、完整 τ 或 P0 性能重测。

**本轮结论：原始订单颜色串用在受限的纯候选路径已修复，离线回归通过。**
P1 开关默认关闭，建议先审阅候选展示的覆盖与信息取舍，不直接默认启用。
本轮不宣称已经完成通用订单事实一致性、原提案全部 P1/P2 或真实模型成功率提升。

## 根因与实际改动

原始 orders-01/trial=1 已查询 list_orders，但自由回答把不同 SKU 合并为：
“S20008、S20003，演示 A1 入耳耳机，黑色”。同轮实际结果分别为白色和黑色。
工具已经保留订单、明细和 SKU，根因是自由文本重组丢失了对象与属性绑定。
没有更换数据、改写原始任务或修改 Prompt。

[order_candidates.py](../../src/acs/order_candidates.py) 从本轮实际查询结果生成结构化行，
按下单日期降序、订单号升序、明细编号升序呈现，不按商品名称合并。
未知属性保持未知，表格单元格转义 HTML、管道、换行及 Markdown 图片/链接语法。

| 订单 | 明细 | SKU | 工具颜色 | 新的权威展示 |
| --- | ---: | --- | --- | --- |
| S20008 | 10 | V201-2 | 白色 | 白色 |
| S20003 | 4 | V201-1 | 黑色 | 黑色 |

[ChatService](../../src/acs/chat_service.py) 仅在 answered、没有任何既有任务且状态可读、
本轮恰有一次成功 list_orders 且没有其他工具/错误/检索时替换终态回复。
回答、最后 assistant、final Trace 一并更新后才 finish_turn。
新增 fact_render 记录来源索引、结构化行、当前用户和原模型答案；
[UI](../../app.py) 独立显示“订单候选事实”，不增加工具计数或伪造最终回答。

开关 `ACS_ORDER_CANDIDATE_RENDER_ENABLED=false` 独立于 P0，支持关闭消融。
不修改 ToolRegistry、SQL、ReturnService、数据库 schema、RAG、确认或权限；
不保存订单草稿、选择明细、推断资格或创建申请。不是 Fact Ledger 或新框架。
范围与替代方案见 [P1 说明](../../docs/P1_ORDER_FACTS.md)。

## 红绿与回归

[原始红测试](red_pytest.log)：**1 failed、1 passed**，开启侧仍输出原始错误回答，
没有绑定明细颜色；关闭侧保留并证明原始缺陷。
[最小实现后](initial_green_pytest.log)：同两项 **2 passed**。

[定向测试](targeted_pytest.log)：**25 passed**。覆盖：25 条实际明细逐行绑定与排序、
SQL 颜色变更、其他用户隔离、未知 SKU、恶意单元格、已有任务、错误与超预算终轮、
缺/乱序/重复回执、混合工具不接管、UI 历史/恢复/Trace 同步、与 P0 共存不误补存。
既有草稿不改订单、不增加 revision；候选列表不构成 P0 补存的 get_order 授权。

[完整 pytest](full_pytest.log)：**483 passed、1 skipped、4 warnings**。
跳过项为缺少可选 tau2；既有 Gradio/Pandas 告警保留。Ruff 和 git diff --check 通过。
[输入与源码冻结](manifest.json)在完整回归和配对分析阶段保存；
[最终校验](verification.json)核对已测源码、保护文件和交付产物。

## 两种方案的同条件对照

固定假模型、同一合成业务快照与两轮输入，四组各开/关，共 **8 段对话、16 个用户轮次**。
原始错误、实际 SQL 颜色改蓝、其他用户、本来正确的模型回复均保留。
探针拒绝外部网络和已有输出目录，完整原始回答与副作用检查见
[raw.jsonl](paired/raw.jsonl)、[summary.json](paired/summary.json)。

| 对照项 | 自由文本 | 确定性候选 |
| --- | --- | --- |
| 原始 S20008 / S20003 颜色 | 黑 / 黑，前者错 | 白 / 黑，分别绑定自己的明细 |
| SQL 将 V201-2 改为蓝色 | 固定原回答仍黑色 | 随真实查询呈现蓝色，不硬编码白色 |
| USER002 查询 | 固定原答案含其他订单，是构造的反例 | 权威表格仅含 USER002 的实际候选 |
| 原模型两项颜色本来正确 | 保持简短原答 | 仍完整展示所有候选，不合并或猜测 |
| 新草稿、新申请 | 0 | 0 |

25 条候选绑定的完整集合由正式测试逐项对照真实工具结果，未仅靠“含白色”关键词判分。
这只是固定开发错误与反例的对照，不是新的模型总体正确率、盲测或真实用户任务成功率。
没有把历史 140/160 写成本轮提升。

[可查看的方案对照](paired/comparison.html)与[离线截图](comparison.png)已生成并人工查看；
右侧以结构化数据展示两种颜色。该图是独立对照页面，不是 P1 真实模型或完整浏览器点击验收。
P1 UI 的实际 on_send、on_restore 与独立 Trace 已由无网络定向测试覆盖；
P0 的真实 Gradio 三轮及恢复另见其 PR 和 [页面报告](../p0_order_draft_integrity/gradio_acceptance_20261020_verified/REPORT.md)。

## 调用、上下文与性能限制

每段均为首轮两次假模型调用、后续一次，开关两侧都是 3 次；机制额外调用为 0。
首轮两次模型输入字符增量均为 0。没有省去原模型的最终生成请求；
保留它用于相同模型决策下的对照，再确定性呈现，不报告节省调用或输出 Token。

| 场景 | 下一轮序列化输入字符增量 |
| --- | ---: |
| 原始错误 | +1,618 |
| 颜色变更 | +1,618 |
| 其他用户 | +1,624 |
| 原模型简短正确答复 | +1,978 |

增加来自完整候选表替换摘要后保留在历史中的文字。假模型 usage 为占位，
真实 Token、费用、线上时延与后续工具选择未测。单轮耗时仅原样保留在 raw 中，
样本少且与 pytest 并发，不能拿它比较稳定延迟；本轮不重跑 P0 性能测试。
渲染函数不增加模型、SQL 或工具请求；完整表格的输入代价需在后续 Context Engineering 中另行评估。

## 已知边界与后续顺序

候选表展示实际工具查询的全部订单和商品，不自动按“最近”“耳机”筛选，
不把状态当作退货资格。替换自由回答可能丢失筛选、汇总或其他解释；
默认仍关闭，扩大入口前应冻结并验证这些问法，而非悄悄扩大保证范围。

不能撤回已经流出的错误文字。最终显示明确候选更正，历史保存权威表格，
原错误答复只留在诊断 Trace；没有实现全面流式事实拦截。
已有任务、混合工具、历史事实、无工具回答和政策推理仍走原路径，
直接 Agent/τ 入口也不经过本挂点。模型主动错写不是本机制可修复的缺陷。

复现：

```bash
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest tests/test_order_candidate_facts.py
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest -rs
.venv/bin/ruff check .
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python scripts/probe_order_candidate_facts.py --out /tmp/p1-paired-new
```

启用设 `ACS_ORDER_CANDIDATE_RENDER_ENABLED=true` 并重启；回退 false 并重启。
无数据迁移，不删除会话。当前本地改动没有提交或推送，准备时须将实现、测试、
文档和证据作为同一个完整改动，排除用户原有 TODO/skill 修改。

下一步先审阅 P0 PR、决定合并，再审阅 P1 的受限候选协议与信息取舍；
随后才决定 Context Engineering 和更广的多轮 Evaluation。
本轮未自动开展 Context Engineering、Fact Ledger、后训练或付费评测。
