# Experiment Log

One entry per experiment. The rule from the project brief: every entry records
enough that the run can be reproduced or invalidated later.

Required fields per experiment:

```
commit | model | prompt_version | dataset_version | kb_version |
temperature | top_k | seed | command | result
```

An entry with a missing field is an unfinished entry.

---

## E000 — Step 0: environment + real cost/latency calibration

**Date:** 2026-10-07
**Commit:** see the commit that adds this entry
**Status:** ✅ complete — both the LLM and embedding endpoints verified with real calls

### Purpose

Not an experiment. This is the gate that has to pass before any phase is
meaningful: prove the toolchain reaches real services, and find out what a call
actually costs and how long it takes.

Rationale for doing it first: "cost-aware routing" is the project's central
claim. Without a measured per-call cost and latency, no routing result
downstream can be calibrated, and the claim stays unfalsifiable.

### Environment

| Item | Value |
|---|---|
| OS | WSL2 Ubuntu, kernel 6.6.114.1 |
| Package manager | uv 0.11.8 |
| Python | 3.13.13 (pinned in `.python-version`) |
| openai | 3.26.0 |
| pydantic | 2.13.5 |
| typer | 0.27.3 |
| pytest | 9.1.1 |
| ruff | 0.16.10 |

**Interpreter decision.** uv initially selected Python 3.14.4. That was rejected
and 3.13 pinned instead, because τ³-bench (the intended external benchmark for
the knowledge-retrieval domain) requires Python 3.12–3.13 and cannot coexist
with τ²-bench in one environment. Discovering this at Phase 6, after the agent
and evaluator exist, would have forced an environment rebuild or dropping the
external benchmark. Pinning now costs nothing.

### Config

| Field | Value |
|---|---|
| llm_provider | deepseek |
| llm_model | **deepseek-flash** (DeepSeek-V4.1-Flash) |
| llm_base_url | https://api.deepseek.com |
| llm_temperature | 0.0 |
| llm_max_output_tokens | 1024 |
| embedding_provider | siliconflow |
| embedding_model | Qwen/Qwen3-Embedding-8B |
| embedding_dim | **4096** (observed, then pinned) |
| seed | 42 |
| prompt_version | n/a — smoke prompts only |
| dataset_version | none yet |
| kb_version | none yet |
| top_k | n/a |
| peak hours at run time | no (off-peak pricing) |

### Command

```bash
uv sync --extra dev
uv run acs info
uv run python scripts/check_connectivity.py
uv run pytest
uv run ruff check .
```

### Result

| Check | Result |
|---|---|
| `uv sync --extra dev` | 29 packages installed |
| `uv run acs info` | config resolves; key detected as set |
| `uv run pytest` | **50 passed** |
| `uv run ruff check .` | All checks passed |
| `scripts/check_connectivity.py` | **LLM OK, Embedding OK** |

**Measured LLM calls** — first run, before thinking mode was pinned:

| stage | tokens in/out | cached | latency | cost CNY | reply |
|---|---|---|---|---|---|
| router | 98 / 209 | 0 | 2326 ms | 0.001009 | `{"route": "RAG_TOOL", "confidence": 0.9}` |
| generate | 95 / 518 | 0 | 3371 ms | 0.002340 | grounded answer citing the 15-day window |
| extract | 78 / 222 | 0 | 1565 ms | 0.001043 | `{"order_id": "A202600317", "intent": "查询发货状态"}` |

**Measured LLM calls** — after pinning `thinking: disabled` (the configuration used from here on):

| stage | tokens in/out | reasoning chars | latency | cost CNY |
|---|---|---|---|---|
| router | 72 / 17 | 0 | 980 ms | 0.000151 |
| generate | 69 / 74 | 0 | 889 ms | 0.000394 |
| extract | 52 / 20 | 0 | 549 ms | 0.000143 |

- 3 calls, 304 tokens total, 2418 ms cumulative
- **average 0.000229 CNY per LLM call** (was 0.001464 with thinking on — 6.4× cheaper)
- **projection: a 120-case × 3-pass run ≈ 0.08 CNY** (floor: assumes 1 call/case)

