# Open design refinements

Decisions agreed during review that are **not yet implemented**. Each entry
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

**Status:** not implemented. Needed at Phase 2.

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

**Status:** not implemented. Needed at Phase 3.

---

## T3 — Idempotency must be observable in the data, not just in code

With a fully mocked SQLite backend there is no real side effect, so a duplicate
write would be invisible. Therefore every write operation also appends a row to
an **operation audit table** keyed by `idempotency_key`.

This makes "the same logical action was attempted twice" detectable from the
database state alone, which is what the evaluator inspects.

**Status:** not implemented. Needed at Phase 2 (schema) + Phase 3 (guard).

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

**Status:** table is a plan; real numbers arrive in Phase 4.

---

## T5 — Vocabulary

Use **"mutating"**, not "write", for tools with side effects.

Reason: "write" collides with *writing a Python file* and with *writing an
answer*, both of which occur in this codebase. `READ_ONLY` / `MUTATING` keeps
the tool-safety discussion unambiguous.
