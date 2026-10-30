# RAG 意图规划器与多意图评测

2026-10-30。目标是替换“关键词命中 → 硬过滤 tool schema”的路线，改成：

1. 版本化 Intent Registry（意图定义 + 正反例 + 必需工具/证据）；
2. BM25 检索意图边界示例（RAG）；
3. 一次 JSON 模型调用输出多个 goal + confidence + 澄清；
4. 所有工具保持可见，route 只做软提示；
5. 多 goal 交给 `GoalLedger` 做证据完成检查。

## 实现

- [intent_registry_v1.json](../data/eval/intent_registry_v1.json)：6 个意图，含 `policy` 与
  `return` 的负例（“能退吗/退款多久/运费谁出”不等于退货动作）。
- [intent_planner.py](../src/acs/intent_planner.py)：
  - `IntentRegistry.retrieve()`：对定义、正例、负例做 BM25，取 top-8 边界示例；
  - `IntentPlanner.plan()`：一次 JSON 调用，输出 goals/confidence/clarify；
  - 低置信度、退货动作缺订单对象、颜色变更等歧义 → 澄清；
  - 配送异常（签收/没收到/送到）作为通用补全，补上 `orders`，避免只看到物流；
  - 所有工具始终可见；写操作仍由 ReturnService 的草稿/预览/确认/审核链路授权。
- `ChatService` 默认关闭 planner，`app.py` 显式启用；`strict_routing` 硬过滤已删除。
- `GoalLedger.from_plan()` 支持 6 类 goal，检查订单/物流/商品/政策/退货/价保回执。

## 评测数据

- **170 条开发/覆盖集**：已有冻结任务，expected 由 gold tool 的必需工具族推导；用于覆盖
  回归，不当作独立人工语义集。
- **30 条挑战集**：作者编写的 paraphrase/多意图/歧义/未知集，先冻结再跑。
- **30 条最终验收集**：作者编写、在首次运行前冻结；运行后没有按结果调 prompt。
- 指标：
  - `coverage`：required ⊆ predicted，是否有漏必需 route；
  - `exact`：严格集合相等（extra 会扣分，作为噪声指标）；
  - `multi_intent_coverage`：多意图用例的 coverage；
  - `write_false_positive`：预测 return 但非 return 需求，且 **未触发澄清** 的用例；
  - `clarification_accuracy`：挑战集的 clarify 判断准确率。

## 结果

| 数据集 | 方法 | exact | coverage | 多意图 coverage | 写误触发 |
|---|---|---:|---:|---:|---:|
| 170 开发 | 规则硬路由 | 21.8% | 62.4% | 48.1% | 20 |
| 170 开发 | RAG planner | 42.4% | **99.4%** | **96.3%** | 17（旧口径，含澄清） |
| 30 挑战 | 规则硬路由 | 56.7% | 80.0% | 62.5% | 0 |
| 30 挑战 | RAG planner | 80.0% | 90.0% | 81.3% | 1 |
| 30 最终 | 规则硬路由 | 56.7% | 70.0% | 62.5% | 2 |
| 30 最终 | RAG planner | **86.7%** | **100%** | **100%** | **0** |

最终验收集代价：30 次 planner 调用，32,459 prompt / 1,216 completion tokens，31.7 秒，
0.023989 元（按 usage 估算）。170 开发集：200 次调用，0.174398 元。

最终验收集仍有 1 个不必要澄清、5 个额外 route；exact 86.7%，不是 100%。额外 route 在
软提示下是噪声，在硬过滤下会变成风险，因此**不恢复硬过滤**。

## 验收

| 门槛 | 结果 |
|---|---|
| 必需 route coverage ≥95% | 30/30 = 100% |
| 多意图 coverage ≥90% | 16/16 = 100% |
| 未澄清的 return 误触发 = 0 | 0 |
| 澄清准确率 | 28/30 = 93.3% |
| 规则路由不可用 | 规则 coverage 70%，已被 planner 替代 |
| 端到端不回归 | 相关单测/应用测试通过；未声明生产端到端成功率 |

## 限制

- 挑战/最终集是作者编写的合成语义集，不是真实客服日志；不能外推“线上准确率 86.7%”。
- 170 的 expected 是工具族最小集合，exact 会低估/高估；coverage 才是主指标。
- `write_false_positive` 是“未澄清的 return route”，不是实际写数据库；实际写仍由
  ReturnService 确认/审核链路控制。没有声称真实退款/安装/维修。
- planner 每条 query 多一次模型调用；当前 prompt ~1.1k tokens，成本已记录，生产需评估缓存。
- V3 网页默认保留旧的提示级规则 route 以兼容旧流程；设置 `ACS_V3_PLANNER=1` 后
  切到 planner（已通过 V3 web 测试）。ChatService/app 已默认启用 planner。
  下一步是把 V3 的 opt-in 变成默认并清理旧规则 hint，再进入记忆开发。

## 复现

```bash
uv run python scripts/eval_intent_planner.py \
  --out results/intent_planner_20261030_v2
uv run python scripts/eval_intent_planner.py \
  --out results/intent_planner_final_v3_20261030 \
  --skip-validation --challenge-path data/eval/intent_semantic_v1/final_validation_v3.json
```