**Direct thinking-mode comparison** (identical router prompt, one call each):

| mode | latency | output tokens | reasoning chars | cost CNY |
|---|---|---|---|---|
| disabled | 1096 ms | 17 | 0 | 0.000151 |
| enabled | 2786 ms | 393 | 1455 | 0.001804 |

→ **2.5× latency, 23× output tokens, ~12× cost** on a call whose entire job is
emitting ~20 tokens of JSON. Reproducible via `scripts/check_connectivity.py`.

**Embedding:** 2 vectors, **dim = 4096**, cosine similarity between two unrelated
inputs = 0.528 (i.e. not a constant vector).

### Findings that changed the plan

1. **Python 3.14 → 3.13 pin** (above).

2. **The published price table made the project's framing wrong.** The original
   `PRICING` table listed `deepseek-chat` / `deepseek-reasoner` — both retired —
   and `.env.example` still pointed at `deepseek-chat`. The current table has
   `deepseek-flash` and `deepseek-v4-pro`, with two properties that matter:
   - a **cache-hit input token is ~50× cheaper** than a cache-miss one
     (0.003 vs 0.15 USD/1M), and a RAG service resends the same system prompt on
     every call, so ignoring the cache rate **overstates cost**;
   - **off-peak prices are exactly half of peak**, and peak is defined in UTC, so
     an unpinned run can cost double purely because of when it ran. `peak` is now
     an explicit, testable parameter rather than a silent clock read.

3. **Cost is not a meaningful lever at this price point; latency is.**
   0.000229 CNY per call means a 120-case × 3-pass run costs about **0.08 CNY**.
   Even a 10× cost regression would be under 1 CNY. Meanwhile the cheapest
   possible router call still takes ~1.0 s.

   This changes how Research Question A must be stated. The original framing
   ("reduce Token / Latency / Cost") implies the three are comparable levers.
   The measurements say otherwise on this tier. The honest framing is:
   **latency and wasted tool calls are the primary objective; cost is reported
   but is not the binding constraint.** Presenting a cost delta as a headline
   result would be misleading at these magnitudes. Re-measure if the model tier
   changes or run volume grows by orders of magnitude.

4. **Thinking mode was on by default and silently broke determinism.**
   DeepSeek enables thinking at `effort=high` by default, and
   **thinking mode ignores `temperature` entirely** — the API accepts the
   parameter and does nothing with it. So the first three calls in this entry
   were *not* temperature-deterministic despite `temperature=0.0`, and the repo
   would have recorded `temperature: 0.0` in every trace while that was false.

   Fixed: `thinking` is now an explicit parameter, **defaulting to OFF**, with
   the flag recorded in every usage record; `temperature` is omitted rather than
   sent-and-ignored when thinking is on; and `reasoning_chars` is tracked so a
   silent spike in chain-of-thought length cannot surface only as an unexplained
   cost increase. The 2.5×/23× table above is produced by
   `scripts/check_connectivity.py` on every run, so the claim stays reproducible.

   **This is the single highest-value finding of Step 0.** It would have
   corrupted every latency and cost number in the project, and — worse — every
   A/B comparison, because thinking length varies per case and would have looked
   like route-dependent cost.

5. **Router latency is a first-class bottleneck even with thinking off.** ~1.0 s
   for a 72-token input whose job is emitting ~17 output tokens. Any adaptive
   router adds this to *every* request, including the DIRECT path it exists to
   make cheap. So the DIRECT budget cannot assume the router is free, and a
   cheap rule-first path is not a nicety — it is the only way DIRECT stays fast.
   To be re-measured with the real router prompt in Phase 3.

6. **`/v1/embeddings` as a base URL fails.** The OpenAI SDK appends
   `/embeddings` itself, so the configured value produced a doubled path and a
   404. Now guarded in code with an explicit warning rather than silently
   "fixed", because a silent rewrite would hide the misconfiguration.

