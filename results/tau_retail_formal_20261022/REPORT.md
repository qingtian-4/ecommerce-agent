# 电商客服 Agent 正式 Retail 执行记录

本轮是正式40题、三组一次的协议及实际运行，**因环境/进程中断，仅完成102/120段**。
103段已开始，包含V1/task97无最终结果；17段未开始。最后请求usage未知，停止继续付费。
未重跑、未补成功、未新增组或重复次数。完整结论见
[正式报告](../../docs/EXTERNAL_BENCHMARK_V2_FORMAL_REPORT.md)。

## 主要结果

- 同34题：Official/V1/V2各30/34；不报告完整40题pass^1或新pass^3。
- V1/V2无成功题差异，但task39失败根因不同，V2有SKU引发空工具范围与虚报写入。
- V2路由173次，171次fallback、2次空范围；没有有效非空工具收窄，Recovery自然触发0。
- V2较V1输入Token+5.96%、请求+4.44%；不支持V2更强或更省的结论。
- 全角色1668请求记录，1667已知usage，共7,692,544 Token；已结算保守32.619652元，
  最后未知请求全预留0.163316，合计32.782968元。已知usage配置价估算3.032677元，非账单。
- 协议修复仅Adapter，生产src/acs无变化；四实验开关和V2默认配置不变。

## 索引

| 文件 | 内容 |
| --- | --- |
| [ANALYSIS_PROTOCOL](ANALYSIS_PROTOCOL.md) | 付费前冻结分析契约、停止规则、口径 |
| [protocol_v6](protocol_v6.json) | 实际运行唯一协议，源码、依赖、官方工具schema与任务哈希 |
| [preflight_v6/summary](preflight_v6/summary.json) | 全120脚本接线+负向漏写+503正反例，valid=true、零网络 |
| [live/manifest](live/manifest.json) | 真实运行配置，和v6协议同条件 |
| [live/requests](live/requests.json) | 全部请求、原始响应/流、usage、预留、角色；最后记录未结算 |
| [live/attempts](live/attempts.jsonl) | 每段执行前先保存开始记录，103条 |
| [live/analysis](live/analysis.json) | 实际完成、缺失、未知用量、分组指标、建议预算；非原始评分覆盖 |
| [live/task_comparison](live/task_comparison.csv) | 全40题各组结果与状态；97/V1为interrupted_unscored |
| [live/badcase_index](live/badcase_index.json) | 所有12个失败的诊断证据标签，不改reward |
| [badcase_source_review](badcase_source_review.json) | 27/38/39/56/79三臂原始动作、错误回执、根因与限制 |
| [score_components](score_components.json) | 官方DB/NL/Action各项计数 |
| [INTERRUPTION](INTERRUPTION.json) | 退出原因未知、预算全预留、原始账本/已完成结果哈希 |
| [protocol_replay_cases](protocol_replay_cases.json) | Pilot五次合法结果误报逐例原文及来源 |
| [protocol_semantic_red](protocol_semantic_red.log) | 修复前语义红测13失败1通过 |
| [v1_budget_identity_red](v1_budget_identity_red.log) | 独立V1过期预算被误标llm_error红测 |
| [adapter_final_v6](adapter_final_v6.log) | 真实可选τ环境专项49通过 |
| [RECOVERY_PLAN](RECOVERY_PLAN.md) | 已执行离线故障、未执行付费故障建议，独立于官方自然成绩 |
| [pytest_final](pytest_final.log) | 最终完整本地离线回归，实际数见文件末尾 |
| [analysis_final_tests_v2](analysis_final_tests_v2.log) | 分析器4项回归含中断/原始reward不可冒充成功 |
| [ruff_final](ruff_final.log) | 完整Ruff结果 |
| [preservation](preservation.json) | 原1166份结果及用户3项修改冻结哈希 |

v1–v5协议、预演和初次失败均为历史调试证据，未付费；不可当成多次正式尝试。
初次protocol_red是fixture设置错误；真正红绿用protocol_semantic_red。
preflight_v4因格式变化源码哈希过期被拒绝，重新冻结后v6才进入付费，没有绕过一致性检查。
没有制造summary.json假装原进程正常收尾；中断状态另存INTERRUPTION和派生analysis。

## 最终工程检查

完整离线运行exit=0，819项通过；3个可选τ模块在主环境跳过，
[collection_final](collection_final.log)列明跳过原因。pytest命令的-q与仓库addopts=-q叠加，
该日志未打印末尾总数，819由成功进度点数与最终collection交叉核实，见[verification](verification.json)。
可选τ独立环境另跑49项全部通过，见[adapter_verified](adapter_verified.log)。
Ruff通过；冻结源码、官方工具数据、1166份历史和3项用户工作哈希均未变化。
原始ACS流记录聚合raw_response_text、工具参数/回执、provider_usage，不是逐网络数据包抓取；
最后未完成请求没有保存响应/usage，不能声称该段轨迹完整。

## 可复现命令

依赖先按主仓uv.lock安装。外部环境版本见manifest.packages及
[前轮补充依赖](../external_benchmark_v2_20261022/external-requirements.txt)，上游固定dd8757d4。
原始V1必须独立归档f34981f/src；src/acs必须与f6c3387逐字节一致；工具/评分/DB保持上游原样。
临时环境不是repo依赖，普通pytest缺可选τ时按预期跳过。

```bash
# 仅重分析已保存资料，零网络/零费用；不会改变原始官方reward
uv run python scripts/analyze_tau_v2.py results/tau_retail_formal_20261022/live

# 全本地离线回归与代码规范
uv run pytest
uv run ruff check .

# 原付费执行命令（记录用途；不要重新执行，不支持原目录覆盖/自动续跑）
# TAU2_DATA_DIR=/tmp/acs-external-tau-20261022/data \
# /tmp/acs-external-tau-env-20261022/bin/python scripts/eval_tau_v2.py \
#   --upstream /tmp/acs-external-tau-20261022 \
#   --v1-root /tmp/acs-external-v1-20261022 \
#   --protocol results/tau_retail_formal_20261022/protocol_v6.json \
#   --out results/tau_retail_formal_20261022/live \
#   --preflight results/tau_retail_formal_20261022/preflight_v6 --live
```

若以后重建预演，使用新输出目录且验证v6所有冻结源码/依赖，命令与上述相同但去掉--live和--preflight。
本轮后处理Analyzer不在模型执行源码冻结范围；其4项测试保护缺失题不冒充成功。
回退本轮单个提交可撤销Adapter/runner/分析改动，不涉及业务库迁移；不删除冻结原始证据。
不合并PR #4/main，不更改可见性。正式完整性验收No-Go，停止付费与无边界开发。
