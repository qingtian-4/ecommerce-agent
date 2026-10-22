# 电商客服 Agent τ² Retail 正式评测

更新：2026-10-22。原始 V2 的获批补充运行已结束，**119/120 段完成官方评分**；
最后 ACS V2/task111 因逐请求费用预留不足而停止，不能称三臂正式评测完整完成。
没有更换 V2 源码，没有重跑已评分失败，没有 V2.1 付费成绩，也没有新的 pass^3。

| 指标 | Official | ACS V1 | 原始 ACS V2 |
| --- | ---: | ---: | ---: |
| 官方已评分 / 计划 | 40/40 | 40/40 | 39/40 |
| 官方成功 | 35/40 | 36/40 | 35/39，1段未评分 |
| 完整单臂 pass^1 | 87.5% | 90.0% | 不报告 |
| 相同39题成功 | 34/39 | 35/39 | 35/39 |
| 相同39题模型请求，含全部角色 | 647 | 647 | 656 |
| 相同39题输入 / 输出 Token | 2,955,906 / 60,565 | 2,947,277 / 60,157 | 3,020,545 / 62,007 |
| 相同39题实际工具调用 | 297 | 292 | 303 |
| 相同39题错误工具回执 / 参数错误 | 7 / 0 | 7 / 0 | 9 / 0 |
| 相同39题 Router / fallback / 空范围 | 0 / 0 / 0 | 0 / 0 / 0 | 199 / 197 / 2 |
| 相同39题 Recovery retry | 0 | 0 | 0 |
| 相同39题延迟 p50 / p95，秒 | 34.99 / 60.28 | 38.45 / 66.04 | 39.46 / 64.86 |
| 相同39题保守记账 CNY | 12.792664 | 12.751620 | 13.074292 |

[40题逐项表](../results/tau_retail_formal_combined_20261022_final/task_comparison.csv) ·
[完整指标与逐段回执来源](../results/tau_retail_formal_combined_20261022_final/analysis.json) ·
[39题同条件统计](../results/tau_retail_formal_combined_20261022_final/common_39_metrics.json) ·
[官方 DB/NL/Action](../results/tau_retail_formal_combined_20261022_final/score_components.json) ·
[失败索引](../results/tau_retail_formal_combined_20261022_final/badcase_index.json)。
Action write 匹配分别51/56、51/56、48/53；DB分别35/40、36/40、35/39，
NL=1分别39、39、38，另各1题没有NL分项。Action不等于reward，保留官方判据。

## 补充运行与费用

用户“直接执行吧”批准既有恢复计划，仅沿用原独立40元总保护上限。
原102个结果全部跳过，task97/V1从全新官方环境作attempt2；旧中断尝试和#1668未知usage保留。
补充18段全部开始，17段评分完成，最后task111/V2记录BudgetExceeded和部分轨迹。
332个新请求均有usage；历史#1668仍未知，按0.163316元全额预留计费。

- 全链2000请求，保守记账 **39.840192/40元**，余额0.159808元不足下一次保守预留。
- 本次新增保守记账7.057224元，新增已知输入1,619,818 / 输出36,122 Token。
- 已知usage按原配置价格估算总计3.369738元，新增0.337061元；未知#1668不在此估算中。
  **估算不是供应商账单，也不是放松硬预算的理由。** 所有角色及中断尝试均计入保护。
- 有效120个尝试文件对应1989请求；另外11请求属于旧task97/V1，费用0.360564元。
  有效尝试指标与总费用分列，不能把旧尝试Token混入新task97。
- tmux监督进程真实退出码2，持久exit标记request_or_money_limit。此次是正常预算终止；
  原102段后的异常退出仍为UNKNOWN，不能据本次日志倒推其原因。

[批准与启动](../results/tau_recovery_authorized_20261022/) ·
[新增原始轨迹/账本/单请求记录](../results/tau_retail_formal_supplement_attempt2/) ·
[监督退出码](../results/tau_retail_formal_supplement_attempt2.process.json) ·
[只读父链审计](../results/tau_retail_formal_combined_20261022_final/source_audit.json)。
1973份历史结果及3份用户工作哈希未变。预演与live恢复协议JSON语义相同，但对象序列化顺序使
字节SHA不同：e732daa…与463eca5…。批准绑定预演文件；CLI比对解码后计划，未改任务或预算。
[完整校验](../results/tau_retail_formal_combined_20261022_final/preservation_audit.json)。
原102段版本保留为[历史报告快照](../results/tau_retail_formal_combined_20261022_final/FORMAL_REPORT_BEFORE_SUPPLEMENT.md)。