7. **A config bug surfaced only because `.env` was real.** `ACS_EMBEDDING_DIM=`
   (blank) crashed settings loading, because pydantic cannot coerce `''` to
   `int`. Now a validator treats a blank value as unset. This would have hit
   every new contributor, since `.env.example` ships that way.

### Regression risk carried forward

- **`PRICING` is hand-maintained.** A stale table is exactly the bug found here.
  Mitigated by tests pinning the arithmetic and by an unpriced model reporting
  as `None` rather than `0.0`. **Re-verify against the pricing page before
  quoting any cost in the README**, and re-check the USD→CNY rate (currently
  hardcoded at 7.2).
- **The smoke prompts are ~100 tokens; real prompts will be larger.** A real RAG
  prompt carries retrieved chunks, so both cost and latency will be higher than
  this projection. Re-measure in Phase 2 with real prompt sizes.
- **Latency here is a single sample per stage.** Three samples is not a
  distribution; the router's P50/P95 must be established over the whole dev set
  in Phase 4, and grouped by route rather than averaged.
- **The `thinking` decision is a hypothesis, not a settled result.** Thinking is
  disabled because it costs 2.5× latency for a JSON-emitting task. But it is
  plausible that thinking *improves* accuracy on multi-hop policy reasoning.
  Phase 4 should A/B this on the dev set and keep it only if the accuracy gain
  justifies the latency — the same standard applied to every other module.
- **`deepseek-flash` supports a 1M context.** Not exercised here. Long-context
  behaviour (retrieval stuffing vs. many small calls) is an open design choice
  for Phase 2, and context caching makes repeated long prompts much cheaper than
  the naive per-token arithmetic suggests.

### Next

Phase 0 — repository investigation → `docs/REPO_ANALYSIS.md`.
No agent code until baseline and evaluation exist.

---

## E001 — MVP validation: agent loop, dense RAG, tool calling

**Date:** 2026-10-07
**Commit:** the commit that adds this entry
**Status:** ✅ complete — all four demo cases run end to end, in the UI and in the CLI

### Purpose

Not a benchmark. This entry establishes that the naive unified agent runs at all,
and records the first observed behaviour so later changes have a baseline to be
compared against. **It is explicitly not evidence that the agent is good** —
four hand-written cases cannot support that claim.

### Environment

| Item | Value |
|---|---|
| Python | 3.13.13 |
| LLM | `deepseek-flash`, OpenAI-compatible, `api.deepseek.com` |
| Embedding | `Qwen/Qwen3-Embedding-8B` via SiliconFlow, dim 4096 |
| Vector index | FAISS `IndexFlatIP` over normalised vectors (score = cosine) |
| DB | SQLite `data/db/business.db` — 25 orders / 4 products / 19 logistics rows |
| UI | Gradio 6.29.1, single process, `127.0.0.1:7860` |
| Deps | openai 3.26, pydantic 2.13.5, numpy 2.5.3, faiss-cpu 1.15.1 |

### Config

| Field | Value |
|---|---|
| llm_thinking | **false** (see E000 — 2.5× latency, 23× output tokens if on) |
| llm_temperature | 0.0 |
| llm_max_output_tokens | 1024 |
| chunk_size / chunk_overlap | 700 / 80 (ceiling only; see chunking note) |
| top_k | 3 |
| max_steps | 6 |
| seed | 42 |
| prompt_version | v1 (`SYSTEM_PROMPT` in `service_agent.py`) |
| dataset_version | none — 4 ad-hoc cases in `scripts/run_demo_cases.py` |
| kb_version | 8 documents, 51 chunks, git-tracked under `data/docs/` |

### Command

```bash
uv run python scripts/init_db.py
uv run python scripts/build_index.py
uv run python scripts/run_demo_cases.py
uv run python app.py
uv run pytest && uv run ruff check .
```

### Result

