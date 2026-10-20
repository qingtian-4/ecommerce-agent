# fix(agent): preserve explicit return order drafts with runtime checks

`orders-02` 已查到本人订单 S20017，但模型只追问商品，四轮都未调用 `update_return_task`。新增可独立关闭的终轮检查：仅完整匹配当前明确退货原话，并核对同轮真实本人订单回执，在 SQLite 事务内仅当任务缺失时补存订单。商品明细留空，不创建售后申请，不覆盖、复活或重复修改已有任务。

ChatService 补存后读回权威状态，同步最终回答、会话消息和 final Trace；页面独立显示 state_check。最终响应失败时重新读任务与相符的持久化轮次，读回失败显示未知，并保留失败提示和实际异常。权限、明确确认、预览、幂等、审核、ReturnService、RAG 和 schema 不变。默认开关保持关闭。

验证：针对性 pytest 108 passed；隔离工作树完整 pytest 458 passed、1 skipped（缺少可选 tau2）；ruff check 与 git diff --check 通过。8 组开关配对中遗漏 0/1→1/1，6 组负例无误补存、额外 revision、新申请或新审计。新增 UI 故障红测试 3 failed→3 passed；真实处理器、恢复与离线 DOM 已核对。所有失败日志保留，无付费 API 或完整 τ Benchmark。

性能：固定协议的独立无网络微基准，六路径各 10 冷/30 热试验、5 次预热；真实补存加读回预热 p50/p95/max 为 150.59/240.26/258.22 ms，主要耗时在 SQLite 提交；保留原有 FULL 同步和原子事务。四线程竞争 40 次均只有一个新草稿且 revision=1。没有把原始单次 404.82 ms 当作稳定 p95。

成本：原始四轮开关两侧均 5 次假模型调用，首轮输入无增量；后三轮每轮增加 1,207 个序列化字符（草稿投影 1,138、历史更正 69），未测真实 Token 或费用。

验收建议：可以启用现有窄入口；本 PR 保留默认关闭，不扩展语义覆盖。不能撤回流式文字，中断恢复不保留完整 Trace；数据库不可读时明确未知。启用与回退分别设置 `ACS_RETURN_STATE_CHECK_ENABLED=true/false` 并重启；不删除合法草稿，无数据迁移。完整审查、失败日志、配对探针与微基准见 [报告](../REPORT.md)。

本文件是本地 PR 描述草稿，尚未创建 PR、提交、推送、合并或发布。不包含用户已有 docs/TODOS.md、skills-lock.json 修改；未实施 P1/P2。
