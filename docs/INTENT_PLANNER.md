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

2026-11-02 审查修订：此前 `app.py` 虽设置 ChatService 开关，却传入了未开启 planner 的
Agent 工厂，实际未生效；现已在实际工厂同时开启 planner 和完成检查，并覆盖 `on_send`
入口回归。planner 输出须符合结构及有限数值置信度要求；调用/解析失败保留 trace 并恢复
原有有界完成检查，不将失败当作合法空意图。低置信度/歧义保留澄清提示，工具仍全部可见。
`ChatService.run_turn(agent=...)` 使用调用者提供的 Agent 配置；服务开关用于其默认工厂，
传入自定义 Agent 时需在该 Agent 的构造处明确设置开关。

账本从原请求保留全量目录和明确预览/草稿动作要求，分页首页与业务错误不能当作成功证据；
订单回执不能代替物流或价格历史。合法空列表仍可表示无匹配。补查后的最终回答须覆盖所有
诉求，不再自动拼回此前可能错误的整段回答。它仍是工具合同检查，不证明自然语言完整、
政策语义匹配或任意实体绑定已经正确。
有明确对象及原因的正常无结果（如本人订单暂无物流）可以结束对应只读查询，但不证明任何
业务资格；拒绝或错误回执不会替代成功证据。分页复用已有 `catalog_progress`，按同一查询的
首末页、唯一商品覆盖和稳定总数判断完成，不能将其他筛选条件的页混入全店目录。
补查反馈仅进入模型请求，不写入会话消息，避免持久化角色校验失败。

## 评测数据

- **170 条开发/覆盖集**：已有冻结任务，expected 由 gold tool 的必需工具族推导；用于覆盖
  回归，不当作独立人工语义集。
- **30 条挑战集**：作者编写的 paraphrase/多意图/歧义/未知集，先冻结再跑。
- **30 条最终验收集**：作者编写、在首次运行前冻结；运行后没有按结果调 prompt。
  现已看过结果，只能作为开发验收/回归证据，不是独立盲测；文件历史名称中的 held-out
  不代表外部独立标注或盲审。
- 指标：
  - `coverage`：required ⊆ predicted，是否有漏必需 route；
  - `strict exact`：严格集合相等；
  - `acceptable exact`：`required ⊆ predicted ⊆ required ∪ optional`，允许数据集声明的
    可接受 optional route；
  - `multi_intent_coverage`：多意图用例的 coverage；
  - `write_false_positive`：预测 return 但非 return 需求，且 **未触发澄清** 的用例；
  - `clarification_accuracy`：挑战集的 clarify 判断准确率。

## 评分与 judge

本轮**没有使用 LLM-as-judge**。意图是离散标签，评分在
[`_metrics`](../scripts/eval_intent_planner.py) 中用集合比较完成：

- strict exact：`predicted == required`；
- acceptable exact：`required ⊆ predicted ⊆ required ∪ optional`；
- coverage：`required ⊆ predicted`；
- 多意图、per-route P/R/F1、clarification、return 误触发均为确定性计算。

planner 模型只负责产生预测，不参与打分；没有 judge prompt、judge 模型调用或 judge 费用。
规则基线没有澄清器，其澄清标志固定为 false；不能借用 planner 的 `planned_clarify`
抵扣规则基线的 return 误触发。2026-11-02 修正了该评分实现，历史预测只做离线重评分。
如果以后评“额外 route 是否真的可接受”“澄清是否自然”或最终答案覆盖，才需要 judge；那时
应先用人工标签校准，并报告 judge-human 一致率，不能直接拿 judge 当唯一判据。

## 结果