## 新增分歧与结论

同39题V1与V2逐题reward完全一致：V2修复V1失败0题、新增reward回归0题。
这不等于没有行为回归：task39的空工具范围和无写回执虚报依然存在，详见下面保留的轨迹复核。
V2比V1请求多1.39%、输入Token多2.49%、输出多3.08%、工具多3.77%、保守费用多2.53%。
Router没有一次合法非空收窄，Recovery自然重试0次；**未证明原始V2更强或更便宜**。
也不能把缺失task111视作失败或成功来制造完整排名。

共同失败27/38/56；Official独对39，V1/V2独对79/108。新增108：
Official算出退款346.93美元，但面对用户追问退货运费选择转人工，未实际执行return。
V1/V2说明未知运费及退款时间，用户随后明确确认后，实际执行同三件商品退货并变更DB。
V2另作email问题转人工。三臂模拟用户后续话语不同，不能将此差异归因Router/Recovery。
官方Action因item_ids排列顺序判V1/V2不匹配，但DB/NL均通过，reward=1；不改分。
[108逐消息与调用证据](../results/tau_retail_formal_combined_20261022_final/task108_review.json)。
其余逐例证据沿用下方原始复核，未看结果修改规则或调优官方测试题。

优先级：先保留119段完整证据和1段预算阻塞；不要挪用余额/加重复/重跑全40题。
补task111需要新预算、从最新父链的新补充协议及明确批准，不能调用旧18段命令。
V2.1仅离线路由/回执/混合回答验证，不能混入此表。当前不建议支付3次重复或接入ECom-Bench：
先验证通用能力映射、混合任务完整性及新独立小样本是否有收益，再决定投入。
工程优势可据实表述为官方协议复用、原始轨迹保全、事务/回执安全及可审计断点续跑；
不能表述为Router提升通用任务成功率。

## 复现与停止条件

```bash
# 纯离线分析；新目录必须尚不存在，不发模型请求
uv run python scripts/analyze_tau_resume.py \
  --source results/tau_retail_formal_supplement_attempt2 --out /tmp/tau-joint-new
uv run pytest tests/test_resume_analysis.py tests/test_external_analysis.py tests/test_formal_resume.py
```

三臂120段完成状态仍为 **No-Go（缺1段评分、预算已停止）**；
分析和原始证据可交付。保留下面原102段时点报告，以明确结果演进和当时停止原因。

---

# 原102段时点报告

日期：2026-10-22。**正式协议冻结了 40 题三臂一次，但真实执行中断：完成 102/120 段，
另有一段已开始、17 段未开始。不是完整 test split 成绩，不报告新的 pass^1/pass^3。**
已完成的相同 34 题上，Official、ACS V1、原始 ACS V2 均为 30/34；没有证明 V2 更强。
V2 出现一次可定位的空工具范围缺陷，成本也没有下降。保持 PR #4 未合并及机制默认关闭。

[证据目录](../results/tau_retail_formal_20261022/REPORT.md) ·
[40 题逐项状态](../results/tau_retail_formal_20261022/live/task_comparison.csv) ·
[完整指标](../results/tau_retail_formal_20261022/live/analysis.json) ·
[逐例源码复核](../results/tau_retail_formal_20261022/badcase_source_review.json)

## 协议与完成边界

| 项目 | 冻结条件 |
| --- | --- |
| 上游 | `dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd`，未改官方工具/数据/政策/评分 |
| V1 | main `f34981fa1324310a9d0f0de4430fe1980ba664e9`，独立加载实际源码 |
| V2 | `f6c3387c81e71022b96d553bd080f5751198b6f9`，Runtime/Router/Recovery 源码原样 |
| Adapter | `tau-schema-v2`，两 ACS 臂相同结果归一化与未知写回执停止保护；无 V2-adapted 第四组 |
| 任务 | 官方 test 原顺序全部 40 题，一次/臂，seed=20261022，每题轮换臂顺序 |
| 模型 | Agent/用户/裁判均 `deepseek-flash`，api.deepseek.com，temperature=0，thinking=false，max_tokens=768 |
| 预算 | 本轮独立 40 CNY，所有角色、错误均计入；逐请求按输入字节+4096与最大输出预留，4/16 CNY/M，不依赖缓存 |
| 截止 | SDK/LiteLLM 重试 0，provider 45s；三臂 orchestrator 100 steps/10 errors/240s |
| 内部差异 | ACS 每用户轮 8 steps/16 tools/12 API/120s，64k 字符上下文；Official 没有同等内部裁剪/预算，非流式；ACS 流式 |
| 测量范围 | 官方环境+真实 Loop/Router/Recovery；不经过本店 ChatService/UI/RAG/P0/P1 或进程恢复 |

