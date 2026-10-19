# 电商客服 Agent Review 指南

GitHub 仓库：`qingtian-4/ecommerce-agent`（私有）。本地与网页端通过提交、分支和 PR
同步代码；本地未提交文件不会自动出现在网页端。

## 首轮 Review

在网页端连接并授权访问该仓库，选择要检查的分支。建议先做只读 Review：

> 先阅读 AGENTS.md、docs/PROJECT_SCOPE.md、docs/EXECUTION_PLAN.md、
> docs/PROBLEM_SOLVING.md 和 results/iteration_v2_recovery/final/REPORT.md。
> 检查实现是否符合业务范围，重点关注订单草稿保存、身份与数据权限、预览确认、
> 幂等与恢复、评分器未判定和成本统计。区分已确认缺陷、证据不足和设计取舍；
> 每个问题给出文件位置、触发条件、现有防护和可复现依据。先报告问题，不修改代码。
> 不调用外部模型 API，不重跑在线评测，不将已有回归称为盲测。

历史回归严格通过两组均为140/160。候选的 orders-02 仍未保存订单草稿，裁判未判定
14次；这些是已记录的问题，Review 应检查原因及影响，不能假设整体可靠性已得到证明。
τ 项目 Loop 和官方 Agent 的预算不同，外部成绩不评价本店页面、RAG 和审核权限。
原始 JSONL 较大，先看 summary 和失败索引，再按任务 ID、trial、arm 筛选记录；
全量统计使用本地脚本，不把整份对话记录展开到模型上下文。

## 离线检查

在云端安装锁定依赖后，可运行不使用外部 API 的检查：

```bash
uv sync --frozen --extra dev
uv run --frozen pytest
uv run --frozen ruff check .
```

不要上传本地 `.env`，也不需要为首轮 Review 配置 DeepSeek 或 embedding 密钥。
在线评测需要另行明确任务数量、重复数及金额预算；运行前先完成小规模费用估算。

结果文件中的本机绝对路径、`/tmp` 上游环境和基线工作树是历史运行记录，不会随 Git
复制到云端。业务快照和索引可从已提交的清单与脚本重建，不能把它们缺失当作客服失败，
也不能静默更换基线、模型或评分器补跑原实验。

## 后续修改

每项修改使用独立分支，连同必要测试和文档创建 PR。审查差异和检查结果后再合并；
本地继续开发前同步远程更新。若启用了 Codex GitHub Code Review，可在 PR 中使用
`@codex review`；PR Review 主要检查该 PR 的差异，整体审查需要明确要求阅读全项目。

官方说明：[Codex Cloud](https://learn.chatgpt.com/docs/cloud)、
[GitHub PR Review](https://learn.chatgpt.com/docs/third-party/github)。
