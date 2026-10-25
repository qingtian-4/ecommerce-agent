# M8.1 真实 Pilot 准备

本轮只进行无网络验收，DeepSeek 付费请求 **0**、费用 **0 元**。新合同为
`m8.1-open-answer-v3`，继承 M8 v2 已见任务；不能据此宣称模型质量改善。
基线 `2c794c7` 的 v1/v2 数据与历史实验目录全部保留，前后哈希见
`results/m8_1_readiness_20261025`。

## 实现与运行

- `src/acs/live_evaluation.py`：开放回答状态、权威业务回执核验、配对调度、HTTP 前检查。
- `scripts/eval_live_readiness.py`：复用原 M8 `run_task`；两个 Runtime 使用相同 SDK、
  Prompt 投影、工具 Schema、SQLite 初态与上下文边界。没有复制业务提交逻辑。
- `data/eval/v3_multiturn_m8_1_v3/manifest.json`：新实现与继承输入的 SHA-256。
- `tests/test_live_evaluation.py`：真实 SDK MockTransport、合成 SQLite、事务故障及待审反例。

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/eval_live_readiness.py --mode pilot-plan --out /tmp/proposal-only
uv run python scripts/eval_live_readiness.py --mode rehearsal --out /tmp/m81-new-fixed
uv run python scripts/eval_live_readiness.py --mode rehearsal-open --out /tmp/m81-new-open
uv run python scripts/eval_live_readiness.py --mode inspect --out /tmp/m81-new-open
```

输出目录必须不存在；rehearsal 在进程内阻断 socket.connect，只允许真实 SDK 调用
MockTransport。它验证序列化、回调和实际业务执行，不证明 DeepSeek 能完成任务。
原始请求、响应、DB、Graph 和审查队列均放在被 Git 忽略的 `.private-evidence/`。
公开 Scorecard 只含结构化指标。inspect 只读，不重置账本、不重放未完成臂。

## 新 Pilot 边界：提案，不是授权

| 项目 | 限制 |
|---|---|
| 顺序 | D02 old → D02 V3 → D04 old → D04 V3 |
| 数据 | 原 D02 多候选澄清、D04 混合咨询与草稿写入；已见开发题 |
| 模型 | 经重新核价的 direct DeepSeek / deepseek-flash、temperature=0、非 thinking |
| 请求 | 每 Episode 8 次 HTTP，整轮最多 40 次；失败尝试也计数 |
| 时间 | 模型 60 秒，Episode 600 秒；另受授权到期时间约束 |
| 上下文 | 相同请求级事实投影；完整 UTF-8 HTTP 正文 ≤32,768 字节 |
| 输出 | 每请求最多 1,024 Token；不重试失败请求或失败臂 |
| 工具 | 外层每 Episode 24；原两臂共同的内部每 turn 16 次保护保持一致 |
| 总费用 | ≤5 元保守累计预留，不释放未知费用，先到任一限制即停止 |

原 20,000 字节限制会拦截本轮已知合法固定链路，实测正文最大约 24.8 KB。
新上限不是无限上下文；真实模型若构造更长请求，仍会在 HTTP 前停止，不能承诺完成。
不删除历史或工具回执来适配长度。

按既有账本的峰值非缓存价格假设：输入 2 元/百万、输出 8 元/百万。**这不是本轮实时
价格核验**，未来付费运行仍必须保存并复核官方 CNY/USD 页面与 tariff 完全一致。

```
输入上界 = 2 × 32768 + 4096 = 69632 Token
单次预留 = (69632 × 2 + 1024 × 8) / 1000000 = 0.147456 元
4 Episode × 8 次 = 32 次，最大累计预留 4.718592 元
40 次同样上界 = 5.898240 元，因此费用门禁可能先截停
```

真实序列化包含 tools、messages 和模型配置；费用按实际字节重新预留，不假设缓存命中。
无网络 SDK 用量是注入的测试 usage，不是真实 Token 或费用观测。

## 授权与停止

live 模式必须提供新的授权文件，绑定 `authorized=true`、原用户指令出处、完整
`source_sha`、`contract`、`task_ids=[D02,D04]`、`arms=[old,v3]`、唯一 `run_id`、
绝对 `output_dir`、带时区 `expires_at`、`cap_cny≤5`、`max_requests≤40`。
该文件属于受信任本地操作输入，不是生产授权系统。旧 M7/M8 授权不能继承。
同时要求干净且冻结的源码、经复核的 `--price-review` 和显式 `--env-file`。
本轮没有创建有效付费授权文件，没有执行 live。

普通任务失败、被安全阻止的错误工具选择、已知授权拒绝：保存该臂，继续同题另一臂。
单 Episode 请求/截止时间耗尽：该臂 execution_error，若全局门禁仍有效可执行另一臂。
未知业务写入、实际越权副作用、不可核实协议/费用、全局费用/请求上限、授权到期：
全局停止。已知完整 unknown_tool 响应、usage 可核实且没有写入，属于安全工具选择失败。

Graph 和工具意图日志不证明业务成功。写入出错后只读核验 `local_receipts`；提交后回执
丢失而权威记录存在时不重放，提交前没有权威记录时停止。DB 无法读取同样停止。
正式申请成功仍依赖原 M6 完整回执。共同能力 Pilot 不开放正式申请提交。

配对分析只包含两臂执行完整的 pair；有 execution_error 或缺臂的 pair 为 incomplete。
语义待审的完整 pair 另标 pending，不进入 resolved 配对分母。报告保留全部计划和实际
Episode、四种结果数量、待审项数，不把 null 成功字段换成 false。

进程退出后，只允许 inspect。持久化 arm_started 防止重新执行失败臂；授权输出路径绑定
阻止换目录重置同一许可。原 EpisodeLedger 的 POSIX 文件锁、截止时间与保守预留保持。
验收目标仅 Linux/WSL 单 Worker。

结论：离线门禁通过后，**具备申请新授权并核价后执行两题 Pilot 的条件**。本轮真实模型
对照及独立语义质量结论均 No-Go；授权前不得调用付费接口。