冻结文件 [protocol_v6](../results/tau_retail_formal_20261022/protocol_v6.json) SHA-256：
`9f1439e091725506bbcfc456311008d6f1422fc4c6ebb4800389d4aa4be788a7`。
预演 120 段通过，负向漏写失败、503 关闭失败/开启成功、零网络请求。
预演使用标准动作脚本检验接线和官方评分，**不是模型成功率**。预演与在线协议/源码哈希一致。

在线进程消失，原 PTY session 不存在，`ps` 无对应进程；没有捕获退出码或 traceback，
不能判断是主机、进程终止还是终端生命周期。最后请求 #1668 为 V1/task97 Agent，
仅保存预留、没有 usage。按预先冻结的未知 usage/环境错误停止规则，不重启、不自动续跑或挪用剩余额度。
原始 requests 的 `halted=null` 是进程未能收尾，不代表仍在运行或实验完整；分析另记
`effective_stop_reason=interrupted_unsettled_request`。

[中断证据](../results/tau_retail_formal_20261022/INTERRUPTION.json) 保留原账本与 102 份结果哈希。
Task97 的 V1 已开始但无最终评分，其他两臂及 100/101/102/108/111 均未开始。
这六题集中在原顺序尾部，不能假设缺失随机，不能用 34 题子集替代 40 题成绩。

## 工具协议修复

原分类器接受本店字典回执，直接接收官方字符串用户 ID 或无 `ok` 的 User 字典会误报。
修复仅在 [OfficialRegistry](../scripts/tau_acs_agent.py) 边界：按官方 Tool.returns 严格校验，
保留原始 ToolMessage 和事实，将合法值包在 `result` 内供 Loop 识别。声明为字符串时不先 JSON
解析（数字计算和产品类型工具也返回字符串）。不改官方数据库和 reward。

按官方 error 标志、调用签名及实际工具源码声明的 ValueError 区分 not_found、business_rejected、
invalid_arguments、tool_failure；真正坏 JSON、错类型、缺字段仍是 invalid_result。
未知写回执停止，不自动重放；与 V1 共用，不冒充 V2 Recovery 独有收益。
错误模式来自固定工具函数 AST，不读取测试答案制定规则。

[五个原始误报](../results/tau_retail_formal_20261022/protocol_replay_cases.json) 逐个回放：

| Pilot 任务/工具 | 合法协议 | 结果 |
| --- | --- | --- |
| 17/find_user_id_by_name_zip | 字符串用户 ID | invalid_result → ok，事实一致 |
| 51/find_user_id_by_name_zip | 字符串用户 ID | invalid_result → ok，事实一致 |
| 51/get_user_details | User 结构字典 | invalid_result → ok，事实一致 |
| 68/find_user_id_by_name_zip | 字符串用户 ID | invalid_result → ok，事实一致 |
| 68/get_user_details | User 结构字典 | invalid_result → ok，事实一致 |

[test_tau_result_protocol](../tests/test_tau_result_protocol.py) 14 项含真实五例和负例；
[test_tau_recovery_protocol](../tests/test_tau_recovery_protocol.py) 20 项含空列表、写后丢回执等。
红测为 13 failed/1 passed（其中五例均复现），修复后专项合计 49 passed。
旧 fixture 初次配置错误和中途一次无效错误原因 fixture 均保留日志，不混作 Agent 红绿收益。
另外修复冻结 V1 跨模块 BudgetExceeded 类型身份导致超时误标 llm_error：从实际 Loop 模块构造
RunBudget，过期时准确停止且不发请求。生产 src/acs 未改。

## 已完成子集结果

