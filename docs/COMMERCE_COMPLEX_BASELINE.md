# 复杂多意图、商品实体与工具决策：离线基线

> 后续：用户授权的低谷期真实 Pilot 已完成，18 次请求、费用估算 0.0354724 元。
> SQL/工具检查 4/4、子目标证据 20/20；四段语言结果仍待独立复核。
> 见[真实结果](../results/complex_pilot_20261027_run1/REPORT.md)。下文保留离线阶段记录。

基线 `7e5b893`，分支 `codex/complex-intent-entity-baseline`。本阶段已完成冻结、免费
基线、最小工具修复和回归；真实模型基线尚未执行，等待费用授权。

`data/eval/commerce_complex_v1` 冻结 12 个场景、24 轮、每臂 59 个子目标，以及 12 个
实体工具探针。继续使用 v2.2 的 40 商品、240 订单、8 用户和 30 文档，没有另建业务事实。
用例是作者编写的开发材料；所有输入在基线前冻结，不宣称未见测试或独立客户样本。

场景覆盖名称空格/全角/大小写、多系列歧义、双商品比较后的代词歧义、咨询与部分草稿
并行、同款多订单、更正订单清依赖、引用取消、他人订单拒绝后继续合法咨询、不存在型号、
跨页候选、双实体更正和聊天确认边界。完整 SQL 与工具回执按既有 Runtime 执行。

每个目标分开检查：业务状态、可用证据、已标注回答是否覆盖。正确 SQL 或一句正确回答
不能掩盖漏意图；59 个固定遗漏反例全部判失败。陌生自然语言继续 pending_review。
允许完整商品详情、目录、静态规格历史复用、RAG、订单现价和价格历史等合法证据；
错误查询/额外价格筛选得出的空结果不能证明目标不存在，完整目录扫描也能提供合法证明。

发现的具体缺陷是 `name_contains` 直接比较原文：`晴岚A6` 与目录的 `晴岚 A6`、小写
和全角写法不匹配。工具合同升级为 `catalog-pages-v2`，仅加入 NFKC、去空白和 casefold。
不增加模糊纠错、同义词或整句意图解析，不把 A60 替换成 A6，也不自动选择多个候选。
已有分页、完整字段、长度上限、订单权限、确认与幂等机制保持。

| 固定输入和判据 | 基线 | 改进 |
| --- | ---: | ---: |
| 实体工具探针 | 6/12 | 12/12 |
| 旧 Loop：完整多目标证据与脚本答案 | 8/12 | 12/12 |
| V3：完整多目标证据与脚本答案 | 8/12 | 12/12 |
| 两臂子目标证据合计 | 106/118 | 118/118 |
| 每臂工具调用 / 脚本决策 | 41 / 65 | 41 / 65 |

这是实际工具/Runtime 配合固定决策的结果，不是模型 NLU 或自主工具选择成功率。
改善来自候选召回，调用量未下降。真实模型的歧义处理、意图拆分、子目标完成和成本仍待测。
见 [报告](../results/complex_intent_20261027/REPORT.md)。

免费复现（每次使用新输出目录）：

```bash
.venv/bin/python scripts/eval_commerce_complex.py --mode verify
.venv/bin/python scripts/eval_commerce_complex.py --mode entities --out /tmp/complex-entities
.venv/bin/python scripts/eval_commerce_complex.py --mode agents --out /tmp/complex-agents
.venv/bin/python scripts/eval_complex_pilot.py --mode plan
.venv/bin/python scripts/eval_complex_pilot.py --mode rehearsal --out /tmp/complex-sdk
```

默认 Pilot 命令只给预算；rehearsal 仅用 MockTransport。live 必须另给明确用户授权、
当前提交 SHA、两份数据哈希、指定场景/Runtime/输出目录/有效期和新价格核验文件。
授权不足、源码或数据未提交、预算扩大均在真实调用前拒绝，不复用旧 Pilot 授权。

建议第一轮只测 CX01（歧义商品加政策）与 CX04（部分草稿加商品咨询），每题两种
Runtime，共 4 段；每段最多 8 请求、总共 32 请求，最大正文 32,768 字节、输出 1024
tokens。按官方高峰输入 2 元/百万、输出 8 元/百万及既有保守预留规则，最多预留
4.718592 元，建议授权上限 **5 元**。费用清单见
[pilot_proposal.json](../results/complex_intent_20261027/pilot_proposal.json)。不包含付费用户
模拟器、Judge、Embedding 或 Rerank；真实运行前仍需重新验证官方价格。当前未授权。
