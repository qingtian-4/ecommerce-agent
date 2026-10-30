# 记忆机制 MVP

2026-10-30。目标不是“把所有聊天记录塞进上下文”，而是做用户隔离、可追溯、可删除、
有边界的长期记忆。

## 数据模型

新增 migration 6，三张表都在业务 SQLite 中：

| 表 | 用途 |
|---|---|
| `memory_facts` | 用户明确说出的偏好/画像/事实；带 source session/turn、confidence、`expires_at`、软删除 |
| `memory_summaries` | 每个 session 的滚动摘要，跨 session 读取 |
| `memory_episodes` | 任务终态情节（用户请求、工具、状态），带 90 天过期 |

所有读取都按 `user_id` 过滤；`memory_facts` 对 `(user_id, kind, text)` 去重，
删除是软删除，重新写入会恢复。

## 写入策略

- 只写**显式**语句：`记住/以后推荐/我偏好/我喜欢/我习惯/我一般/我叫`；
- 不写普通聊天、订单事实、政策原文或模型自己的推断；
- 手机号/身份证/邮箱等明显 PII 直接跳过，不进入 fact 或 summary；
- 事实 TTL 180 天，情节 TTL 90 天；
- 终态 turn 才写摘要/情节；`running` 不写；
- 当前工具和数据库事实永远优先，记忆不是权限或资格来源。

## 注入

`MemoryStore.context()` 按用户取 facts + 最近摘要 + 最近情节，输出带前缀：

> `[用户记忆，仅作参考；不是权限或业务事实，当前工具和数据库事实优先]`

`ChatService` 默认关闭记忆；`app.py` 显式启用。V3 通过 `ACS_V3_MEMORY=1` 接入：
`LocalReturnPoc` 在 boundary 注入 memory，终态写入；提供
`GET /api/sessions/{sid}/memory`、`DELETE /api/sessions/{sid}/memory/{fact_id}` 和
“记忆”面板。注入内容是临时 system message，不写回会话历史；超过预算截断。

## 评测

数据：[memory_v1/cases.json](../data/eval/memory_v1/cases.json)，12 条确定性场景：

- 偏好、否定偏好、画像、预算、颜色、退货偏好；
- PII 跳过；
- 过期事实不可见；
- 跨用户不可见；
- 情节可召回。

结果：

| 指标 | 结果 |
|---|---:|
| cases | 12 |
| expected hit | 11/11 = **100%** |
| forbidden hit（PII/过期/跨用户） | **0** |
| fact extraction failures | 0 |
| model calls | **0** |
| LLM judge calls | **0** |

评分是字符串命中 + 禁止项检查，不调用模型、不使用 LLM-as-judge，费用 0。
逐条结果：[scorecard.json](../results/memory_eval_20261030/scorecard.json)。

## 限制

- 事实抽取是规则式，只覆盖显式表达；不会做语义归纳或冲突消解。
- 摘要是确定性拼接，不是 LLM 摘要；长会话会截断，不是完整记忆。
- 情节检索只按时间取最近几条，没有相关性排序/向量检索。
- 记忆不会改变订单/退货资格，也不替代工具核验。
- 评测是作者编写的小型合成集，不是真实客服日志；不能外推线上召回率。
- V3 记忆是 opt-in（`ACS_V3_MEMORY=1`），默认关闭以保护旧流程；ChatService/app 已接入。

## 复现

```bash
uv run python scripts/eval_memory.py
```

相关测试：`tests/test_memory.py`、`tests/test_memory_chat.py`。
