# Development Pilot：统一 Episode 预算与真实 DeepSeek

**结论：No-Go。预算和费用基础通过验证，真实配对任务未全部完成。** 原始失败保留，没有重跑、改题、改评分器或启动大规模 Benchmark。本次没有替换默认 Agent，也没有迁移退货。

## 实际官方 Scorecard

| 官方任务 | Runtime | 状态 | Task Reward | DB Reward | 真实模型请求 Agent/User/Judge | 工具真实执行 | 墙钟秒 | 输入/输出 Token | 非峰时估算 CNY |
| --- | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: |
| 33 | 旧 Loop + receipt_guard | user_stop，已评价 | 1 | 1 | 7 / 4 / 0 | 6 | 19.233 | 45824 / 1059 | 0.008664800 |
| 33 | LangGraph V3 | adapter_protocol_error，agent_stop | 0 | 0 | 4 / 2 / 0 | 2 | 14.000 | 21760 / 511 | 0.003733600 |
| 34 | 旧 Loop + receipt_guard | 前一 Episode 错误后未启动 | 未评价 | 未评价 | 0 / 0 / 0 | 0 | 不适用 | 未请求 | 0 |
| 34 | LangGraph V3 | 前一 Episode 错误后未启动 | 未评价 | 未评价 | 0 / 0 / 0 | 0 | 不适用 | 未请求 | 0 |

原始官方结果在 [scorecard.json](scorecard.json)，完整官方消息与工具回执在两个 `.trace.json.gz` 中。任务 33/34 评价依据均仅 DB；COMMUNICATE/NL_ASSERTION 不适用，Judge 真实调用为 0。没有 pass^k 或额外综合成功率。33/34 已用于开发，以上均为 Development Pilot，不能声称未见泛化或 Judge 准确性。

## 请求、Token 与费用

| 角色 | HTTP 尝试 | 输入 Token | 输出 Token | 缓存命中 Token | HTTP 延迟合计秒 | 非峰时估算 CNY |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Agent | 11 | 63010 | 1085 | 59392 | 12.617 | 0.009145840 |
| User Simulator | 6 | 4574 | 485 | 3328 | 7.285 | 0.003252560 |
| Judge | 0 | 0 | 0 | 0 | 0 | 0 |
| 合计 | 17 | 67584 | 1570 | 62720 | 19.903 | 0.012398400 |

全场保守不可退款预留 **1.372904 CNY**，低于 5 CNY；17 次付费请求低于 48 次。17 次请求均收到 usage，无未知计费请求。峰时估算为 0.02479680 CNY；实际运行时间属于非峰时，按已核实官方人民币价格估算为 **0.0123984 CNY（约 1.24 分）**。

这些是实际请求 usage 对应的估算，供应商逐请求账单未取得。[余额差额快照](balance_delta.json) 为 0.00，但余额精度与更新延迟不能证明免费，也不替代账单。所有实际调用均占预留和请求数；失败模型响应的 5355 输入、139 输出 Token 也已计入。账本与价格核实见 [ledger.jsonl](ledger.jsonl)、[price_review.json](price_review.json)、[price_preflight.json](price_preflight.json)。没有使用旧实验的 Token 或费用。

## 失败事实及取证限制

V3 在任务 33 第四个模型请求之后、已经真实执行两次只读工具之后停止。`find_user_id_by_name_zip` 和 `get_user_details` 的回执均正常。Graph 的业务写次数为 0，其官方 DB 终态与初态相同。旧 Loop 执行 6 个工具，其中 `modify_user_address` 写入一次；全场共 8 次工具执行、8 次返回、8 次接收，无重复执行 ID。

失败 checkpoint 的 `__error__` 为：

```text
IncompleteModelResponse('Partial, malformed or mixed model response')
```

该异常来自 `scripts/tau_langgraph_agent.py:model_boundary`，可能是 finish_reason、invalid_tool_calls 或文本与工具混合输出之一；它不是工具错误或 Episode/模型预算耗尽。两个已执行 Episode 初始官方 DB hash 相同；当前源码与冻结清单相同，上游源码与数据仍未改动。

**具体拒绝谓词尚不能确认。** 账本有请求体 hash、usage、耗时和完成状态，但当前 Callback 没有保存被拒绝的模型原始输出，Graph 在守卫抛错前也没有把该输出纳入 messages checkpoint。因此官方会话原始 Trace、错误 checkpoint 和真实回执已保留，被拒绝的供应商响应正文存在取证缺口，不能伪造补齐。

[失败证据](failure_evidence.json) 和 `33-langgraph.failed-checkpoint.sqlite.gz` 保留原始异常及可恢复的 Graph 文件；该文件不是官方业务 DB 的原子恢复镜像。没有发额外付费请求复现，也没有修改守卫去制造成功。

另做了一次阻断全部网络的固定决策诊断：[offline_guard_gap.json](offline_guard_gap.json)。同一个完整“文本＋只读工具调用”响应，旧 Loop 执行 1 次读取，V3 执行 0 次并停止，原配对断言失败。这证明目前响应守卫的跨臂合同存在缺口，**不能证明真实失败恰好就是混合输出**，也不是新的模型成绩。完整诊断日志留在 `intermediate/`。必须先对齐共同的接受/停止规则，再进行另行授权的真实 Pilot。

