# 电商客服 Agent 混合回答验收

日期：2026-10-22。基线 `c513061`，实现分支 `fix/v21-mixed-answer-evidence`。
本目录全部离线，模型决定固定，真实 ChatService/SQLite/KB。没有付费请求，不重算官方reward。

## 根因与最小变更

`write_evidence.guarded_answer` 有写尝试时用真实回执摘要替换模型整答。
它挡住虚报完成，同时吞掉政策/商品解释。保留原核验器，在ChatService最终状态核对之后，
将本轮实际只读工具结果核验出的咨询材料另列，统一更新answer/assistant消息/final Trace并持久化。
不直接追加模型余文，不解析隐藏推理，不增加模型或业务调用，不改变权限、事务、确认、幂等或schema。
UI只增加独立的“咨询依据”fact_render标签。

沿用 `ACS_WRITE_RECEIPT_GUARD_ENABLED`，默认仍false；关闭即可独立消融。
只支持明确正向“同时说明退货政策/退货期限/退货运费”和“同时介绍P001的参数”等句式，
商品ID来自工具结果/参数及正向展示句，不限定测试中的P前缀，V201同样验证。
否定/选择/引用或复杂修饰不当作正向展示授权。材料缺失时说明未核实，不放行模型猜测。

政策完整文档须匹配本地当前KB内容、哈希及请求document_id；真实检索片段仅保留片段，
标注适用条件可能缺失，verified=false。商品须对象相符、必要结构完整、非partial/truncated/has_more。
孤立tool_result不能冒充真实执行，缺少被请求商品时不标完整。

## 红绿与配对

- [freeze_v2](freeze_v2.json) 在实现前冻结24段；[red_v2](red_v2.log) **18 failed / 8 passed**，含2控制。
  初始C10硬编码短名与SQL不符的fixture错误已在实现前纠正，旧freeze/red均保留。
- [审查红测](review_red.log)：政策片段缺条件、否定商品误展示两例失败。
  [catalog_id_red](catalog_id_red.log)、[policy_scope_red](policy_scope_red.log)分别复现非P商品ID和否定政策问题。
- [最终定向](acceptance_targeted.log) **66 passed**：24冻结、2原控制、14边界、4生命周期、22原回执测试。
- [完整回归](full_pytest.log) **877 passed / 4 skipped** 为新增审查边界前版本；
  最终代码须由集成树完整回归与本分支CI再覆盖，不把旧877当最终集成检查。
- [原102段回放](original_102_replay/summary.json) **102/102 DB终态相同**，0请求、0官方重新评分。
  此路径走外部ACSAgent，不走新增ChatService组合；它证明外部路径保持，不能证明C咨询质量。

| 同24段固定决定 | 原V2.1 guard关闭 | 原V2.1 guard开启 | 新C guard开启 |
| --- | ---: | ---: | ---: |
| 满足冻结咨询事实清单 | 0/24 | 0/24 | 17/24 |
| 冻结整题条件全通过 | 0/24 | 0/24 | 14/24 |
| 固定“退款已到账”错误可见帧 | 18 | 0 | 0 |
| 新申请写入 | 0 | 0 | 0 |
| 假模型调用（不含上下文探针） | 46 | 45 | 45 |
| 下一轮平均输入字符 | 见关闭组 | 7301.33 | 7883.58 |

[关闭](paired_off/summary.json)、[旧开启](paired_before/summary.json)、[最终开启](final_pair/summary.json)。
增加 **582.25字符/后续轮**；没有真实Token或费用测量。相同45次脚本模型调用，新增0模型调用。
关闭组多1次源于未知写继续执行，不声称C节省了调用。
受控的正确短答与只读保护断言通过；不能据宽泛0→14称自然模型成功率提升。
计时受并行测试/WSL影响，单轮观测不是性能因果证据或延迟SLA。

## 失败与覆盖边界