| Check | Result |
|---|---|
| `pytest` | **129 passed** |
| `ruff check .` | All checks passed |
| UI | HTTP 200 on `127.0.0.1:7860`; all expected panels render |
| Demo case 1 (RAG only) | 1 tool call, 1.66 s, 2886 tokens, ¥0.001436 |
| Demo case 2 (tools only) | 2 tool calls, 2.19 s, 3069 tokens, ¥0.001428 |
| Demo case 3 (RAG + tools) | 2 tool calls, 2.53 s, 3512 tokens, ¥0.002110 |
| Demo case 4 (cross-user) | 0 tool calls, 1.08 s, 1177 tokens, ¥0.000462 |

Retrieval hits were on-target for case 1 and 3: `return_policy#2` at 0.715 for
"无线耳机退货期限", and `return_policy#1` at 0.745 for the combined query.

### Findings that changed the implementation

1. **Chunking was wrong twice, and only inspecting retrieved output revealed it.**
   - v1 packed sections up to `chunk_size=600` → **15 chunks for 8 documents**,
     so one vector covered both "7-day returns" and "quality returns".
   - v2 packed with an overlap prefix that truncated the buffer → **1 chunk per
     document**. The top hit for a returns question became the *headphone product
     page*, because the nearest neighbour of a 2400-character vector is whichever
     topic the document spends most words on.
   - v3 treats a **section as the chunk** → 51 chunks, and every probe query
     returned the passage a human would quote.

   This is the single most useful lesson of the phase: **the failure was
   invisible in the code and obvious in the scores.** Chunk counts (15 → 1 → 51)
   were the signal.

2. **The agent misreported a date, so date arithmetic moved into SQL.** In an
   early case-3 run the answer correctly said "within 7 days" but stated the
   delivery date as 10-08 when the row said 10-07. The cause is structural, not
   model-specific: the tool returned raw timestamps and a day count, leaving
   conversion to the model, on a machine whose clock is not the server's.
   `database.py` now returns `today`, `delivered_date`, `delivered_days_ago`,
   `within_return_window`, `within_price_protection` and an explicit instruction
   to use them. **Generalisation: any derived value the model must not
   recompute should be computed in the data layer.**

3. **A model-supplied identity argument was only blocked by accident.** A
   scripted test sent `current_user_id` as a tool argument. It did not leak —
   Python raised `multiple values for keyword argument`. That is safe only
   because the parameter names happen to collide. `cs_tools.py` now strips
   `user_id` / `current_user_id` from the model's payload, substitutes the
   session identity, and emits an `identity_override_rejected` event so the
   attempt is visible in the trace.

4. **Tool calls grew from 1 to 2 in case 2 once the prompt required both order
   and logistics.** Before that the agent answered "where is it" using only
   `get_logistics`. The order status was never fetched. Behaviour worth watching:
   the *order* of the two calls is not stable across runs.

5. **Gradio 6 moved API surfaces.** `css` moved from `Blocks()` to `launch()`;
   `Chatbot(type=...)` and `launch(show_api=...)` were removed. Verified against
   `inspect.signature` rather than guessed from documentation.

### Regression risk carried forward

- **The index is a build artefact and is not committed.** Changing an embedding
  model without rebuilding is caught by a model check in `KnowledgeBase.load()`,
  but changing *chunking* is not — `chunk_size` is stored in the metadata yet
  never compared. Rebuild after any change to `data/docs/` or `rag.py`.
- **`PRICING` in `config.py` is still hand-maintained** (unchanged from E000).
- **`max_steps=6` has been reached by nothing so far**, so the ceiling is
  untested against real traffic — only against a scripted infinite-loop model.
- **Two `config.py` copies exist conceptually**: the MVP added `docs_dir`,
  `chunk_size`, `chunk_overlap`, `top_k`, `max_steps`. None of these are in
  `.env.example` yet, so they are code defaults only.

### Next

Not another module. The observed defects (unstable tool ordering, a title-only
chunk occupying a top-k slot, weak product-level retrieval, cross-user refusal
never reaching the SQL guard) are listed in `README.md` under Bad Cases, and the
proposed order of work is justified there **from those observations** rather than
from the earlier design documents.
