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

### Two guards that must ship with T1

1. **No-new-entity guard.** After paraphrase, verify the query introduces no
   entity or number absent from the structured case (order id, amount, date,
   product name, platform name). A paraphrase that adds a new one is dropped.
2. **Answer-invariance guard.** Re-run retrieval on the paraphrased query and
   require that `expected_docs` are still in top-k. Otherwise drop the
   paraphrase.

   Why this matters: a paraphrase can add a *distractor domain word*. E.g.
   `"耳机降价了能退差价吗"` → `"我买的耳机昨天在京东降价了，能补差价吗"` — the
   word 京东 sends retrieval toward platform policy, which this KB has no
   dimension for. The agent then answers "wrongly" for a data-construction
   reason, and the failure gets mis-attributed to the agent.

3. **Provenance in the trace.** Every case records
   `query_source: template | paraphrase` and `paraphrase_model`. A paraphrase
   batch is itself an experimental variable.

**Status:** not implemented. Needed at Phase 2.

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
