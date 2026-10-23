# LangGraph 官方 HalfDuplex Adapter 交付

已完成源码、真实官方环境离线执行和回归。默认客服 Agent 未替换，付费请求为零。

- 独立分支：`feat/v3-tau-langgraph-adapter`；基线 `576f56fd3cd13d04244a19fd00c8419f34808bed`。
- 上游：`dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd`，tau2 1.0.1；源码和原始 Retail 数据未改。
- 本地完整 Pytest：**762 passed、0 skipped、0 failed**，810.63 秒，9 个 warning。
- 官方 Adapter 合同：**42 passed、0 skipped、0 failed**；14 组新旧固定轨迹的完整模型输入、调用、DB、Reward 和官方终止状态一致。
- 实际官方任务编号：**33、34**。各侧每任务执行六个原参考动作，覆盖用户和订单地址更新，正常脚本路径官方 Reward 均为 1。此结果验证协议，不能称为真实模型任务成功率。
- 两个写回执故障注入合同被原严格重放拒绝评分，保留 null Reward 与原拒绝类别，不改评分器。
- 真实 DeepSeek 请求 **0**，真实付费 Token **0**，模型费用 **¥0**。脚本桩 usage／cost 和离线 HTTP 响应 usage 不代表真实消耗。
- Ruff、密钥扫描和 diff check 通过。分支最终 SHA、匹配的远端 CI run 链接在交付回复中提供；不以基线 CI 代替本分支验收。

源码与操作手册见 [TAU_LANGGRAPH_ADAPTER_REPORT.md](../../docs/v3/TAU_LANGGRAPH_ADAPTER_REPORT.md)。

## 复现

在本分支目录、Python 3.13 执行，先准备干净的固定上游 checkout：

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run --no-sync python scripts/check_tau_langgraph_adapter.py \
  --upstream /tmp/acs-external-tau-20261022 --out /tmp/tau-langgraph-recheck
TAU2_DATA_DIR=/tmp/acs-external-tau-20261022/data LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_secrets.py --all-tracked
```

`--out` 必须是新目录。上游完整 clone／checkout 命令和 `thread_id` 恢复示例在操作手册中。
本轮异步验收在沙箱外执行，合同 fixture 仍禁止网络连接；无付费模型客户端实例。
真实 ChatOpenAI 测试使用离线 MockTransport，不访问示例 URL。

## 最终证据

| 文件 | 内容 |
|---|---|
| [validation.json](validation.json) | 完整测试、源码指纹和默认 Runtime 无改动检查 |
| [final_with_http/manifest.json](final_with_http/manifest.json) | 最终合同的上游源码、Retail 数据、依赖和配置冻结 |
| [final_with_http/pytest.log](final_with_http/pytest.log) / [junit.xml](final_with_http/junit.xml) | 42 项实际执行记录，不跳过可选依赖 |
| [final_with_http/summary.json](final_with_http/summary.json) | 14 组官方对照汇总及费用零消费 |
| [final_with_http/paired/](final_with_http/paired/) | 每组完整双方请求、官方回执、DB 哈希及完整 Reward |
| [db_reward_consistency.json](db_reward_consistency.json) | 14 组完整 Reward、DB、请求、执行次数／顺序及终止状态逐项相等 |
| [full_pytest_final.log](full_pytest_final.log) / [full_pytest_final.xml](full_pytest_final.xml) | 最终 762 项完整回归 |
| [ruff_final.log](ruff_final.log) / [secrets_final.log](secrets_final.log) | 静态检查和完整 tracked 文件密钥扫描 |
| [tested_source.json](tested_source.json) | 验证过的 Runtime、合同、CLI、CI 和依赖文件哈希 |
| [freeze.json](freeze.json) / [preservation.json](preservation.json) | 原用户修改与旧评测文件保留证明 |
| [dependencies.json](dependencies.json) | 锁文件新增 28 项、原 96 项版本未改、Python 未升级 |
| [review.json](review.json) | 架构、安全、四角审查及修复与边界 |
| [costs.json](costs.json) / [pilot_proposal.json](pilot_proposal.json) | 本轮零费用与未执行的后续 Pilot 建议 |

`final_with_http` 是最终实现的完整合同证据；`acceptance*` 和 `final` 为开发过程记录。
中间重复的 paired JSON 已删除，保留相应日志、manifest 和 summary，不用旧记录替代最终验收。

## 失败与修复

| 记录 | 结果与处理 |
|---|---|
| contracts_outside_sandbox.log | 官方工具 schema 被默认转换删减；middleware 改为绑定原始完整 schema |
| contracts_iteration2.log | 空工具消息的内存表示不同；改为比较真正 wire 格式 |
| contracts_iteration3.log | 批量 native tools 节点识别和短 Orchestrator 超时；修复批次判断、保留独立超时合同 |
| contracts_iteration5.log | 合成不确定回执触发官方严格重放拒绝；保存拒绝、null Reward，未放宽评价 |
| acceptance/pytest.log、user_simulator_diagnostic.log | 用户模型输入比较误含时间戳；改用官方模型消息序列化 |
| http_loop_red.log / http_loop_green.log | 真实 ChatOpenAI 离线 transport 复现跨循环问题，复用 Runner 后通过 |
| diagnostic.log、import_diagnostic.log | 沙箱异步 SQLite 唤醒诊断；沙箱外断网测试完成 |
| full_pytest.log | HTTP 生命周期修复时中断旧全量回归，564 passed；最终版本已重新完整跑 762 项 |

风险回执与停止原因原子保存、原生回执投影、最终回答交付窗口均由退出恢复测试验证。
完整 JUnit 的测试用例名包含密钥扫描器故意使用的固定仿真密钥，提交钩子曾拦截；
仅精确替换这些已知夹具并规范化 JUnit 行尾空白，原始运行日志、官方请求和 Reward 未改。
[report_redaction.json](report_redaction.json) 记录处理范围，测试数量与断言结果复核不变。

五个 `os.fork` 测试产生 Python 3.13 多线程 fork 警告，未屏蔽；另外四个 warning 来自已有依赖。
当前只在 Linux／WSL 验收。Windows 原生、spawn／exec 和分布式 worker 未验证。

## Go / No-Go

**离线 Retail 协议验收：Go。立即付费 Pilot、生产替换：No-Go。**

候选预算按 episode 累计、旧 Loop 按用户回合重置；候选的官方 usage／cost 导出尚未接入。
下一轮只统一外层 episode 预算、接通 Agent／User／Judge 共用账本并补充多回合合同，
再冻结模型及报价、明确授权小额 Pilot。预算建议为 5 元／48 个总付费请求，当前未执行。

只支持空或完整配对的初始历史；官方带未完成工具调用的初始化路径尚不支持。
恢复须有同一 checkpoint、匹配配置、外部官方 DB 快照和真实回执，Graph 不能证明业务写成功。
同一 Adapter 拥有其模型客户端生命周期；stop 后用新模型、新实例恢复，不共享客户端。

未合并 main、未更新 PR #4、未迁移完整退货、未实现换货／React／生产服务或运行全量 τ Benchmark。
