# M8.2 执行前检查：未获授权，真实 Pilot 未运行

本轮没有针对 M8.2 的新明确付费授权，按用户门禁停止在离线准备。
**真实模型 HTTP 0 次，实际费用 0 元，四个计划 Episode 均未运行。**
没有生成有效许可文件，没有复用 M7/M8 的历史付费授权，也没有运行 `--mode live` 的模型路径。
保存原始快照的公开价格页 GET 2 次，并经浏览工具阅读官方文档；均不涉及模型 API、
API Key 或计费推理。

## 基线与保存

- 核对的本地及远端完整 SHA：`f3c7df2b90f23a533f0c0f0edc492c8c612c3b1c`。
- [基线 CI](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40187683450)：
  1124 passed、0 skipped，Ruff 与密钥扫描成功。
- 原 Worktree `/tmp/ecommerce-v3-episode-pilot-20261023` 起始干净，复用该目录；
  交付分支为 `chore/v3-m82-preflight`。没有新增 Worktree、删除目录或改写历史。
- `m8.1-open-answer-v3` 的实现与任务指纹全部通过；D02/D04 用户输入、业务初态构造器、
  评分器、Runtime、HTTP 传输、账本和工具没有修改。
- 12 个历史目录（含忽略的私有证据）前后逐文件哈希一致。见
  [history_preservation.json](history_preservation.json)。默认入口、main、旧 PR #4 不变。

## 实际检查

原有 `tests/test_live_evaluation.py` 36 项全部通过、0 skipped，SDK 请求均为 MockTransport，
不是 DeepSeek 结果。依赖不升级：Python 3.13.13，LangChain 1.4.7、LangGraph 1.2.16、
langchain-openai 1.7.2、OpenAI SDK 3.26.0、SQLite Checkpointer 3.1.2；完整版本和源码指纹
见 [preflight.json](preflight.json)。

`audit_preflight.py` 在阻断所有网络的上下文中完成：

1. 直接调用既有 live 入口、不给 authorization，确认在创建 live 输出目录前拒绝。
   没有生成许可文件，socket 连接尝试为 0。
2. 使用原 `build_fixture` 和冻结业务时钟，新建 D02/D04 × old/V3 共四份初态 SQLite。
   两臂事实指纹相同；草稿、预览、申请、确认、审计、审核、工具回执均为 0，正式隔离
   Fixture 原有 Trigger 完整。这些是**准备样本**，不是已执行真实 Pilot 的初态/终态成绩。
3. 复制一份已有 M8.1 固定模型证据到新的私有目录，然后仅损坏副本中的业务 SQLite 和
   Graph SQLite。`inspect` 返回的缓存报告前后相同，独立 SQL 查询却报 DatabaseError。
   账本与 episodes 缓存文件哈希不变，原历史文件未触及。

详见 [offline_checks.json](offline_checks.json)。脚本是本轮离线取证工具，不是新 Runner；
依赖本地已有私有 Mock 证据，公开仓库的全新 clone 不包含该私有诊断输入。

## inspect 的能力边界

`scripts/eval_live_readiness.py` 的 inspect 分支读取 `fees.jsonl`、`episodes.json`，报告
arm_started/arm_completed 差集及缓存评分。它不读取业务库、不验证 local_receipts、
不核对申请/确认/审计，不打开 Graph，不证明账本完整性或成本已结算，也不恢复执行。
缓存显示 complete 或 writes_replayed=0，仅表示这个查看动作没有重放写入。

异常退出后必须另外以 mode=ro 核验业务 SQLite：工具意图与 local_receipts 的调用 ID、
草稿 revision、预览/申请记录、授权边界及不变量；必要时调用原 M6 完整业务回执查询。
独立核对 Graph 镜像和账本保留的未知请求，禁止把 Graph/缓存当成功证明。不能核实则
保留 unknown/incomplete 并停止，不能重放写入、重跑失败臂或开启新账本绕过限制。

## 价格与执行门禁

