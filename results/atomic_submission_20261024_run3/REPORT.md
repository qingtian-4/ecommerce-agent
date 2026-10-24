# M6 最终离线执行记录

- 基线 `47ec9c5a549f10a2f87f6e194d1983da81edf96e`；候选源码在工作区运行，逐文件 SHA256 见 `scorecard.json`。冻结执行前后源码指纹完全一致。最终交付 SHA 由本报告所在提交及最终回复确定，不能把基线 SHA 当作本轮已提交源码。
- 命令：`.venv/bin/python scripts/eval_atomic_submission.py --out results/atomic_submission_20261024_run3`。
- 固定脚本模型，**不是本轮真实模型评测**；30 个新进程 CLI 动作，全部退出码 0。
- 新增付费 HTTP 请求 **0**，费用 **¥0**，付费 Token **0**。

| 场景 | 业务阶段 | 申请 | 正式确认 | created审计 | 提交幂等回执 | 审核 |
|---|---:|---:|---:|---:|---:|---:|
| 接受 | 冻结8阶段全完成 | 1 pending_review | 1 | 1 | 1 | 0 |
| 拒绝 | 独立合成库准备预览 | 0 | 0 | 0 | 0 | 0 |
| 取消 | 独立合成库准备预览 | 0 | 0 | 0 | 0 | 0 |
| 过期 | 独立合成库准备预览 | 0 | 0 | 0 | 0 | 0 |

接受流程经过建立草稿、补信息、商品插问、商品更正、政策插问、进程重启恢复、明确取消、重建最新预览、原生 Interrupt、应用决定、新事件消费及模拟申请创建、只读查询和重复点击。模型工具阶段均未创建正式记录。最终申请真实读取自 SQLite，重复点击前后 SQL 快照完全相同。

`scorecard.json` 的 before/pre_submit/after 保存每条路径的业务记录，event_id、wait_id、preview_id、request_id 可逐一关联。初始业务数据由 `src/acs/db_seed.py` 按 Fixture reference_date 合成，源码指纹已记录；本地 `.private-evidence/` 保存四套原始 Fixture、八个初始/最终 SQLite 备份、完整业务 Trace 与 30 对 CLI stdout/stderr。该目录受限且被 Git 忽略，不复制旧真实客户或模型证据。

专项测试新增 **39 项**，本地按执行时集合分为 32+4+3，全通过；未跳过。32 项主集合耗时 713.71s，额外4项105.05s，事件发放后失效3项72.94s。原有相关97项全部通过（子集初次96通过、1项在脚本导入处失败，显式PYTHONPATH重跑该项通过；完整CI也全部覆盖）。源码提交 `93c3b30` 的完整CI为 **945 passed、0 skipped、0 failures、0 errors**，JUnit总耗时75.904s；39项新专项均在JUnit中逐项核实。见[CI 40054427592](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40054427592)及 `ci_acceptance.json`。下载的JUnit与完整CI日志保留在本地私有validation和CI Artifact，不作为大型生成文件提交。实跑44个源码/依赖文件指纹已与93c3b30逐一核对相同。

故障矩阵见 `docs/v3/FORMAL_SUBMISSION_FAILURE_ANALYSIS.md`，其中六个退出点使用真实 `os._exit(73)`。还覆盖实际审计SQL错误、两会话争用、并发重复点击、事件唯一性、Graph线程/绑定/结果/整文件损坏、缺失及被篡改回执、跨用户会话、伪造Tool和Resume、旧accepted拒绝及同时间戳预览排序。

失败未隐藏：run1在第一个业务阶段前因脚本SDK参数名错误终止，Scorecard保留；run2诊断迭代四路径已通过，run3为最终冻结证据。第一次全仓collection漏配TAU2_DATA_DIR而有两项收集错误；按项目文档设置固定官方数据目录后945项正常收集，不改官方Task或评价器。本轮不运行任何付费Benchmark。

结论：隔离原子提交与恢复合同通过，Go限于下一阶段隔离REST/SSE/确认UI接入；生产办理、默认Runtime切换、审核及退款仍No-Go。源码提交CI已成功；最终证据提交的CI另由最终回复提供。
