# Open design refinements

Decisions agreed during review, with domain delivery and remaining scope separated. Each entry
records the reasoning so it does not get lost or silently dropped.

---

## T1 — Golden set: derived facts vs. naturalised queries

**Agreed refinement (from user review, adopted over the original proposal).**

The split of responsibility is deliberate and must be enforced in code:

| Part of a case | Source | LLM allowed? |
|---|---|---|
| `ground_truth`, expected facts | **derived from KB + DB** | ❌ never |
| `expected_db_state` / `expected_post_condition` | **derived from DB** | ❌ never |
| `policy_facts` (window days, rates, thresholds) | **parsed from KB front-matter / body** | ❌ never |
| `required_tools`, `expected_docs`, `expected_route` | derived from the case template | ❌ never |
| **`query` (surface form only)** | LLM paraphrase of the structured case | ✅ allowed |

Rationale: an LLM writing the *question* can invent a plausible-looking order id
or the wrong policy number, and human spot-checking 40 rows will not catch it.
Deriving the facts and paraphrasing only the surface form keeps ground truth
self-consistent while preserving real user phrasing.

### Guards that must ship with T1

1. **No-new-entity guard.** After paraphrase, verify the query introduces no
   entity or number absent from the structured case (order id, amount, date,
   product name, platform name). A paraphrase that adds a new one is dropped.
2. **Answer-invariance guard.** Check that the paraphrase preserves the case's
   entities, conditions, negation and requested facts. Reject a change of meaning,
   such as introducing another shop or platform. Do not use retrieval success as
   an admission rule: a valid paraphrase that misses its expected section must
   remain in the dataset as a retrieval failure. Filtering those failures would
   inflate the reported score.

3. **Provenance in the trace.** Every case records
   `query_source: template | paraphrase` and `paraphrase_model`. A paraphrase
   batch is itself an experimental variable.

**Status:** 自动自然化生成器及其两个 guard 尚未实现。v2 冒烟场景的事实从 SQL 派生，
问题仍用模板，记录 `query_source=template`；这只覆盖数据来源，不代表 T1 全部完成。

章节级检索对照已单独实现：`data/eval/retrieval_cases.json` 与
`scripts/run_retrieval_eval.py`。52 条问题先冻结，再比较 Dense、BM25 和 RRF；
失败问题保留，不据检索结果改写标签。这不代表上述自动自然化生成器已经实现。

重排与核验后补位已加入 `search_knowledge` 内部，使用同一候选池独立消融：
`scripts/run_rerank_eval.py`。原 52 条问题全部转为开发证据，新 20 条验证另行冻结。
默认部署依据开发集选择，API 可用不等于需要默认启用。后续应先扩充真实业务章节和
含指代的多轮用例，再验证独立 query rewrite；不能把当前小知识库的满分当作泛化证据。

---

## T2 — Mutation safety must be a framework-level two-phase commit

`MUTATING` tools must be structurally unable to write on first call:

```
LLM calls cancel_order(order_id)
  → guard intercepts; NOTHING is written
  → returns PendingAction(token, preview) to the LLM
  → LLM asks the user to confirm
  → framework executes only with a valid, unexpired, unused token
```

This turns "wrote without confirmation" from a *probabilistic* prompt-following
failure into a *structural* impossibility, and makes it directly testable:

```python
def test_mutating_tool_without_confirmation_never_writes(): ...
```

**Status:** 退货申请的正式提交已具备预览、过期/修订/归属校验和页面明确确认；
模型没有提交或审核工具。草稿字段保存是允许的低风险副作用，不套用“首次不写任何数据”。
通用 `MUTATING` 拦截框架及 `cancel_order` 尚未实现，也不对外宣称支持取消订单。
见 `returns.py`、`test_return_submission.py`。

---

## T3 — Idempotency must be observable in the data, not just in code

The original review assumed a fully mocked backend. The current SQLite backend
persists requests and audit rows, so duplicate domain actions must be detectable
from database state, with the logical action key preserved in the audit trail.

This makes "the same logical action was attempted twice" detectable from the
database state alone, which is what the evaluator inspects.

**Status:** 退货申请已实现。`return_requests.preview_id` 唯一，确认消费、创建申请和
`return_audit` 在同一事务；重放同一预览返回已有申请，不再追加创建审计。
审核结论也有唯一记录和审计。数据库能直接验证一次逻辑提交对应一张申请/一条创建审计。
泛化到其他未来业务动作的统一审计框架尚未实现；不为同一退货流程再建重复审计表。

---

## T4 — Per-route budgets make "cost-aware" falsifiable

"Cost-aware routing" is a slogan unless each route has a declared budget that a
test can check. Target table (to be re-calibrated in Phase 2 against real prompt
sizes — these are placeholders, **not measured values**):

| Route | LLM calls | Retrievals | Tools | Target P50 | Target cost |
|---|---|---|---|---|---|
| DIRECT | 1 | 0 | 0 | < 1.5 s | lowest |
| RAG | 2 | 1 | 0 | < 3 s | low |
| HYBRID | 3 | 3 | 0 | < 4 s | medium |
| TOOL | 2 | 0 | ≤ 3 | < 4 s | medium |
| RAG_TOOL | 3 | 3 | ≤ 3 | < 6 s | high |
| MULTI_HOP | ≤ 6 | ≤ 6 | ≤ 6 | < 12 s | highest |
| CLARIFY / REFUSE / HUMAN | 1 | 0 | 0 | < 2 s | lowest |

The claim to be proved later is of the form: *"N% of queries are answerable on a
single hop; forcing everything through the full agent path moved P95 from X to Y
while accuracy changed by only Z%."*

**Status:** 上表仍是计划，没有路由器或实测承诺。当前先交付可检查的共同边界：
整轮120秒、最多6个step/16个API请求/12个工具/4次知识查询；两个活动会话，超额可见排队。
Agent输入按消息及工具schema的JSON字符计数，最多24,000字符，完整历史仍在SQLite。
核验/embedding/reranker共用请求与耗时预算；SDK隐式重试关闭，失败请求也记账。
这不是原表的多路线成本路由，也不是生产SLA。证据见 `results/runtime_service/`。

---

## T5 — Vocabulary

Use **"mutating"**, not "write", for tools with side effects.

Reason: "write" collides with *writing a Python file* and with *writing an
answer*, both of which occur in this codebase. `READ_ONLY` / `MUTATING` keeps
the tool-safety discussion unambiguous.

---

## 本轮评测交付

单机容器已实际构建运行，包含持久化重建、迁移、索引、并发、服务不可用与管理认证。
新增80段脚本多轮任务，开发40段各1次、留出40段各3次；真实后台状态与语义评分分开。
保留评分输入缺失/时间顺序误判及修正后的同回答评分，不冒充模型提升。原版τ retail
固定5任务各3次，项目14/15、官方同模型15/15，失败与复现见[TASK_EVALUATION.md](TASK_EVALUATION.md)。
T1的自动自然化与去重/不泄漏规则、T4的多路成本路由仍未实现，不将本次脚本语料
与共同预算当作完成了这些计划。新增反馈、人工工单和真实价保办理需另有业务证据。
