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
