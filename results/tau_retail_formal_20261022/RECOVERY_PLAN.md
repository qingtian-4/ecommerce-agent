# 电商客服 Agent 独立 Recovery 验证

范围冻结于付费正式运行前的 test_tau_recovery_protocol.py（protocol_v6含其哈希）。
正式自然任务不注错，本文件整理已执行结果；不是额外在线实验。自然102段中重试调度0。

| 模式 | 测试入口（tests/test_tau_recovery_protocol.py） | 离线观察 |
| --- | --- | --- |
| 首次模型503 | test_provider_fault_before_content_is_bounded | 开关关闭0重试/失败，开启1重试/完成，无数据库变化 |
| 读工具后429/503 | 同上 after_read=True | 已成功读取不重放；下一请求最多一次重试，状态不变 |
| 不可重试400 | 同上 status=400 | 0重试，不假报完成 |
| 部分流式后异常 | test_partial_stream_is_not_retried_or_reported_complete | 1请求、llm_error、内部最终历史与返回一致；不证明Gradio逐帧保护 |
| 合法空结果 | test_legal_empty_list_is_valid_not_an_error | result=[]，ok，无重试 |
| 官方写成功但回执丢失 | test_successful_official_write_with_lost_receipt_stops_without_replay | 官方任务17地址真实改变一次；注入传输错误后state_uncertain，停止，无第二次写 |
| 相同读取无进展 | test_identical_successful_reads_stop_at_no_progress_limit | 3次实际只读后no_progress停止，无重试 |
| 预算到期 | test_budget_end_prevents_model_call_and_official_tool_execution | 零模型/工具调用，budget_exceeded |
| 实际V1异常类型身份 | test_frozen_v1_budget_exception_uses_its_own_module_identity | 关闭恢复的真正V1也正确识别过期预算 |
| 环境初始化失败 | test_environment_setup_failure_is_unscored_stops_and_preserves_plan | unscored、停止、保存缺失计划，0付费请求 |
| 错协议/参数/ID | 本文件末尾三个定向测试 | 错读取协议停止；写参数绑定错误不误认已写；响应ID不匹配拒绝继续 |

总20项还包括4个英文模糊/混合安全fallback与1个已知SKU空范围限制；不是20项在线故障成功率。
写后未知状态保护在共享Adapter，两ACS组同规；不能全部归功V2 Recovery。
工具可写性按官方__mutates_state__元数据，未知默认保守视为可写；不以本店工具名字猜测官方副作用。

复现（独立环境不发网络请求，未配置模型Key也可）：

```bash
TAU2_DATA_DIR=/tmp/acs-external-tau-20261022/data \
  /tmp/acs-external-tau-env-20261022/bin/python -m pytest \
  tests/test_tau_result_protocol.py tests/test_tau_recovery_protocol.py \
  tests/test_tau_bridge.py tests/test_external_tau_protocol.py tests/test_p1_live_budget.py
```

真实故障实验只作建议：固定已知官方任务17/51/68，7种模式×2臂=42段，写后回执丢失必须用独立
官方环境并在核对副作用后停止，不得回放写操作。粗略按本批平均16.35请求/段约687请求，
保守均值约13.5元（25%规划余量约16.9元），这些不是上界，故障可能改变对话长度。
**未授权、未执行，不使用正式40元剩余额度。** 若要量化Recovery独立贡献，应在相同故障位置与
决策脚本下只切换Recovery，然后另做小规模真实请求；不能混入自然任务的官方成绩。
