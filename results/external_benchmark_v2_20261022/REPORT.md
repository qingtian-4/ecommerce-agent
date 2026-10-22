# 电商客服 Agent 外部评测交付

2026-10-22；V1=f34981f、V2=f6c3387；独立分支feat/external-benchmark-v2。
[完整技术报告](../../docs/EXTERNAL_BENCHMARK_V2_REPORT.md)给出源码、选择矩阵、风险和预算。

## 真实结果与结论

固定官方 Retail test 的17/51/68，各三臂一次，无额外重试或扩大规模。
Official/V1/V2均3/3，九段DB检查正确，task68官方NL裁判三次通过。
132实际请求=78 Agent+51模拟用户+3裁判，503,612 Token；
保守记账2.126384元，配置价估算0.191793元（不是账单），低于用户单独5元硬限额。
各臂调用46/44/42；全部官方工具调用各16次，错误各1次是任务预设错误ZIP查询。
两道写入任务每段仅一次业务写，前面均有官方模拟用户明确确认，详见[pilot_write_review](pilot_write_review.json)。
三臂初始DB hash相同且task initial_state为空，执行状态隔离。

**没有自然质量提升证据**：V2路由13/13 fallback、重试0；五次invalid_result是本地结构
分类对合法外部结果的误报。本轮不能测到ChatService、本店SQL/RAG/UI、四保护或崩溃恢复。
三题曾在历史完整运行中出现，不当作新留出；不能计算pass^3、不与旧版本分数相加。

Go：独立评测接线、固定Pilot、透明报告与实习讲解材料交付。
No-Go：一般成功率提升、默认启用或未经授权正式扩评。
40题×三臂×三次的[建议](formal_proposal_not_executed.json)未运行，仍需新预算授权。

## 验证与证据索引

- `bridge_real_red.log`：3失败/1通过；`bridge_final_green.log`：4通过。
- `preflight_v1/`：无网络9段自然回放、漏写失败、受控503 V1失败/V2恢复成功；零付费。
  gold动作/固定裁判只验证接线，不作模型成绩。
- `pilot_v1/`：真实完整轨迹、原始模型请求/返回、官方reward细项、逐请求usage和费用。
- `freeze.json`：任务开始前1097份历史结果及三项用户WIP哈希。
- `protocol_v1.json`：本次实际源码/上游/模型/任务/预算/包版本冻结。
- `external-requirements.txt`：隔离评测环境实际依赖，应用uv.lock不变；`TAU_LICENSE.txt`为官方任务和政策的MIT声明。
- `budget_final.log`：9通过，覆盖共用账本、请求前拦截和usage未知停止。
- `external_tests.log`：13项实际运行，含4项τ桥接，不是可选依赖跳过的空绿。
- `pytest_full.log`：810通过/1可选依赖跳过/4 warnings，557.25秒，采集于新测试文件创建前。
- `pytest_clean_worktree.log`：最终独立工作目录全套回归，**813 passed、1 skipped、4 warnings，624.73秒**。
- `ruff_final.log`：Ruff通过；密钥检查与完整性检查见最终验收文件。

保留的非成功记录：`bridge_red.log`是环境缺依赖而跳过，不能算红测；`bridge_green.log`
是第一次修复遗漏（1失败）随后修正。`install_external.log/install_offline.log`分别为网络/
缓存安装失败，`install_mirror.log`才是成功安装；`ruff_initial.log`为导入排序问题，已修正。
`protocol_initial.json`是未使用的初版冻结，只有protocol_v1用于正式预演和Pilot。
`pytest_isolated.log`首次从原cwd指定worktree目录，随后主动中断，以真正clean cwd的
`pytest_clean_worktree.log`替代，不能当完整验收；中断日志保留。

所有产品源码、权限/确认/事务/幂等、实验默认false和历史实验均不变；
已有用户TODO、skills-lock、Zone.Identifier不纳入本次提交。PR4仍由维护者单独决定合并。
