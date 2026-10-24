# M6 故障与恢复分析

所有用例使用真实合成 SQLite 和原生 LangGraph。进程退出注入使用 POSIX `os.fork()` / `os._exit(73)`，不是 Mock 成功返回。退出前后由新宿主重新打开数据库核对。

| 注入位置或场景 | 权威状态与预期处理 | 专项证据 |
|---|---|---|
| 发放事件事务 COMMIT 前退出 | wait 仍 waiting，无事件、无正式四表；重新明确决定 | `test_real_process_exit_atomic_rollback_or_recovery[before_event_commit]` |
| 事件已发放、消费前退出 | issued、consumptions=0，无申请；只读查无成功后显式 resume | 同上 `[after_event_commit]` |
| 消费确认与创建申请之间退出 | 消费回滚；申请、确认、审计、提交回执均 0 | 同上 `[after_confirmation_consume]` |
| ReturnService 创建后、最终 COMMIT 前退出 | 原业务写入与消费全部回滚 | 同上 `[after_request_create]`、`[before_consume_commit]` |
| COMMIT 成功、回执及 Graph 更新前退出 | 四表各 1，消费 1；只读找回原申请，镜像待对账，不重写 | 同上 `[after_consume_commit]` |
| 审计 INSERT 实际触发 SQL 错误 | 确认、申请、消费及回执均回滚；移除本用例故障后显式恢复 | `test_fault_between_confirmation_and_business_records_rolls_back_and_can_reconcile` |
| 重复确认/HTTP 式请求 | 返回原申请；前后 SQL 快照一致 | `test_native_interrupt_submit_restart_read_real_status_and_repeated_http` |
| 两个会话争同一商品/并发重复点击 | BEGIN IMMEDIATE 串行，数量 Trigger 和唯一约束防重；最多一份申请 | 两个 `test_*concurrent*` 及真实唯一约束测试 |
| 事件发放前或发放后过期、政策变化、更正 | 接受前、消费事务内均重验；旧事件失效，无正式申请 | `test_changed_binding_never_submits`、`test_binding_is_revalidated_after_event_issuance_before_atomic_consume` |
| 跨用户/会话、假 event、伪造 Resume/模型 Tool | 拒绝或重新 interrupt，无业务写入 | `test_forged_resume_event_owner_session_and_model_tool_cannot_submit`、`test_model_forged_submission_tool_is_rejected_by_actual_native_graph` |
| SQL 成功但 Graph 线程/结果/绑定损坏 | 返回 SQL 原申请，标镜像异常；不重放 | `test_successful_database_receipt_survives_damaged_graph_without_replay` |
| 整个 Graph 文件缺失/损坏 | 独立 CLI `lookup` 仍成功，业务 DB 文件 hash 不变 | `test_readonly_cli_recovers_even_when_entire_graph_file_is_unusable` |
| 人为损坏提交回执或申请标量摘要 | 明确 unknown/disagree，任何 resume/decide 都不得补写 | `test_no_partial_success_is_replayed_when_receipt_is_missing`、`test_corrupted_scalar_receipt_cannot_override_actual_request` |
| M5 accepted 旧回执 | 不作为正式授权；必须新 wait、新 event | `test_old_accepted_confirmation_cannot_be_reused_as_submission` |
| 同时间戳多个预览 | 稳定选择最后插入者，旧确认失效 | 两个 `test_latest*same_timestamp*` |

## 本轮发现及保留的失败

1. **基线缺陷**：两处最新预览查询只有 `created_at DESC`，同时间戳返回先插入的旧预览。先运行失败回归，再在 ReturnService 与确认门统一增加 rowid tie-break；不改预览 ID 或时间。
2. **新代码审查缺陷**：SQL 回执已成功但 Graph 的 wait_id 损坏时，镜像状态曾误报 matched。专用回归先红，随后增加绑定比较；不会因此重写业务。
3. **首轮离线 CLI 失败**：`OfflineModel(messages=...)` 不符合当前 SDK 的 `responses` 必填字段，在第一阶段执行前报错。run1 原始 Scorecard 和私有 stderr 均保留；修复构造参数后新建 run2/run3，不覆盖原实验。
4. **测试数据校验错误**：最初数量更正用例试图在购买量为 1 的种子中改为 2，先被既有业务规则拒绝；改用用户撤回数量为 None 来验证合法 revision 更改后旧确认失效，保留原规则。旧 ReturnService 专项继续覆盖多件数量及剩余量限制。
5. 并发测试通过 Barrier 保证两边均完成 waiting 建立后竞争决定，避免把“另一会话已提前失效”的调度差异误当事务错误。应用入口仍只有单 Worker；独立测试连接专门验证数据库并发约束。

## 恢复边界

只读查询成功是业务证据，Graph checkpoint 只是执行镜像。若数据库不可读、回执不全或相互矛盾，停止并保留现场，不能通过重新初始化 Fixture、清空 Graph 或再次提交制造成功。

新门重用既有完整预览绑定、政策和业务规则；文件政策不属于 SQLite 事务，本轮仅校验读取时 hash，不宣称具备跨文件/数据库分布式事务。生产政策版本必须另行冻结及审计。Linux/WSL、fcntl 单宿主锁为验收边界；没有声称 Windows 原生或多服务实例可用。

全仓测试发现首次遗漏 `TAU2_DATA_DIR`，两个官方 τ 测试模块在收集阶段找不到安装包外的 Retail tasks.json。配置 `/tmp/acs-external-tau-20261022/data` 及离线成本表环境后，945项正常收集。这是验证环境错误，不是业务回归；没有修改官方数据或跳过测试。完整执行由最终CI进一步证明。

原有相关97项子集首次有1项在 `from tau_pilot_artifacts` 处失败；基线该测试依赖其他测试收集时加入 scripts 路径。显式 `PYTHONPATH=src:scripts` 后原样重跑该项通过，未修改断言；完整945项CI同样通过。原始失败与修正命令日志保留在私有validation目录。
