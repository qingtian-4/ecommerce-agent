# 电商客服 Agent 恢复验收

范围A：原始V2恢复，基线462569b，独立分支fix/tau-formal-recovery。零付费请求。
完整设计、待批准18段及准确命令：[TAU_FORMAL_RECOVERY_AND_V21_PLAN](../../docs/TAU_FORMAL_RECOVERY_AND_V21_PLAN.md)。

- 原102段及#1668逐项校验，继承32.782968元，余额7.217032元，后续从1669计数。
- [120段状态表](episode_status.csv)：completed 102、started_unsettled 1、not_started 17，完成文件附SHA。
- [acceptance_dry_run](acceptance_dry_run/summary.json)：18/18脚本接线，0新请求，金额不变。
- [监督器真实退出](acceptance_supervisor.json)：tmux实际运行，exit_code=0；心跳及单请求账本在补充目录。
- [恢复专项](recovery_accepted.log)：33通过，含18项恢复/监督/审批/只读路径测试与原预算分析回归。
- [完整离线](recovery_full_pytest.log)：830通过、3可选模块跳过；新增边界以33项专项及最终新提交CI补验，
  不把旧日志数量改成新数量。Ruff通过，密钥与历史哈希见最终检查。
- [最终完整离线](full_submission.log)：837项通过、退出码0；使用双quiet，数量可由collection.log及通过标记核对。
- [红测](cache_window_red.log)：请求持久化/缓存未更新真实SIGKILL后误拒绝，修复后通过。
- [重复预算红测](duplicate_output_red.log)、[原目录目的地红测](readonly_destination_red.log)及fixture早期失败均保留。
- [CODE_REVIEW](CODE_REVIEW.md)保存双轴审查及修复状态。
- [preservation_verification](preservation_verification.json)验证全部历史和用户3项修改保持。

APPROVAL_REQUIRED.json保持false，live必须提供精确协议审批及匹配无网络预演，当前没有实际续跑。
如果剩余7.217032元不足，停止并报告缺失，不提高上限或使用其他额度。

脚本模型预演不能与原102段相加为真实120段；仍不发布新的完整40题pass^1/pass^3。
B版V2.1另在fix/runtime-v21-evidence，按真实业务字段和回执控制展示，不能用于补原V2成绩。