## 已执行验证

- `uv sync --extra dev --extra v3 --extra tau --frozen`，Python 3.13.12，沿用锁定依赖。
- 完整 Pytest：**810 passed，0 skipped，12 warnings，732.73 秒**；其中新增 48 项 Episode 合同测试。12 项为已有 Pandas/多线程 fork 警告，不代表 Windows 兼容性验收。
- 新测试通过真实 OpenAI SDK、ChatOpenAI Callback、官方 LiteLLM User/Judge 的 MockTransport 路径验证请求前拦截、缓存 Token、失败非免费、截止时间、进程重开、重复回执、未知写和最终输出费用恢复。
- 固定 SDK 决策在真实官方任务 33、34 环境执行四个离线 Episode，断言两臂 DB、官方 Reward（各 1）和模型输入语义一致。这部分付费请求为 0，仅为协议验证。
- Ruff、全仓密钥扫描、冻结源码指纹与原始 τ² 源码/数据比对通过。原始 API key 不出现在新实验文本、压缩 Trace 或失败 checkpoint 中。
- 真实 native Callback 的 4 次请求记录与 HTTP 账本一致，输入 20518、输出 305 Token，已导入官方 AssistantMessage usage；User cost 元数据从同一账本导出，Reward 保持原样。

810 项绿灯未覆盖所有跨臂异常响应，不能否认新增离线诊断暴露的合同缺口。CI 在本分支推送后执行，最终交付提供具体 SHA 与 Actions 链接；不能把基线 CI 当作本次 CI。

证据索引：[门禁与源码指纹](gates.json)、[冻结实验清单](manifest.json)、[可比性审计](comparability_audit.json)。完整 Pytest/JUnit/检查日志在本工作区 `intermediate/checks/`，CI JUnit 作为 `offline-check-evidence` Artifact 保存 14 天；大体积中间文件不进入 Git。

## 本次运行和停止条件

工作区 `/tmp/ecommerce-v3-episode-pilot-20261023`，分支 `feat/v3-episode-deepseek-pilot`，基线 `d7da103`。源码入口：`scripts/eval_tau_development_pilot.py`。本次通过同一 `run()` 入口外加免费余额前后快照执行，不增加模型请求。

```bash
uv run python scripts/eval_tau_development_pilot.py \
  --upstream /tmp/acs-external-tau-20261022 \
  --out results/tau_development_pilot_20261023 --freeze
# 源码/价格和离线 gates 校验通过后，真实执行入口如下：
uv run python scripts/eval_tau_development_pilot.py \
  --upstream /tmp/acs-external-tau-20261022 \
  --out results/tau_development_pilot_20261023 --run \
  --env-file '/home/qingtian/projects/E-commerce Agent/.env'
```

上面的旧结果目录已经存在 `RUN_STARTED`，重复执行会被拒绝。复现实验应先获得相应授权、使用新目录，并保留本次失败；门禁重建命令见[源码与恢复报告](../../docs/v3/EPISODE_BUDGET_PILOT_REPORT.md)。不会复制 `.env`。官方初态来自固定 `dd8757d` checkout 的原始 Retail 数据；本地 CustomerService SQLite 不参与。

每 Episode 24 次 HTTP、24 个工具意图、80 步、单模型 60 秒、Episode 300 秒；全场 5 CNY/48 次先到即停止，未知费用保留预留。额外冻结了错误后停止规则，因此本次剩余两个 Episode 没有自动启动。真实费用及预算不会因新进程而重置；`RUN_STARTED` 不是完整 Orchestrator 恢复功能。

## 五项结论与下一步

1. 真实旧 Loop 在任务 33 完成并获得官方 DB/Task Reward 1；V3 未完成，任务 34 未执行，不能说两组都跑通。
2. V3 实测被模型协议守卫停止；离线另证实响应接受规则有差异，尚不能确认该差异就是实测失败的具体原因。不能解释为模型能力优劣，且只有一个实际配对任务，没有独立重复结果。
3. LangGraph 的消息持久化、最终 outbox 费用恢复和禁止本地 ToolNode 执行业务工具通过测试；统一预算是两组共享收益。额外成本是 checkpoint I/O、事件循环和协议边界维护。当前数据不能支持效率或成功率提升。
4. **暂缓本地退货迁移。** 先关闭响应守卫和拒绝输出取证阻塞；后续隔离草稿、预览及显式确认计划见 [LOCAL_RETURN_MIGRATION_PLAN.md](../../docs/v3/LOCAL_RETURN_MIGRATION_PLAN.md)，本轮没有实施。
5. 新的评测阻塞是模型响应接受条件未完全一致、拒绝输出缺少原始正文；官方 DB 与 Graph/Orchestrator 也没有原子恢复。已执行路径未发现重复工具或模型越权写 SQLite。

下一轮最小范围只包括统一两臂模型响应合同、记录完整拒绝原因及原始输出、补该跨臂负例回归，然后另行冻结少量开发期真实验证。独立留出组需先审计全部历史已曝光 ID，不能把 33/34 或已读历史子集包装成未见集。本轮停止，不运行完整 τ Benchmark。