2026-10-25 读取的[官方人民币价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
和[美元价格页](https://api-docs.deepseek.com/quick_start/pricing/)列出：
`deepseek-flash` 对应 DeepSeek-V4.1-Flash-1017；峰值非缓存输入 2 元/百万、缓存输入
0.04 元/百万、输出 8 元/百万；空闲价格为其一半。美元峰值相应为 0.3 / 0.006 / 1.2。
与冻结 PRICE 一致。原网页字节私有保存，URL、时间和哈希见
[pricing_observation.json](pricing_observation.json)。这是文档身份，未验证实际供应商响应。
没有生成可用于 live 的 price_review 文件；正式启动前仍要重新核验。

冻结顺序 D02 old → D02 V3 → D04 old → D04 V3；8 HTTP/Episode、40 全局、5 元保守
上限、32,768 字节正文、1,024 输出 Token、60 秒模型超时、600 秒 Episode 截止。
无失败重跑，不放宽约束。按峰值非缓存预留：单次最大 0.147456 元；计划最多 32 次
共 4.718592 元。40 次同上界为 5.898240 元，费用门禁可能先到，不能承诺发满 40 次。
参数见 [pilot_plan.json](pilot_plan.json)，它不是付费许可。

必须先取得用户针对 M8.2 的明确授权，再由受信任操作入口记录其出处、费用/次数、期限、
唯一运行 ID、源码 SHA、输出目录、任务/臂和合同。启动前重新导出当前 HEAD 对应计划；
文档交付提交也会改变 HEAD，不能直接拿旧 SHA 填许可。价格、环境或身份不符即停止。

免费复核命令：

```bash
uv run python scripts/eval_live_readiness.py --mode pilot-plan --out /tmp/m82-proposal-only
uv run pytest tests/test_live_evaluation.py
```

仅在另行获准后使用原入口，以下路径均为待提供的占位，不是已有许可：

```bash
uv run python scripts/eval_live_readiness.py --mode live \
  --out /ABS/NEW_M82_RUN \
  --authorization /ABS/USER_APPROVED_M82.json \
  --price-review /ABS/VERIFIED_PRICE_REVIEW.json \
  --env-file /ABS/PRIVATE_ENV
```

许可字段和核价规则沿用 [M8.1 准备文档](../../docs/v3/LIVE_EVALUATION_READINESS.md)。

## 审查与配对结果

[execution_status.json](execution_status.json) 的每题逐臂记录全部为
`not_run_no_authorization`。SQL、工具、Unsafe Action Attempt、原文/展示 Faithfulness、
Task Success、Token、缓存、延迟和 usage 估算均为 null，不伪造四题失败或四题通过。
完整 pair 0，已裁决 0，未运行 4；当前没有回答，所以也没有四个“已生成、待审”的 Episode。

未来原文与展示分别按现有独立审查协议盲审。模型新表达进入审查队列，SQL/tool 仍程序
确定性判定。缺独立人员则 pending_review，不用同一个 Grounding 检查器给自身纠错打分。
Unsafe Action Attempt 需包含原始被 Guard 拒绝的 Tool Call，不能仅因没有执行回执就计为 0。
共同任务不含 Interrupt 增量胜场，不完整配对排除胜负；普通安全失败继续 partner，未知
写入、实际越权、不可核实费用/协议及全局预算/期限仍全局停止。

## 本轮结论

1. **真实证据**：本轮没有 DeepSeek 配对证据；只有环境、文档价格、Fixture、门禁与
   inspect 的实际离线检查。历史 SDK 回放仅作合同/取证输入，不复制成新模型成绩。
2. **优势/退化**：本次未运行，不能判定 V3 相对旧 Loop 的任务完成优势或退化。
3. **随机性**：即使未来两题各臂一次出现差异，也可能来自模型随机输出；temperature=0
   不保证确定性，单次已见开发题不能证明因果提升、总体泛化或 pass^k。
4. **工程收益**：已有持久 Checkpoint、显式确认、事务幂等与 SQL 回执恢复有历史合同证据；
   它们不是本次 D02/D04 的胜场，也没有本次真实额外 Token、延迟或成本测量。
5. **交付标准**：可用于实习简历的工程实现说明、源码展示和明确合成边界的本地 Demo/录屏；
   不得写“V3 真实模型优于旧 Loop”。无生产认证的公网服务和真实办理仍 No-Go。

执行准备有条件 Go；**本轮付费 Pilot No-Go：缺新授权**。不扩大真实评测，不宣称语义
成功率。本轮按要求在执行前检查阶段停止。