| 数据集 | 方法 | strict exact | acceptable exact | coverage | 多意图 coverage | 未澄清 return |
|---|---|---:|---:|---:|---:|---:|
| 170 开发 | 规则硬路由 | 21.8% | 21.8% | 62.4% | 48.1% | 20 |
| 170 开发 | RAG planner | 42.4% | 42.4% | **99.4%** | **96.3%** | 17（旧口径，含澄清） |
| 30 最终 | 规则硬路由 | 56.7% | 56.7% | 70.0% | 62.5% | 2 |
| 30 最终 | RAG planner | 86.7% | **93.3%** | **100%** | **100%** | **0** |

最终验收集代价：30 次 planner 调用，32,459 prompt / 1,216 completion tokens，31.7 秒，
0.023989 元（按 usage 估算）。170 开发集加 30 条挑战集合计：200 次调用，0.174398 元。

最终验收集仍有 1 个不必要澄清、5 个额外 route：其中 2 个是数据声明的 optional
（H02/H06 的 product），3 个是不必要的；因此 strict exact 86.7%、acceptable exact
93.3%。早期评分没有使用 optional，strict-only 口径会误判这 2 条。额外 route 在软
提示下是噪声，在硬过滤下会变成风险，因此**不恢复硬过滤**。

评测代码已增加 `acceptable_match`、`optional_predictions`、`unnecessary_labels`；
重打分见 `results/intent_planner_final_v3_20261030/rescore.json`，不需要重新调用模型。

## 验收

| 门槛 | 结果 |
|---|---|
| 必需 route coverage ≥95% | 30/30 = 100% |
| 多意图 coverage ≥90% | 16/16 = 100% |
| 未澄清的 return 误触发 = 0 | 0 |
| 澄清准确率 | 28/30 = 93.3% |
| strict exact（参考） | 26/30 = 86.7% |
| acceptable exact（参考） | 28/30 = 93.3% |
| 规则路由不可用 | 规则 coverage 70%，已被 planner 替代 |
| 端到端不回归 | 相关单测/应用测试通过；未声明生产端到端成功率 |

## 限制

- 挑战/最终集是作者编写的合成语义集，不是真实客服日志；不能外推“线上准确率 93.3%”。
- optional 是作者声明的可接受 route，不是“任何额外 route 都合法”；未声明 optional 的
  额外 route 仍计入 unnecessary_labels。
- 170 的 expected 是工具族最小集合，exact 会低估/高估；coverage 才是主指标。
- `write_false_positive` 是“未澄清的 return route”，不是实际写数据库；实际写仍由
  ReturnService 确认/审核链路控制。没有声称真实退款/安装/维修。
- planner 每条 query 多一次模型调用；当前 prompt ~1.1k tokens，成本已记录，生产需评估缓存。
- V3 网页默认保留旧的提示级规则 route 以兼容旧流程；设置 `ACS_V3_PLANNER=1` 后
  在真实模型模式切到 planner；固定脚本/测试模式不会因环境变量隐式调用付费模型。
  planner 复用宿主模型的 HTTP transport、费用账本、正文上限及超时/授权期限，不再另建
  LLMClient。单次 V3 执行保留总共最多 8 次模型调用（1 次规划 + 至多 7 次业务调用），
  工具仍最多 16 次；未启用 planner 时业务模型仍最多 8 次。
- `ChatService` 默认关闭 planner/memory；`app.py` 显式开启二者；V3 两项均为 opt-in。
  交互主模型可使用 64KiB 正文上限，冻结评测仍为 32KiB，两种结果不能直接比较。

## 复现

```bash
uv run python scripts/eval_intent_planner.py \
  --out results/intent_planner_20261030_v2
uv run python scripts/eval_intent_planner.py \
  --out results/intent_planner_final_v3_20261030 \
  --skip-validation --challenge-path data/eval/intent_semantic_v1/final_validation_v3.json
# 已保存预测的重打分（不调用模型）
uv run python scripts/rescore_intent_planner.py \
  --scorecard results/intent_planner_final_v3_20261030/scorecard.json \
  --dataset data/eval/intent_semantic_v1/final_validation_v3.json \
  --out results/intent_planner_final_v3_20261030/rescore.json
```
