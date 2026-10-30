# 意图路由 held-out 评测（v2）

2026-10-30。目标不是再证明作者写的 24 条用例能被规则命中，而是用已有冻结任务检验
规则路由和真实模型分类的泛化。

## 数据和标签

- 来源：`shop_mainline_v1_1`（16 轮）、`commerce_complex_v1`（24 轮）、
  `commerce_benchmark_v2_2/agent_tasks`（130 个 chat 步骤）。
- 共 **170 条 query**，不是为规则路由器编写的 utterances。
- 预期 route 由 gold tool 的必需工具族推导：
  - orders：list_orders/get_order
  - logistics：get_logistics
  - product：list_products/get_product
  - policy：search_knowledge/get_knowledge_documents
  - return：get_return_task/update_return_task/cancel_return_task/preview_return/get_return_requests
  - price：get_price_history
- 这是“至少需要哪些工具族”，不是穷尽的语义意图。工具没覆盖的隐含意图不会出现在 Gold，
  因此 exact match 会低估模型；coverage（expected ⊆ predicted）才是“有没有漏必需 route”
  的主指标。

## 结果

| 指标 | 规则路由器 | DeepSeek 分类基线 |
|---|---:|---:|
| exact match | 37/170 = **21.8%** | 51/170 = **30.0%** |
| coverage | 106/170 = **62.4%** | 140/170 = **82.4%** |
| 缺失标签 | 70 | 31 |
| 多余标签 | 163 | 107 |
| 多意图 exact | 4/27 = 14.8% | 11/27 = 40.7% |
| 多意图 coverage | 13/27 = 48.1% | 22/27 = 81.5% |
| 费用 | 0 | 0.052759 元（170 次，按 usage 估算） |

按 route（规则）：

| route | support | precision | recall | F1 |
|---|---:|---:|---:|---:|
| orders | 18 | 0.206 | 0.778 | 0.326 |
| logistics | 15 | 0.833 | 1.000 | 0.909 |
| product | 47 | 0.673 | 0.745 | 0.707 |
| policy | 17 | 0.115 | 0.529 | 0.190 |
| return | 98 | 0.722 | 0.531 | 0.612 |

## 解释

- 规则路由器在作者编写的 24 条开发集上是 24/24，但在 held-out 上只有 21.8% exact、
  62.4% coverage；这是明显过拟合。最大问题是 **return 漏识别**（“退订单/存草稿/预览/
  只读核对”没有“退货”字样）和 **orders/policy 过度触发**。
- 真实模型 coverage 更高（82.4%），多意图 coverage 81.5%；但 exact 仍只有 30%，
  多余标签 107 个，且仍有 31 个 missing。模型倾向把隐含语义都列出，例如
  “我要退订单…先存草稿” → `orders+return`，Gold 只要求 return；“现价和降噪” →
  `price+product`，Gold 只要求 product。
- exact 不是唯一目标：对**硬工具白名单**，missing 是致命的；对**提示级路由**，extra
  可能只是噪音。当前两类结果都不足以支持“规则硬过滤”。

## 决策

1. **ChatService 的路由降级为软提示**：`intent_routing=True, strict_routing=False`，
   保留 route 提示但不按规则过滤 tool schema，避免 62.4% coverage 的规则挡掉正确工具。
2. 规则 router 保留为实验/回归基线；24/24 只能叫开发集，不是能力结论。
3. 如果后续要做硬路由，需要：
   - 独立人工语义标签（区分必需/可选 route）；
   - 模型或语义路由的 coverage 门槛与低置信度兜底；
   - 不在同一批已见用例上调参后继续当验证集。
4. 记忆机制与路由解耦，可以继续；但不应让有缺陷的硬路由影响记忆/工具执行。

## 复现

```bash
uv run python scripts/eval_intent_router_v2.py --out results/intent_routing_v2_rule_20261030
uv run python scripts/eval_intent_router_model.py --out results/intent_routing_v2_model_full_20261030 --limit 170
```

模型脚本会记录每次请求的 usage 和费用；输出目录不可覆盖。
