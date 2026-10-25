# M8 独立合成多轮 Evaluation

## 范围和冻结

基线953a00b；Phase0分句修复5ef59d5（6项红测、16项新增反例、完整1064 passed/0 skipped）。该修复先于任务结果冻结。默认客服、Gradio、Web服务、官方τ²工具与Reward均不变。

`data/eval/v3_multiturn_m8_v1`：10开发、20冻结测试任务，全部至少两次聊天；25共同能力任务与5个V3应用增量任务。每任务/每臂创建独立Formal Fixture，新增独立合成订单和明细；数据在种子阶段写入后安装原正式隔离Trigger，不删除Trigger。逻辑业务时钟固定2026-10-25 04:00 UTC；预算和延迟仍使用真实时钟。任务包含用户目标、Fixture索引、多轮输入/应用动作、允许工具、必需回执、允许错误、SQL预期、回答证据及成功失败合同。

封存tasks/contracts的SHA256和所有行为源码指纹，Loader拒绝篡改。实际运行还记录Git完整SHA。开发测试只运行D任务，封存前不跑T任务。冻结后不因成绩改变任务、初态、参考或评分器；发现缺陷只能留在本版报告，下一版另立版本。

这些是同一工程代理新编写的合成任务，不是独立人工语料或严格盲测。冻结首次使用只控制版本泄漏，不证明任务来源独立性。历史六阶段没有原样复制；能力类别与已知风险仍是开发知情的。首次查看本轮结果后，这30题均成为已见开发证据；不能再包装成未见泛化成绩。

## 三类实验

| 类型 | 执行 | 可以回答 | 不能回答 |
|---|---|---|---|
| 共同能力 | 原CustomerServiceAgent循环 vs FormalReturnPoc原生Graph | 固定决定下的消息/工具/SQL合同、框架开销 | 真实模型哪组更可靠 |
| V3增量 | Interrupt应用接受、拒绝、取消、重复和回执丢失恢复 | 现有应用桥与权威数据库的联通性 | 把旧臂未接入能力当成胜负 |
| 工程安全 | MockTransport、故障注入及已有M6/恢复/SSE回归 | 幂等、事务、预算、临时正文与最终结果分离 | 模型业务质量与生产可用性 |

共同臂是**旧Loop + 同一实验Gateway和模型边界**，不是原默认ChatService的整套体验。复用真实CustomerServiceAgent，未手写第二个工具循环。Gateway、ToolRegistry、ReturnService、会话/Turn授权及业务回执完全相同；旧臂额外输出保护不启用。两侧都按相同正式隔离Prompt、请求级SQL事实投影和原生历史调用同一个模型接口及工具Schema。原模型/工具历史保留；SDK不发送的ToolMessage.name/status不算输入差异，审计仅规范化不携带权限的随机ID。

原Loop及V3内部相同8模型步骤/16工具每轮保护；统一外层EpisodeLedger限制整个多轮Episode为16决定、24工具、64执行步、模型60秒/任务600秒。离线决定数与HTTP请求数分开，离线没有伪造usage。付费提案另统一收紧每Episode4请求，使用全实验同一费用账本，失败不自动重跑。

## 指标定义

- SQL State Correctness：每一步匹配冻结预期，基础业务表不可变化；申请/确认/审计数量一致且审核0，成功另读完整M6回执。Graph或回答不能证明成功。
- Tool Execution Validity：实际请求在允许集合中；必需工具有真实成功回执；业务拒绝、found=false和preview.ready=false不当成功。错误取消的预期拒绝与执行成功分开。
- Task Success：全部SQL/工具合同通过且每条原始最终答复经完整注释核验。未解决语义不能算通过；它与SQL成功分别报告。
- Raw/Display Faithfulness：分别评分原文和展示文本。合同模型的固定答复有预先人工编写的完整引用注释，事实值在实际执行后从SQL、商品资料和政策取得；评分器不调用Grounding规则。新文本不匹配参考时标unresolved，进入独立审查队列。
- 纠错率：原文错误且展示正确 / 原文错误；漏检率：原文错误且展示仍错误 / 原文错误。误纠正率：原文正确而展示错误 / 所有改动答复；未解决的改动另列，不能当0误纠正。
- Recovery Success：明确执行了恢复并取得权威回执的次数/恢复尝试；无恢复任务不虚构成功率。
- 模型决定、实际HTTP、输入/输出Token、整轮/聊天执行延迟、费用分别记。脚本无真实Token，填null；0元仅表示本轮无付费。临时正文错误由独立真实SDK/SSE合同测量，主非流式Runner不冒充测过该项。

