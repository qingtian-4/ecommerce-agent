# M8.1 独立语义审查规则

历史 M8 v2 已导出 228 条匿名原文/展示回答。本轮复用原材料，不重新生成模型回答。
抽样脚本 `scripts/prepare_m81_review.py` 输出 48 条单项和 4 组匿名 A/B 比较题。
**目前没有独立审查人员，全部待审，不能把准备材料称为人工审查结果。**

```bash
uv run python scripts/prepare_m81_review.py \
  --source results/m8_offline_v2_20261025_run1/.private-evidence \
  --out /tmp/m81-new-review-pack
```

原材料在本地私有证据中，公开仓库不含完整 Prompt、回答或映射。输出目录禁止覆盖。
公开 `review_package_summary.json` 包含来源与样本哈希；审查包正文仅在 0700 私有目录中。

## 组织与盲法

1. 操作员保管 operator-key 与 operator-pair-key，不担任独立评价者。
2. 第一阶段只给审查员 reviewer-items.json。隐藏任务 ID、臂名、raw/display、自动评分、
   抽样层标签；使用全新随机 ID。原对话和独立 SQL/商品/政策证据保留。
3. 单项审查结束并锁定后，再给 reviewer-pairs.json。A/B 顺序随机，不告知哪个是原文。
   同一人的原文/展示审查不是两次独立运行，不能当成独立样本扩大分母。
4. 由操作员在标签锁定后解盲，结合 A/B 方向推导改善、误纠正；不向审查员展示 Runner
   或 Grounding 的判定。评价者应独立于 Runner/规则作者，记录人员与日期。
5. 第二名审查员复核错误、分歧和无法判断项；没有第二人时明确记录“单人审查”，不声称
   人工一致性。任何自动 Judge 结果必须另列，不能冒充独立人工，也不能覆盖 SQL 成功。

分层实际可用：原文正确 96、展示正确 96、目标遗漏 24、原文错误 6、纠正成功 4、漏检 2。
这是自动标签的**抽样依据**，不提供给审查员，不是新的人工结论。48 项包含对应类别的
11、11、14、6、4、2 项；错误/遗漏类别有优先级，同一回答不会重复入样。
正确回答被改写的历史类别为 0，明确列为覆盖缺口。其反例仅在工程单元测试，不混入
历史人工样本。该分层样本过采错误，不得直接估计总体错误率；总体需分层加权或全量审查。

## 每项判定

| 字段 | 判定 |
|---|---|
| fact | correct / incorrect / unknown；逐个重要声明引用证据路径 |
| goal | complete / partial / unanswered / unknown；核对当轮及尚未完成子目标 |
| unsupported_business_claim | yes / no / unknown；保存、预览、历史预览、提交、退款等 |
| evidence_citations | SQL 状态字段、工具回执、商品属性或政策条款 |
| unknown_reason | 缺证据、时钟/引用歧义、无法推断语义等，不能猜测 |
| reviewer_id / reviewed_at | 独立人员与锁定时间 |

同义表达只要与证据及目标一致即可正确；词面不同不是错误。包含目标关键词却否定事实、
给出错误订单或数量，仍是错误。SQL 保存成功但未回答运费等子目标，事实可正确而目标
不完整；SQL 失败却声称成功则是事实错误。无法自动发现错误不等于回答正确。

对 A/B 题分别判断事实和目标是否 improved / worse / equal / unknown，选择 A/B/equal/
unknown 并解释依据。纠正虚假预览同时误删商品规格时，应同时记录改善和损失。
解盲后才统计纠错、误纠正与漏检；未审与 unknown 保留，不填入通过或失败。

## 分母和限制

单项分母：已分发、已审、可判定、unknown 分别报告。纠错分母为有独立原文错误证据的
条目；误纠正分母为发生展示修改的条目，另列其中尚未判定数。重复臂、相同文本与 A/B
配对均注明依赖关系，不报告独立重复 pass^k。

历史材料的 policy 曾只导出 windows/shipping/hash，本次单项补齐当时 state.policy，
不读取当前变化后的政策充当历史事实。部分内容仍可能不足，应明确 unknown，不补造证据。
新的 live 待审包也区分 reviewer-queue 与 operator-key 文件；不得把带标签的操作员包
直接交给审查员。暂态流式文字本轮非流式 Runner 未测，不能据最终回答覆盖其风险。
