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

**Measured LLM calls** (off-peak, temperature 0, `deepseek-flash`):

| stage | tokens in/out | cached | latency | cost CNY | reply |
|---|---|---|---|---|---|
| router | 98 / 209 | 0 | 2326 ms | 0.001009 | `{"route": "RAG_TOOL", "confidence": 0.9}` |
| generate | 95 / 518 | 0 | 3371 ms | 0.002340 | grounded answer citing the 15-day window |
| extract | 78 / 222 | 0 | 1565 ms | 0.001043 | `{"order_id": "A202600317", "intent": "查询发货状态"}` |

- 3 calls, 1220 tokens total, 7261 ms cumulative
- **average 0.001464 CNY per LLM call**
- **projection: a 120-case × 3-pass run ≈ 0.53 CNY** (floor: assumes 1 call/case)

**Embedding:** 2 vectors, **dim = 4096**, cosine similarity between two unrelated
inputs = 0.5285 (i.e. not a constant vector).

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
   0.001464 CNY per call means a 120-case × 3-pass run costs about **0.5 CNY**.
   Even a 10× cost regression would be ~5 CNY. Meanwhile a single short router
   call took **2.3 s**.

   This changes how Research Question A must be stated. The original framing
   ("reduce Token / Latency / Cost") implies cost is comparable in weight to
   latency. The measurements say otherwise on this tier. The honest framing is:
   **latency and wasted tool calls are the primary objective; cost is reported
   but is not the binding constraint.** Reporting a cost delta as a headline
   result would be misleading at these magnitudes. This will be re-measured if
   the model tier changes or the run volume grows by orders of magnitude.

4. **Router latency is a first-class bottleneck, not a detail.** 2.3 s for
   98 input tokens on a prompt whose entire job is emitting ~20 output tokens.
   Any adaptive router adds this to *every* request, including the DIRECT path
   it is supposed to make cheap. So the DIRECT route's budget cannot assume the
   router is free, and a cheap rule-first path is not an optimisation nicety —
   it is the only way DIRECT stays fast. To be re-measured with the real prompt
   in Phase 3.

5. **`/v1/embeddings` as a base URL fails.** The OpenAI SDK appends
   `/embeddings` itself, so the configured value produced a doubled path and a
   404. Now guarded in code with an explicit warning rather than silently
   "fixed", because a silent rewrite would hide the misconfiguration.

6. **A config bug surfaced only because `.env` was real.** `ACS_EMBEDDING_DIM=`
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
- **`deepseek-flash` supports a 1M context and thinking mode by default.** The
  measured calls did not disable thinking. If thinking is being invoked, much of
  the 2.3 s router latency may be reasoning tokens rather than network overhead.
  **Phase 3 must confirm whether thinking mode is on and pin it**, since it
  changes the latency budget and the `max_output_tokens` semantics.
- Latency here is a single sample per stage. Three samples is not a distribution;
  the router's P50/P95 must be established over the real dev set in Phase 4.

### Next

Phase 0 — repository investigation → `docs/REPO_ANALYSIS.md`.
No agent code until baseline and evaluation exist.
