# 意图路由 held-out 评测：规则路由器

2026-10-30。数据来自已有冻结任务，不是为规则路由器编写的 24 条作者用例：

- `shop_mainline_v1_1`：8 个场景、16 轮；
- `commerce_complex_v1`：12 个场景、24 轮；
- `commerce_benchmark_v2_2/agent_tasks`：60 个任务、130 个 chat 步骤。

共 **170 条** query。预期标签按 gold tool 的必需工具族推导：

| 工具族 | route |
|---|---|
| list_orders / get_order | orders |
| get_logistics | logistics |
| list_products / get_product | product |
| search_knowledge / get_knowledge_documents | policy |
| get_return_task / update_return_task / cancel_return_task / preview_return / get_return_requests | return |
| get_price_history | price |

这里把 expected 当作“至少需要哪些工具族”，不是穷尽语义意图；工具没覆盖的隐含意图不会出现在 Gold 里。

## 结果

| 指标 | 结果 |
|---|---:|
| cases | 170 |
| exact match | 37/170 = **21.8%** |
| coverage（expected ⊆ predicted） | 106/170 = **62.4%** |
| 缺失标签总数 | 70 |
| 多余标签总数 | 163 |
| 多意图 exact | 4/27 = **14.8%** |
| 多意图 coverage | 13/27 = **48.1%** |

按 route：

| route | support | precision | recall | F1 |
|---|---:|---:|---:|---:|
| orders | 18 | 0.206 | 0.778 | 0.326 |
| logistics | 15 | 0.833 | 1.000 | 0.909 |
| product | 47 | 0.673 | 0.745 | 0.707 |
| policy | 17 | 0.115 | 0.529 | 0.190 |
| return | 98 | 0.722 | 0.531 | 0.612 |

## 解释

- 规则路由器在作者编写的 24 条开发集上 24/24，但在 held-out 任务上只有 21.8% exact；这是明显的过拟合。
- 最大的两类问题：
  - **return 漏识别**：98 条 return support，只覆盖 52 条；大量“退订单/存草稿/生成预览/只读核对”没有“退货”字样。
  - **orders/policy 过度触发**：只要出现“订单/期限/政策”就加 route，导致 163 个多余标签，精确率很低。
- coverage 62.4% 说明它不是“稍微不准”，而是会漏掉必需工具族；如果 ChatService 用它做 tool schema 硬过滤，会直接挡掉正确工具。

## 结论

**规则路由器不能作为产品硬路由继续使用。** 它只能作为提示/实验基线；在独立评测达标前，应关闭 ChatService 的硬过滤，或降级为软提示。24/24 不能再作为能力结论。

原始逐条结果见 [scorecard.json](scorecard.json)。