冻结参考不是开放语言评分器。Quote/否定的speech-act标签是作者标注；没有任意表达语义保证。匿名审查队列隐藏臂名及raw/display标识，密钥文件分离；文风仍可能泄漏来源，不能称完全双盲。未来审查须冻结标签/证据版本、保留未判定，不用Judge替代SQL事实。

## 运行

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
# 新目录，默认完全离线；先看开发集：
uv run python scripts/eval_v3_multiturn.py --ids D01 D03 --out /tmp/m8-dev-NEW
# 全30任务，25配对+5增量，共55 Episode：
uv run python scripts/eval_v3_multiturn.py --out results/m8-NEW
# 仅输出费用提案，不读取凭据、不创建账本：
uv run python scripts/eval_v3_multiturn.py --mode pilot-plan --out /tmp/not-created
```

私有目录保留SQL、Graph、原始回答、展示回答、原生模型输入输出和匿名审查材料；公开只导出脱敏结构化核验、哈希和必要失败摘要。不得将官方τ²分数与此内部合同混合，也不报告pass^k。


## v1审计与v2测量修正

v1（源码634cc43）首次封存后完成55 Episode；随后独立审计发现新Fixture订单总额600与明细合计不一致、Evidence.checked_at未跟随合成时钟、有限事实合同不足以证明用户目标完整完成。原v1的数据/Scorecard/SQL/有效性失败均保留，不修改其成绩。

v2仅修正合成金额、共享Evidence时钟，增加明确候选ID/订单状态/数量等必要答复要素检查。其用户输入、模型固定决定及原SQL预期不变；Task Success额外要求目标要素，不再把“忠实但未回答问题”算成功。这是测量与Fixture修正，不是模型提升；必要词项只是保守覆盖检查，不是通用语义判断。

新目录v3_multiturn_m8_v2保留10/20来源分组，但所有30题均已在v1见过，明确作为已见数据复验，不宣称新的独立留出。v1必须在634cc43复现，当前实现会拒绝v1旧源码指纹；当前默认Runner为v2。未来泛化评估仍需要独立新任务和人工语义审查。

## 共同能力与增量矩阵

| 能力 | 旧Loop对照臂 | V3 | 权威边界 |
|---|---|---|---|
| 商品/订单/物流/政策 | CustomerServiceAgent + 实验Gateway | 原生create_agent + 同一Gateway | ToolRegistry、同一BM25与业务DB |
| 草稿补充、更正、取消 | 同一Turn/回执约束 | 同一Turn/回执约束 | SessionStore授权与revision |
| 预览与失效 | 同一ReturnService | 同一ReturnService | SQLite、政策hash、有效期 |
| 完成Turn历史 | SessionStore保存原生消息 | Checkpointer + SessionStore | 原始tool_call_id对应关系 |
| 原生Interrupt应用确认 | 对照臂未接入，不计胜负 | 单列增量验收 | FormalConfirmation |
| 模拟提交与幂等 | 不在共同能力胜负统计 | 单列5例 | 同一ReturnService事务与M6回执 |
| COMMIT回执丢失恢复 | 本轮不比较 | 单列SQL恢复 | Graph仅诊断，镜像可能待对账 |

原Loop内部context_max_chars调至1000000防止比候选更早截断；两侧同一事实投影，不以历史压缩制造优势。真实Pilot还有共同的实际HTTP正文20000字节上限。预算上下文一致不等于云端随机输出或随机ID的字节完全相同，原始请求留在私有证据。

匿名审查补充导出：`uv run python scripts/export_m8_review.py --run results/m8_offline_v2_20261025_run1`（输出文件须不存在，防覆盖）。该只读后处理不改预测、评分器或原始实验。228项已导出，但未实施独立人工语义评审。


工具有效性在此指执行/拒绝合同，不是模型工具选择正确率。D07/D09/T10/T11故意请求无授权取消，由同一业务层拒绝；不能因状态安全就说模型理解了取消意图。实际工具成功/拒绝数另存tool_summary.json。实验step按模型边界计数，不将Graph内部节点数与旧Loop的一步混成统一CPU工作量。

最终审查输入为私有blind-goal-review-v2.json，使用随机UUID而非可推算的任务标签哈希；旧导出保留但不作最终审查包。原始数据与最终包已逐文件核验并持久归档到~/.local/state/ecommerce-agent/m8-20261025-5d7bbf9（0700），不依赖/tmp长期保留。