| 指标（每臂相同 34 题） | Official | V1 | V2 原始 |
| --- | ---: | ---: | ---: |
| 官方成功/reward 总和 | 30/34 | 30/34 | 30/34 |
| 已评分子集成功占比 | 88.24% | 88.24% | 88.24% |
| 完整 40 题 pass^1 / 新 pass^3 | 不报告 | 不报告 | 不报告 |
| DB match | 30/34 | 30/34 | 30/34 |
| NL_ASSERTION=1 | 33，另 1 无该分项 | 33，另 1 无该分项 | 33，另 1 无该分项 |
| 官方 write action 匹配 | 38/42 | 38/42 | 38/42 |
| 官方 read action 匹配 | 118/123 | 115/123 | 118/123 |
| 官方 generic action 匹配 | 1/6 | 1/6 | 2/6 |
| 模型请求（含用户/裁判） | 551 | 541 | 565 |
| 输入 Token | 2,494,831 | 2,427,729 | 2,572,441 |
| 输出 Token | 50,675 | 49,950 | 52,025 |
| 实际工具调用 | 253 | 246 | 258 |
| 官方 error 回执 | 7 | 6 | 9 |
| 参数 schema/未知工具错误 | 0 | 0 | 0 |
| Router / fallback / 空范围 | 0 | 0 | 173 / 171 / 2 |
| Recovery retry 调度 | 0 | 0 | 0 |
| Episode 延迟 p50 / p95，秒 | 35.56 / 60.28 | 39.26 / 66.04 | 40.73 / 64.86 |
| 保守记账 CNY（完整结果对应请求） | 10.790124 | 10.510116 | 11.122164 |
| 配置价 usage 估算 CNY | 0.991323 | 0.988307 | 1.030796 |

分项见 [score_components](../results/tau_retail_formal_20261022/score_components.json)。
Action 是官方参考动作匹配，不是每次合法读取都要匹配；本批主要 reward basis 为 DB/NL，
没有调用 calculate 不自动等于最终失败。没有 NL 断言的任务默认分项 1，不代表回答全面正确。
全部已完成结果初始 DB hash 一致；所有 ACS stop_reason 为 answered，无自然重试、no-progress、
预算终止或 scope 拒绝，但 answered 并不保证业务成功。

共同成功 29 题；共同失败 27/38/56；分歧 39/79。V1/V2 34 对结果完全相同，
V2-only success=0、V1-only success=0。V2/task56 输入少于 V1（69,443 vs 69,947），仍失败。
逐题成功/失败/未判定/未开始均保留 CSV，不从平均数省略失败。

## 逐例失败分析

以下为编码助手复核原始请求/工具/评分/政策的事后诊断，不是独立盲审，不改官方分数。
详细消息索引、真实写动作、错误回执、评分在 [source_review](../results/tau_retail_formal_20261022/badcase_source_review.json)。

| 任务 | Official/V1/V2 | 证据与根因 | 归属与边界 |
| --- | --- | --- | --- |
| 27 | 0/0/0 | 用户要求同单退货+换货、冲突时优先换货；三臂先 return，使 W7181492 为 return requested，再 exchange 返回 `Non-delivered order cannot be exchanged`；标准单独换货可通过 | 模型业务动作排序/状态规划；非 Adapter 丢数据，重试不能逆转已写状态 |
| 38 | 0/0/0 | 三臂均成功 cancel W9348897，但实际 reason 为用户模拟器明确说的 ordered by mistake；参考 no longer needed，DB 不同。场景未指定原因，政策允许二者 | 模拟器与参考状态不一致；保留原始 0，不把评分争议当作 Agent 能力失败或提升 |
| 39 | 1/0/0 | V1 首次 ZIP 不符后未取到旧 ZIP，最终仅答商品；V2 已认证并待写地址，用户确认提 SKU 后 route orders→空范围，请求608无 tools，message20 生成 DSML `update_user_address` 文本，22 声称已更新，实际无 modify_user_address 回执 | V1 认证澄清失败；V2 有实证 Router 误收窄+无写回执虚报完成。相同 reward 隐藏不同失败，不能说没有机制回归 |
| 56 | 0/0/0 | 原工具含可售 9534205511=$473.43，三臂却选 9375701158=$489.50 为最便宜并实际修改。V1 另算错退款 57.05，实际差额 58.05 | 模型结构化 argmin/计算错误，工具数据完整；V2 少 Token 仍错 |
| 79 | 0/1/1 | Official 模拟用户断言另一只 1L 为黑色并要求黑色，Agent 未核对已有订单中的红色；选黑7661609223。V1/V2核对跨单红色，选2439754078；V2额外 get_item_details 证实旧玻璃红款缺货 | 模拟器错误事实+模型对象绑定；两自研臂都成功，不能归功 V2 新 Router 或本地颜色保护 |

没有证据表明这些已完成失败来自上下文裁剪或内部预算；工具成功但错 SKU/没写却声称完成是真问题。
参考工具/评分并非本店 ReturnService：外部允许真实修改官方合成订单，不声称本店新增该业务。

## Router、Recovery 与费用

Router 171/173（98.84%）fallback；主要是中文业务词和本店工具白名单不匹配英文官方工具。
剩余两次不是有效缩小，而是 task39 的 SKU 触发空范围。无合法非空收窄，因此没有有效测到
工具裁剪的正收益。没有新增英文规则、能力映射或 V2-adapted，没有按任务 ID/答案特判。
安全的后续方向是通用能力元数据与空能力回退，在非正式任务先验证并独立命名，不追改本轮数据。

