# M8.1 有效性审计

基线 `feat/v3-multiturn-evaluation / 2c794c7`，基线 CI 1088 passed、0 skipped。
本轮新分支 `fix/v3-evaluation-live-readiness`，没有调用真实 DeepSeek。

## 根因及合同差异

| 位置 | v2 事实 | M8.1 新合同 |
|---|---|---|
| assess_answer | 只认证精确参考文本，其余 unresolved | 原精确注释仍保留；开放表达 pending_review |
| run_task/task_success | unresolved 被 faithful=false 折成任务失败 | 四状态；待审/执行异常的 task_success=null |
| goal_covered | 词面提示参与严格布尔失败 | 词面仅为开放表达审查提示，不证明语义成功；已知参考遗漏仍失败 |
| raw/display | 原文与展示按独立参考评估 | 保持独立、按 SQL/参考注释打分，不调用 Grounding 做裁判 |
| 配对 | 任一任务/工具失败直接 break 全局 | 普通失败保留并继续 partner；危险/全局门禁才停止 |
| 预算 | 6 题×2 臂×4 请求，20 KB 请求正文 | 2 题×2 臂×8，每轮 40/5 元，32 KiB，实际字节预留 |
| 退出恢复 | 旧 live 计划无显式失败臂防重放合同 | 持久 arm_started/arm_completed；中途退出只读 inspect |

新模块复用原执行器，不改写其源文件，保留原 v2 实现指纹可复现。scoped ModelTap 子类
只观察被旧 Loop 吞掉的异常、调用参数验证和业务回执，不改变工具、消息或模型决定。
这仅用于单 Worker 评测进程，不是新 Agent Runtime，也不支持并发评测 monkeypatch。

一条先红后绿回归验证：“目前还没有送出申请。”与参考“尚未提交。”不同。
旧返回 unresolved 并导致最终失败；新返回 pending_review，保留待审，不能擅自判正确。
另外覆盖目标词被否定、SQL 成功但目标遗漏、SQL 失败声称成功、未知表达及展示误删。
原文已知完整而展示遗漏运费的反例可识别为一次误纠正；未知原文不得自动算成纠正成功。

## 本轮证据

- `results/m8_1_readiness_20261025`：历史前后哈希、Pilot 数学上界、完整测试验证摘要。
- `results/m8_1_sdk_fixed_20261025_run1` 与 `results/m8_1_sdk_open_20261025_run1`：
  95c14fb 干净源码运行的 Scorecard、SQL 对账、manifest；原始证据私有。
- 新固定 SDK 回放：D02 / D04 各两臂，8 个聊天 turn，16 次 Mock HTTP；没有付费。
  两对实际模型输入、工具调用、SQL 终态一致。D02 仍因回答遗漏目标失败，D04 通过。
- 新开放表达 SDK 回放：相同业务决定、另一组冻结回答，4 Episode 全部 pending_review，
  16 条原文/展示待审项；SQL/tool 全通过，2 对执行完整但语义 resolved 对数为 0。
- 事务故障：提交前无权威草稿回执则全局停止；提交后回执已在 SQL 时只读核验成功，
  保留执行异常、不重放。UNKNOWN 不会被 Graph 假成功覆盖。
- 第 8 次 HTTP 可发送、第 9 次拦截；过期授权、超长正文、过高输出上限均在 HTTP 前拒绝；
  缺 usage 仍占费用并全局停止。重开账本无法重置次数、限制或截止时间。
- 228 项复用材料已分层抽样 48 项及 4 组匿名 A/B，尚无人独立审查。
  首个候选包的 A/B 曾使用公开 Seed，红测复现后改为 SystemRandom。正式待审包为
  `results/m8_1_human_review_20261025_run2`，原候选包保留，不交给审查员。

本轮所有固定结果属于执行/评分合同测试。Mock 的 Token、费用与耗时不能被当作真实模型
表现。原 M8 25 对固定轨迹一致、5 增量通过及历史成绩不变；本轮没有重命名为新模型分数。

## 结论与限制

正确同义回答不会仅因不等于参考而判失败，但仍须独立审查才可宣布语义成功。
SQL 成功和自然语言待审可以同时存在。普通旧臂失败后，V3 partner 可以完成；缺臂、
执行异常和语义待审各有单独分母。未知业务写入、实际安全违规、不可核实协议/费用、
全局预算或授权截止仍强制停止。

数学上界与 SDK 序列化证据足以支持新的两题提案，不保证未知真实轨迹能在上下文/费用
门禁内完成。价格仍须在真实运行前重新核实。本轮未获得新的付费许可，不能执行 Pilot。

Go：离线工程合同及准备材料；获得新授权、干净源码 SHA 和价格复核后的开发 Pilot。
No-Go：现在调用 DeepSeek、独立模型优劣/泛化结论、正式 Benchmark 或生产部署。