冻结评分没有修改。C03正例仍失败：原取消授权协议只接受独立明确句，
“中止退货草稿，同时说明退货政策”被原后端拒绝；新C只补咨询，不放宽取消权限。
这是已有误拦截，不是候选新增回归。C06响应丢失与C12模型异常保留state_uncertain/llm_error，
即使资料可展示仍不计整题完成。错商品、缺资料、伪造来源及原只读短答缺所需全部参数也保留未完成。

| 规划覆盖点 | 本次真实证据与剩余 |
| --- | --- |
| C01/03 | 草稿与政策、否定取消通过；明确混合取消仍被原授权拒绝 |
| C02 | 新增生命周期覆盖跨轮商品咨询更正、重载；不是通用跨SKU语义理解 |
| C04 | 缺材料+新增ready预览通过；未冒充申请 |
| C05 | 新增页面等价ReturnService显式确认→状态查询→重复确认无多写；只证明状态安全/同步，非状态咨询完整性 |
| C06 | 写后响应丢失保留未知/真实状态，不重放；不把停止记成功 |
| C07 | 错用户与原回执对象/revision测试沿用；本24段没有新增Router拒绝/旧revision混合全覆盖 |
| C08 | 政策缺失/伪造及商品缺失独立退出；全部双向缺资料组合尚未穷尽 |
| C09 | 多商品来源绑定；订单事实咨询及跨订单组合本次未实现，保守沿用旧摘要 |
| C10 | 正确短答/纯操作不被新机制无谓追加；不保证覆盖任意自由表达 |
| C11 | 伪造hash、部分结果、孤立结果和错误原答；不是通用自然语言事实审查 |
| C12 | 新增实际部分流后超时、generator关闭写后恢复；最终历史/Trace一致 |

整段KB原文可能冗长；引用是政策资料，包含店铺入口说明，不代表本页已实现所有入口或已办理。
实际办理仍以本应用退货区域/明确确认/真实申请回执为准。此方案不输出自由文本政策摘要，
也没有为复杂表达建立通用事实账本。限制明确后，窄范围咨询保留具有离线净收益；**不默认启用**。

## D的独立真实评测准备

[8个新写开发任务](proposed_live_cases_v2.json)、
[V2预演](pairs_preflight_v2/summary.json)、[V2.1预演](pairs_preflight_v21/summary.json)：
各8段完整ChatService断网接线通过，0真实请求，非自然模型成功率。
v1短答占位名曾与SQL不符，在任何评分/付费前改为SQL名称；旧预演保留，不算Agent修复。
正式配对仍需独立预算与最后SHA/模型/最大输出/逐请求费用协议批准，不挪用A的0.159808元。
当前仅建议最多1元16段一次、不自动重复、不付费故障注入；预算不保证能完成。

## 复现与回退

```bash
uv run pytest tests/test_mixed_write_answers.py tests/test_consultation_edges.py tests/test_mixed_answer_lifecycle.py tests/test_v21_write_evidence.py
uv run python scripts/eval_mixed_answers.py --snapshot results/v21_mixed_answers_20261022/business.sql --out /tmp/mixed-c-new
uv run python scripts/preflight_v21_pairs.py --runtime-root . --cases results/v21_mixed_answers_20261022/proposed_live_cases_v2.json --snapshot results/v21_mixed_answers_20261022/business.sql --out /tmp/pairs-c-new
```

所有输出必须新目录。关闭write_receipt_guard恢复原默认行为；也可revert本完整提交。
没有数据库迁移；回退代码不能撤销实际已创建的申请，仍由原ReturnService管理。
本次不开启P1/P2新框架，不修改原102段或119段官方结果，不宣称生产可靠。


## 后续评分覆盖复核

集成阶段发现本报告“严格整题”是冻结安全契约（含预期拒绝/空状态反例），
**不是原用户任务完成率**；C05-False只有政策而没有P001，也可能满足旧事实清单。
所有原冻结评分保留，17/24仅表示清单事实满足，14/24仅表示契约通过，不作用户完整性收益宣传。
[具体审计](../v21_integration_20261022/offline_grading_audit.json)及
[真实8题结果4/8→3/8](../../docs/TAU_V21_STAGE_ACCEPTANCE_20261022.md)为后续验收依据。
