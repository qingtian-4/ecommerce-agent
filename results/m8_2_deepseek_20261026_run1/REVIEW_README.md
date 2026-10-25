# 本批独立审查交接

当前 16 项全部待审，没有独立人员结论。沿用
[HUMAN_REVIEW_PROTOCOL](../../docs/v3/HUMAN_REVIEW_PROTOCOL.md)。

操作员只交付 `.private-evidence/independent-review-items.json`，不要同时给审查员本目录
报告、自动 Scorecard 或 `.private-evidence/independent-review-operator-key.json`。
新的随机 ID 不编码任务、臂或 raw/display 身份。原文与展示共 8 对完全相同，不能把它们
算成 16 个独立模型样本；当前没有可用于估计纠错效果的改写。

每项含对话、回答、当时业务 SQL 事实、本人订单/明细、商品、政策及工具回执。逻辑业务
时钟冻结为 2026-10-25 04:00 UTC；实际请求发生在北京时间 2026-10-26，不能用实际运行
日期替换任务时钟评判回答。

请分别填写 fact（correct/incorrect/unknown）、goal（complete/partial/unanswered/unknown）、
unsupported_business_claim（yes/no/unknown）、evidence_citations、unknown_reason，以及真实
reviewer_id、reviewed_at。审查草稿/预览/提交/退款声明时以 SQL/业务回执为准。词面不同、
包含关键词、或者 Grounding 没有 findings 均不能单独证明正确。

先锁定独立标签，操作员再解盲。若缺证据或无人独立审查，继续 pending_review。
本轮没有调用付费 Judge，也不由 Grounding 自评。