相同完成子集，V2 比 V1 请求 +4.44%、输入 +5.96%、输出 +4.15%、工具 +4.88%，
保守记账 +5.82%、配置价估算 +4.30%。不能说 Router 降 Token 或降费。
V2 p50 更慢，p95 略低，单次串行请求及不同模拟用户对话不足以推断延迟因果；
ACS 有内容片段的模型请求首字 p50：V1 797ms(n=250)、V2 778ms(n=255)，Official 非流式不可比。

全部尝试包括未完成 task97：1668 请求记录，1667 份 usage，共 **7,692,544 已知 Token**。
角色：Agent1029、用户612、裁判27；未知 usage 的最后一次计入 Agent 请求但 Token 未知。
已结算保守 32.619652 元，加最后请求全预留 0.163316，共 **32.782968 元**，低于独立40元。
已知 usage 按冻结配置/缓存估算 **3.032677 元**，不含最后未知请求，**不是供应商账单**。
未完成轮次的10份已知 usage 也在总账中，不混入完整 34 对质量/效率比较。
官方内部 LiteLLM 未识别型号可能显示 cost=0，不能当免费。

[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)于本日核对：Flash 峰时缓存未命中
输入 $0.30/M、输出 $1.20/M；保护价按4/16 CNY/M且忽略缓存，美元折算预留充足。
固定 model 别名并不固定权重，提供方回报 deepseek-flash；采样温度0也不保证可复现每段自然对话。

自然任务 Recovery 未触发，不证明不存在故障，也没有证明一般恢复率。
[独立冻结矩阵](../results/tau_retail_formal_20261022/RECOVERY_PLAN.md) 与20项无网络专项确认：
首发503有限重试、读后429/503重试、400不重试、部分文本后不盲重试、空结果有效、写后响应丢失
不重放且状态未知停止、三次相同读取无进展停止。覆盖当前进程内有限传输错误，不验证公网可靠率、
跨进程自动续跑或实际付费故障。没有额外发起付费故障请求，也不挪用40元余额。

## 工程检查

完整离线819项通过、3个可选τ模块跳过；可选外部环境专项另有49项通过，Ruff通过。
分析器4项测试含中断后的预留费用、未完成状态和原始reward不可冒充有效成功。
[verification](../results/tau_retail_formal_20261022/verification.json)核实生产src未改、冻结协议源码
与上游哈希一致、1166份历史结果与3项用户工作保持。原始ACS流保存聚合回答与provider usage，
不是逐网络分片抓取；最后未知请求不声称已有完整响应。

## 结论与后续决策

1. **总体谁更强：证据不足。** 已完成34题均30/34；不是完整40题成绩。V2未修复任何V1失败，
   同结果下存在新路由错误，不能宣称强于V1或参考实现。
2. **工程优势：** 自定义 Loop 在同官方环境可完成多数写业务、保留回执/预算/评分链；
   协议五个误报可回放修复，有限恢复可无网络验证。它们不是新模型智能或通用生产可靠性。
3. **PR #4：暂不据此合并/默认开启 Router。** 空能力范围与无回执虚报应先有通用安全处理及新任务验证；
   本轮不修改生产 Runtime，不把修复后版本冒充原始 V2。
4. **三次重复：现在不值得直接花钱。** 先处理可解释的能力映射边界和评测任务38/79的模拟器/参考差异，
   完整性与中断收尾需要单独审阅，再决定如何冻结后续完整运行。当前协议拒绝自动续跑，
   不能重放未知写入或把本轮缺失隐去。360段按已完成均值保守约115.13元、加25%约143.91元仅规划，
   无预算授权且复杂尾题/故障可增成本。
5. **ECom-Bench：暂不开发完整 Adapter/付费运行。** 优先解决现有已证实缺陷。既有
   [静态兼容性分析](ECOM_BENCH_COMPATIBILITY.md)保留53任务及工具缺口，不靠扩业务追分。
6. **交付判定：正式40题完成门槛 No-Go。** 协议修复、预演、真实102段和全部失败证据可复核；
   120段完整真实结果尚未完成。项目演示能力与历史684段结果不因此重评分，也不能补成新的pass^3。

下一轮最有价值的 Badcase 是 **task39 的“混合写请求含SKU→空工具范围→文本伪工具→虚报完成”**。
先以非测试业务同构反例验证空范围安全回退与写回执约束，独立冻结版本，再讨论付费重复。
