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

## E000 — Step 0: environment + cost calibration

**Date:** 2026-10-07
**Commit:** `a7518cb` (superseded by the commit that adds this file)
**Status:** scaffolding complete; **the LLM call itself is pending an API key**

### Purpose

Not an experiment. This is the gate that has to pass before any phase is
meaningful: prove the toolchain runs, and find out what one LLM call costs.

Rationale for doing it first: "cost-aware routing" is the project's central
claim. If a single call's cost is unknown, no cost claim downstream can be
calibrated, and any routing result would be unfalsifiable.

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
and evaluator are written, would have forced either an environment rebuild or
abandoning the external benchmark. Pinning now costs nothing.

### Config

| Field | Value |
|---|---|
| llm_provider | deepseek |
| llm_model | deepseek-chat |
| llm_base_url | https://api.deepseek.com/v1 |
| llm_temperature | 0.0 |
| llm_max_output_tokens | 1024 |
| seed | 42 |
| prompt_version | n/a (smoke prompts only) |
| dataset_version | none yet |
| kb_version | none yet |
| top_k | n/a |

### Command

```bash
uv sync --extra dev
uv run acs info
uv run python scripts/smoke_llm.py
uv run pytest
uv run ruff check .
```

### Result

| Check | Result |
|---|---|
| `uv sync --extra dev` | 29 packages installed |
| `uv run acs info` | config resolves; API key correctly detected as set/placeholder |
| `uv run pytest` | **38 passed** |
| `uv run ruff check .` | **All checks passed** |
| `uv run python scripts/smoke_llm.py` | **NOT RUN — requires a real API key** |
| measured cost per call | **unknown** |
| cost projection for one eval run | **unknown** |

The script prints a projection table (40 / 120 / 200 cases × passes) once a real
call succeeds. Those numbers are placeholders until then.

### Findings that changed the plan

1. **Python 3.14 → 3.13 pin** (above).
2. **The cost-accounting module needed its own tests before any call was made.**
   An unpriced model returns `None` rather than `0.0`, so a stale price surfaces
   as "unknown" in a report instead of quietly reading as free. This is pinned by
   `tests/test_cost_accounting.py`.
3. **A secret scanner was added to a Step 0 commit**, which is unusual. Reason:
   a portfolio repo is public, a leaked key is irreversible, and the first
   one-line version of the guard produced a false positive on the documented
   placeholder in `.env.example` — the exact behaviour that teaches an author to
   bypass a guard. Writing its tests then found three further bugs, all pinned:
   substring marker matching exonerated real keys; AWS's documented example key
   was reported as real; and a marker set containing the generic word `key`
   silently exonerated every credential whose material contains it.
4. **The scanner needed an escape hatch that stays visible.** Shifted
   fingerprints in test fixtures tripped it 18 times. Marking specific lines with
   `check_secrets:allow` was chosen over allowlisting the file, and the number of
   markers is capped, so the exemption cannot silently become "the scanner is
   off here".

### Regression risk carried forward

- `PRICING` in `src/acs/config.py` is hand-maintained. If the provider changes
  prices, the constant is wrong until someone updates it. Mitigation: a test
  pins the arithmetic, and an unpriced model reports as unknown. **Re-verify the
  price table before quoting any cost figure in the README.**
- The smoke prompts are ~1 sentence each. They are a poor proxy for real prompt
  sizes, so the cost projection is a floor, not an estimate. Re-measure in
  Phase 2 once real router/generation prompts exist.

### Next

Phase 0 — repository investigation → `docs/REPO_ANALYSIS.md`.
No agent code until baseline and evaluation exist.
