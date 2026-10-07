# Adaptive Customer Service Agent

**Cost-aware Routing, Hybrid Retrieval and Reliable Tool Execution**

An e-commerce customer-service agent built as an *experiment harness*, not a demo.
Every module exists to answer one question:

> What was broken, what did I change, how much did it improve, and what did it cost?

Domain (v1, deliberately narrow): **wireless headphones + charging accessories**.
Business scope: product Q&A, order lookup, logistics tracking, returns/exchanges,
price protection. See [`docs/SCOPE.md`](docs/SCOPE.md).

---

## Why this repo exists

A fixed `query → embed → top-k → LLM` pipeline is a toy. A stack of
LangGraph + RAG + Memory + MCP + Multi-Agent is not a project either — it is a
pile of components with no measured contribution.

This repo keeps the discipline instead:

```
Baseline → Evaluation → Failure Analysis → Improvement → Ablation → Result
```

A module is only added when the failure analysis proves it addresses a
measured bottleneck, and it must come with a delta on success rate, latency,
token usage and cost — including any regression it introduces.

## Non-goals for v1

No multi-agent, no GraphRAG, no memory service, no microservices, no Milvus,
no fine-tuning, no real platform APIs, no multimodal, no voice.
See `docs/SCOPE.md` for the full out-of-scope list.

---

## Status

| Phase | Deliverable | State |
|---|---|---|
| Step 0 | Environment + real LLM call + cost calibration | ✅ done |
| Phase 0 | `docs/REPO_ANALYSIS.md` (6 repos, concepts only) | ⬜ next |
| Phase 1 | `docs/SCOPE.md` + `docs/ARCHITECTURE.md` | ⬜ |
| Phase 2 | `data/docs` (18) + `data/db` (200 orders) + `data/eval/dev.jsonl` (40) | ⬜ |
| Phase 3 | Baseline A (dense RAG) / B (tool agent) / C (unified) | ⬜ |
| Phase 4 | Evaluation runner → `results/baseline/` | ⬜ |
| Phase 5 | `docs/FAILURE_ANALYSIS.md` + Top-3 candidate improvements | ⬜ |

Adaptive routing and the mutation guard are **intentionally not implemented yet**.
They are candidate *answers*; the failure analysis has to ask the question first.

---

## Quickstart

```bash
# 1. environment
uv sync --extra dev

# 2. configure
cp .env.example .env
$EDITOR .env                      # set ACS_LLM_API_KEY

# 3. verify config (never prints the key)
uv run acs info

# 4. Step 0 gate: one real call + cost projection
uv run python scripts/smoke_llm.py

# 5. tests (no API key required)
uv run pytest
```

## Layout

```
src/acs/
  config.py        resolved config + token pricing
  llm.py           OpenAI-compatible client, per-call accounting
  kb/              knowledge base loading + chunking        (Phase 2)
  retrieval/       dense / bm25 / rrf / rerank / pipeline   (Phase 2+)
  tools/           registry, schemas, read/write, guard     (Phase 3)
  agent/           router + explicit control loop           (Phase 3)
  obs/trace.py     structured JSONL trace
  eval/            runner, metrics, failure taxonomy        (Phase 4)
data/
  docs/            L1 knowledge base (Markdown + front-matter, git-versioned)
  db/              L2 business database (SQLite, seeded)
  eval/            L3 golden set (dev.jsonl / test.jsonl)
configs/           one YAML per experiment
experiments/       experiment configs + recorded results
results/           summary.json / per_case.jsonl / failures.jsonl
docs/              SCOPE, ARCHITECTURE, REPO_ANALYSIS, FAILURE_ANALYSIS, EXPERIMENT_LOG
```

## Documentation

| File | Purpose |
|---|---|
| `docs/ARCHITECTURE.md` | Data flow, the 5 design decisions, module contracts |
| `docs/TODOS.md` | Open design refinements not yet implemented |
| `docs/SCOPE.md` | In/out of scope, tools, DB schema, eval schema (Phase 1) |
| `docs/REPO_ANALYSIS.md` | What to reuse / not copy from 6 reference repos (Phase 0) |
| `docs/EXPERIMENT_LOG.md` | One entry per experiment: commit, config, dataset, result |
| `docs/FAILURE_ANALYSIS.md` | Failure taxonomy distribution + next-step candidates (Phase 5) |
