# PHASE 0 — Repository Investigation

**Scope:** read six reference repositories on six axes — architecture, what to
reuse conceptually, what *not* to copy, dependency complexity, evaluation
quality, relevant source directories. **No agent code was written in this phase.**

**Method.** Each repo was shallow-cloned to a temp directory (not vendored into
this project — no licence risk, no accidental fork) and read directly. Every
claim below is tied to a file path. Where something could not be confirmed it is
listed under *Unverified claims* rather than asserted.

**Date:** 2026-10-07 · Repos read at their default-branch HEAD.

---

## 0. TL;DR — the ten things that actually matter

| # | Finding | Consequence for us |
|---|---|---|
| 1 | **No repo checks whether the agent made *unnecessary* tool calls — except one.** `dxl-commerce-agent` scores `required_tools` **and `forbidden_tools`** per case | This is the only existing mechanism that directly measures RQ-A's "wasted tool call" objective. **Adopt it.** |
| 2 | **ECom-Bench scores DB state *correctness*, not diff size.** It clones the data dir, lets the agent act, then hashes original vs. modified after replaying the expected actions onto the original | Extra writes do **not** fail the action reward. Adopt the isolation technique, but add a forbidden-action check of our own |
| 3 | **ECom-Bench's reward is multiplicative:** `action × search × output` | A single failure zeroes the score — that is why GPT-4o scores pass^3 = 16.98. We should keep dimensions **separable** and report them individually, or we cannot attribute failures |
| 4 | **Task isolation via per-run UUID cache dir** (`_create_isolated_data`) | Makes parallel trials safe and makes DB-change checking possible at all. Directly reusable pattern |
| 5 | **τ² gives every knowledge task `required_documents`** | Turns retrieval quality into an objective, per-task metric — no LLM judge needed for recall. **Adopt into our golden schema** |
| 6 | **τ²'s retrieval pipeline is a 4-stage pluggable config** (document_preprocessors → input_preprocessors → retrievers → postprocessors) validated with a Pydantic-style schema | This is the right shape for our R0→R4 ablation. Copy the *structure*, not the code |
| 7 | **zhiwei's scorer uses applicability gating** — a metric only counts cases where that field exists | E.g. tool accuracy is scored on 20/100 cases, not 100. Avoids penalising cases for fields they do not have. **Adopt** |
| 8 | **zhiwei's *data* is partly corrupt: all 60 `reference_answer` values are literal `?`** — while `message` is intact | Its published metrics are weakened but not void (both judge paths lean on the intact `required_points` first). Still: **any dataset we adopt must be validated before use** |
| 9 | **A repo with no LICENSE is not reusable.** `WangWeiqiang-UCAS/E-commerce-Smart-Agent` ships no licence file | Study-only. All rights reserved by default |
| 10 | **Only `dxl` and `tau2` have evaluation that is genuinely cheap to run.** ECom-Bench needs per-model credentials filled into `utils.py` for every provider | Plan external benchmarking around τ²/τ³ first; ECom-Bench second |

---

## 1. Cross-repo summary

| Repo | ⭐ | Licence | Deps to run | Evaluation in repo | Take |
|---|---|---|---|---|---|
| `XiaoduoAILab/ECom-Bench` | 30 | Apache-2.0 | LangChain + LangGraph + **Elasticsearch** + MCP | ✅ 53 tasks, tools, mock DB, action/search/output reward, pass^k | Evaluation **design**; external benchmark target |
| `sierra-research/tau2-bench` | 2178 | MIT | 19 deps, light; voice extra optional | ✅ 375 tasks / 4 domains; **698 knowledge docs**; pluggable retrieval configs | Retrieval-ablation structure; **external benchmark** |
| `iwcdoit/enterprise-customer-service-agent-v2` | 38 | MIT | MySQL+Redis+Qdrant+Milvus+OpenSearch | ✅ `evaluate_retrieval.py` (Hit@K, MRR) + `evaluation_queries.json`; 27 test files | Tool Registry / approval / idempotency **design** |
| `whichmen/dxl-commerce-agent` | 127 | MIT | 8 deps, light | ✅✅ 50 curated cases + `forbidden_tools` + 12 test modules | **Eval schema** (required *and* forbidden tools); "facts come from tools" |
| `SR88888888/zhiwei-ecommerce-cs-agent` | 17 | MIT | Chroma+Redis+Elasticsearch+PaddleOCR | ⚠️ 100 cases, good scorer, **corrupt reference answers** | **Applicability gating**; parent-child chunking |
| `moondream69/Multi-Agent-E-commerce-System` | — | MIT | PG + Milvus + Langfuse | ✅ Golden scenarios in `docs/evals/*.yaml` + mechanical anti-fabrication + LLM judge | **Effect-deferred approval**; rubric-based eval |
| `WangWeiqiang-UCAS/E-commerce-Smart-Agent` | 141 | **NONE** | PG+Redis+Celery | ⚠️ 12 test files, functional only | LangGraph layer split (read-only study) |

---

## 2. Benchmarks

### 2.1 `XiaoduoAILab/ECom-Bench` — Apache-2.0

**Architecture.** Task-per-episode with a two-agent loop (LLM user simulator vs.
LLM agent), both driven over MCP stdio servers.

```
main.py / run.py                      CLI: env, num-trials, task range
envs/base.py                          Env base: max_turn=20, session loop
envs/story/env.py                     MockStoryEnv: isolation, loop, reward  ← the core
envs/story/tasks.py                   ALL_TASKS = 53 Task objects (190 KB)
envs/story/data/*.json                mock DB: products 339 KB, logistics 89 KB,
                                      orders 45 KB, users, invoices, discount
agent/servers/offline/server.py       the tool server (21 KB)
agent/servers/offline/tools/*.py      21 MCP tools
user/user.py                          UserBased / UserCoT / UserHuman
utils.py                              Task / Validation / Action / Search models
results2metrics.py                    avg reward, pass^k, per-dimension fail rate
```

**1. How is a task defined?** (`utils.py`)

```python
class Task(BaseModel):
    annotator, user_id, shop_id, platform, instruction, metadata
class Validation(BaseModel):
    outputs:  List[str]      # keywords that must appear in assistant text
    actions:  List[Action]   # name + arguments, expected DB mutations
    searches: List[Search]   # name + arguments, expected tool invocations
```

The `instruction` is a **persona + multi-intent script** for the LLM user
simulator, e.g. `tasks.py[0]` gives 价值敏感型顾客, 情绪略有不满, 信任程度较低,
then 8 sequential intents with **conditional logic** ("如果还没有发货，你希望取消订单").
That conditional structure is what makes tasks genuinely multi-hop rather than
multi-turn.

**2. What is ground truth?** Three separate things, which is the key design point:
* `searches` — the exact tool calls (name **and** arguments) that must occur;
* `actions` — the exact DB mutations that must occur;
* `outputs` — keyword strings that must appear in assistant turns.

**3. How is success decided?** (`envs/story/env.py`)

```python
reward = 1 * reward_actions * reward_searches * reward_outputs   # multiplicative!
```

- **action** — `_create_isolated_data()` copies the JSON DB into a per-run UUID
  cache dir; the agent mutates that; then `calculate_actions_reward()` loads
  original and modified, recursively **normalises** (sort dict keys, sort
  comparable lists), SHA-256 hashes both, replays the expected `actions` onto the
  *original*, and compares hashes. So it asserts *"the final DB looks as if
  exactly these actions had been applied"*.
- **search** — `search_data_hash.issubset(service_data_hash)`; expected tool
  calls must be a **subset** of observed calls. Extra calls are tolerated.
- **output** — `all(output in long_string for output in task.metadata.outputs)`
  over concatenated assistant text. Pure substring containment.
- **time** — `(max_limit - avg_time) / (max_limit + avg_time)`, i.e. a smooth
  inverse curve in (0, 0.5]; **not** part of the multiplicative reward.

`results2metrics.py` computes `pass^k` exactly as in the τ-bench paper:
`E_task[ C(c,k) / C(n,k) ]`.

**4. Why is action-based reward more reliable than an LLM judge?** Because it is
**stochastic-input invariant**. The user simulator may phrase things differently
every trial, and the answer text may vary arbitrarily, yet the DB end state after
a correctly solved task is fixed. So the reward is reproducible across trials
while still allowing natural conversation variation — which is exactly what
`pass^k` needs to be meaningful. An LLM judge would inject a second stochastic
process and make pass^k measure judge noise as much as agent reliability.

**5. What can serve as our external benchmark?** Tasks, tools, mock DB and the
reward implementation are all reusable as a *target*. Caveats below.

**What to reuse conceptually**
- Per-run UUID isolation + hash comparison for DB correctness (technique).
- Separate `actions` / `searches` / `outputs` ground truth.
- `pass^k` via the combination formula.
- Conditional (branching) intents inside the user instruction.

**What NOT to copy**
- **Do not adopt the multiplicative reward.** `action × search × output` makes
  failure attribution impossible: a score of 0 does not say *which* dimension
  broke. Report the three dimensions separately and use a conjunction only for a
  headline "fully correct" rate. (This is the main reason GPT-4o pass^3 = 16.98 —
  that number is a conjunction of three conditions plus a strict DB hash.)
- **Do not adopt hash-equality for actions as-is.** It cannot express "a
  *forbidden* mutation must not happen". Combined with subset-matching for
  searches, the benchmark tolerates over-calling. Related to finding #1.
- The dependency set (`langchain-elasticsearch`, `langgraph`, `mcp`,
  `mcp-cli`, `openai-agents`) is heavier than our whole project.

**Dependency complexity: moderate–high.** `requirements.txt` is 18 packages, but
`langchain-elasticsearch` implies an Elasticsearch service, and the MCP servers
are spawned as `python` subprocesses requiring the right interpreter.

**Evaluation quality: high in design, degraded in practice.**
- ✅ Complete: tasks, tools, DB, reward code, pass^k.
- ⚠️ `utils.py` hardcodes **blank** API keys and model names for every provider
  (`ChatZhipuAI(model=" ")`, `AzureChatOpenAI(azure_endpoint=" ", ...)`). Every
  provider must be hand-filled before anything runs.
- ⚠️ `results2metrics.py` has three **hardcoded result filenames** that do not
  exist in the repo, so the metrics script cannot run out of the box.
- ⚠️ 53 tasks only, and a single vertical (home appliances/furniture). Too few
  for tight confidence intervals on any sub-metric.

**Relevant directories:** `envs/story/` (env + tasks + data), `utils.py`,
`agent/servers/offline/tools/`, `user/`, `results2metrics.py`.

---

### 2.2 `sierra-research/tau2-bench` (τ² + τ³) — MIT

**Architecture.** The most mature harness read here. 15 modules under `src/tau2/`:
`agent/`, `user/`, `environment/`, `evaluator/`, `metrics/`, `orchestrator/`,
`runner/`, `knowledge/`, `domains/`, `data_model/`.

**Domains and task counts** (`data/tau2/domains/`):

| domain | files | tasks |
|---|---|---|
| `banking_knowledge` | 815 (**698 documents**) | **97** |
| `retail` | 9 | 114 |
| `telecom` | 23 | 114 |
| `airline` | 6 | 50 |

`pyproject.toml` confirms `requires-python = ">=3.12,<3.14"` — **this validates
our Python 3.13 pin** made in Step 0.

**Knowledge task schema** (`data/tau2/domains/banking_knowledge/tasks.json`):

```json
{
  "id": "task_001",
  "description": {"purpose": "...", "relevant_policies": null, "notes": null},
  "user_scenario": {"persona": null, "instructions": "You are playing the role of a customer..."},
  "evaluation_criteria": {
      "actions": [{"name": "apply_for_credit_card", "arguments": {...},
                   "requestor": "user", "action_id": "001_0"}],
      "communicate_info": [],
      "reward_basis": ["DB"]
  },
  "user_tools": ["apply_for_credit_card"],
  "required_documents": ["doc_credit_cards_gold_rewards_card_001", ...]
}
```

Two fields are worth stealing outright:

- **`required_documents`** — the exact documents needed to solve the task. This
  makes retrieval objectively scorable per task without any judge.
- **`user_tools`** — tools the *user simulator* may call. This lets the simulated
  customer take actions (e.g. apply for a card), which is how it models a user
  who completes part of the task themselves.

**Retrieval pipeline** (`src/tau2/knowledge/`) is a clean 4-stage config, validated
by `config.py`:

```
document_preprocessors → input_preprocessors → retrievers → postprocessors
   (bm25_indexer,          (embedding_encoder)    (bm25,        (identity,
    embedding_indexer)                             cosine,       bge_reranker,
                                                   grep,         qwen_reranker,
                                                   full_kb)      pointwise_llm_reranker)
```

`config.py` enforces the shape: `retriever` XOR `retrievers`, everything a list,
every entry has a `type`. **This is the correct structure for our R0→R4 ablation** —
we should mirror the stage names so our configs are legible to anyone who knows τ².

Rerankers are concrete and adoptable as *options*: `bge_reranker.py` (7.4 KB),
`qwen_reranker.py` (9.8 KB), `pointwise_llm_reranker.py` (4.2 KB).
`embeddings_cache.py` (23 KB) caches document embeddings and invalidates on
content change — the same concern we flagged in Step 0.

**What to reuse conceptually**
- 4-stage retrieval pipeline + config validation schema.
- `required_documents` as an objective per-task retrieval target.
- `reward_basis` as an explicit declaration of *which* signals score a task
  (`["DB"]`, `["DB","COMMUNICATE"]`) — this is applicability gating done
  declaratively, at the task level.
- `user_tools` for a user simulator that acts.

**What NOT to copy**
- Voice/audio stack (`voice/`, `livekit-*`, `elevenlabs`, `deepgram`, `pyaudio`)
  — out of scope and a heavy install.
- `litellm` as the LLM layer: it is a large dependency and hides which provider
  actually served a request, which works against our per-call cost attribution.
- The sandbox-shell retrieval tools (`terminal_use`, `alltools`) need Anthropic's
  `sandbox-runtime` plus `bwrap`/`socat`/`rg` — unnecessary risk for us.

**Dependency complexity: low–moderate.** 19 runtime deps, all pip-installable, no
external service. `banking_knowledge` is offline-capable with the `bm25` config
(needs no API key). Optional extras inflate this sharply — install
`tau2[knowledge]` only, never `[voice]`/`[live]`.

**Evaluation quality: highest of the six.** Real harness, real metrics module,
per-domain splits, pass^k via `mean_and_pass_hat_k`, leaderboard infrastructure.
The one caveat: `banking_knowledge` is a **banking** domain, not e-commerce.

**Relevant directories:** `src/tau2/knowledge/`, `src/tau2/evaluator/`,
`src/tau2/metrics/`, `src/tau2/orchestrator/`, `src/tau2/domains/banking_knowledge/`,
`data/tau2/domains/banking_knowledge/`.

---

## 3. Engineering references

### 3.1 `whichmen/dxl-commerce-agent` — MIT ⭐127

**Architecture.** Message-ingest workers → unified `IncomingMessage` → decision
API → tool gateway → reply, per-platform workers under `workers/`.

**What to reuse conceptually**
- **⭐ The evaluation schema — the single most valuable artifact found.**
  `evals/cases.jsonl`, 50 cases, fully deterministic:

  ```json
  {"id": "greeting-zh",
   "tags": ["routing"],
   "request": {"tenant_id":"demo","channel":"sandbox","store_id":"STORE-001",
               "customer_id":"CUST-0042","message_id":"eval-greeting-001",
               "text":"你好"},
   "expect": {"intent": "greeting",
              "status": "resolved",
              "required_tools": [],
              "forbidden_tools": ["order_lookup","logistics_lookup","product_lookup"],
              "deduplicated": false}}
  ```

  `forbidden_tools` is the mechanism nobody else has. For a greeting, calling
  `order_lookup` is a **failure** — that is a wasted tool call, measured
  directly. This is exactly RQ-A's objective, and it is checkable without a judge.
  Also note `message_id` + `deduplicated` for idempotency, and `status` for
  terminal state.
- **Facts come from tools.** The tool gateway is a separate authenticated HTTP
  service; the model never invents business facts. Enforced structurally, not by
  prompt.
- **Evaluation is first-class in packaging:** `dxl-agent-eval` is a console
  script entry point, and `evals/` + `policies/` are force-included in the wheel.

**What NOT to copy**
- Six-platform integration (Appium, Android `AccessibilityService`, Chrome CDP,
  Playwright profiles). High legal/stability risk, no relevance to the agent
  research question, and it is where most of the 233 files go.
- The OpenClaw runtime dependency. We want no runtime we do not control.

**Dependency complexity: lowest of the six** — 8 runtime deps
(`fastapi`, `Pillow`, `playwright`, `pydantic`, `pycryptodome`, `python-multipart`,
`PyYAML`, `uvicorn`). Playwright is only needed by the platform workers.

**Evaluation quality: high and cheap.** 50 deterministic cases + 12 test modules
(`test_runtime.py` 24 KB, `test_workers.py` 17 KB, `test_openclaw_runtime.py`
14 KB, `test_eval_runner.py`). Distinguishes *offline* evidence from *synthetic
suite* evidence.

**Relevant directories:** `evals/`, `src/dxl_agent/eval_runner.py`,
`src/kefu_tool_backend/`, `policies/`, `tests/`.

---

### 3.2 `iwcdoit/enterprise-customer-service-agent-v2` — MIT ⭐38

**Architecture.** Hexagonal: `api/` → `services/` → `domain/` →
`infrastructure/` → `tools/` → `workflows/`. Clean separation; the *only* repo
here that keeps a `domain/` layer independent of framework code.

```
domain/      confirmations, cost, human_support, knowledge_lifecycle,
             memory, planning, query_rewrite, routing, schemas, trace
tools/       after_sales_tools, default_registry, handoff_tools,
             order_tools, search_tools
workflows/   context, customer_service_graph, nodes (64 KB!), runtime, state
```

**What to reuse conceptually**
- **`domain/confirmations.py`** and the Approval-Token pattern — the structural
  basis for our framework-level two-phase commit (TODO T2).
- **`domain/routing.py`** — routing as its own domain concept, separate from the
  graph. Validates our decision to make the router a pure function.
- **`domain/knowledge_lifecycle.py`** — document version / effective-date / hash
  management. Directly relevant since our KB deliberately contains
  new-vs-old policy documents (TODO T4, step-0 risk).
- **`scripts/evaluate_retrieval.py`** — computes **Hit@K and MRR on the retrieval
  chain without calling any chat model.** That is the right shape for our
  retrieval ablation: fast, cheap, deterministic. Paired with
  `knowledge_base/evaluation_queries.json` (3.4 KB).

**What NOT to copy**
- **`workflows/nodes.py` is 64 KB.** That is the anti-pattern our Phase-1 decision
  was written to avoid: business logic accumulated inside graph nodes, untestable
  without running the graph. Strong evidence for keeping v1 out of LangGraph.
- The infrastructure: MySQL + Redis + Qdrant/Milvus + OpenSearch + Postgres
  checkpointing. Five services before hello-world.

**Dependency complexity: highest.** `requirements.txt` is 32 packages and names
four separate datastores; `docker-compose.yml` is 8.7 KB.

**Evaluation quality: good where it counts.** `evaluate_retrieval.py` (Hit@K/MRR)
is genuinely useful and model-free. 27 test files including
`test_hybrid_rag.py`, `test_confirmations_and_mcp.py`,
`test_cost_governance_service.py`, `test_knowledge_lifecycle.py`. But no
end-to-end task-success evaluation and no golden task set.

**Relevant directories:** `src/customer_service_app/domain/`,
`.../tools/default_registry.py`, `.../workflows/state.py`,
`scripts/evaluate_retrieval.py`, `knowledge_base/evaluation_queries.json`.

---

### 3.3 `SR88888888/zhiwei-ecommerce-cs-agent` — MIT ⭐17

Read closely, because it publishes the exact metrics table we were shown as a
target. **The published numbers and the shipped code do not line up.**

**Architecture.** Flat Python at the root plus a newer `apps/api` service:
`agent.py`, `intent.py`, `rag.py`, `memory.py`, `dialog.py`, `mcp_server.py`,
`vectorstore.py`, `es_client.py`, `evaluation.py`, `run_eval.py`.

**Evaluation quality: mixed, and worth being precise about.**

*What is genuinely good:*
- **100 real cases** in `apps/api/evals/customer_service_v1.jsonl`. The README's
  `evals/` layout is accurate; my first check looked in the wrong directory and
  was wrong.
- **A well-designed case schema** (`apps/api/app/evaluation/dataset.py`) with
  validators that reject malformed cases: `expected_tool` requires
  `expected_terminal_state`; first-turn assertions require `turns`; `turns[0]`
  must equal `message`; duplicate `case_id` rejected. Case content is real:
  `required_points`, `forbidden_points`, `reference_answer`, multi-turn `turns`,
  `expected_tool_params`, `expected_intent`.
- **⭐ Applicability gating** in `scorers.py`: `tool_applicable`,
  `recall_applicable`, `rag_applicable`, `task_applicable`, each `None` when not
  applicable. So tool accuracy is scored on the **22** cases that declare a tool,
  not on all 100. This is the correct semantics and we should adopt it.
- A real LLM judge (`judge.py`) using strict function-calling with a
  `_json_fallback`, 3 retries each, `temperature=0`, and `record_usage` for cost.

*What does not line up with the README:*
- **All 60 `reference_answer` values are corrupt.** They contain literal `U+003F`
  (`?`) runs — 1080 `?` characters in the file — while `message`,
  `expected_intent` and `required_points` are intact UTF-8 Chinese. A lossy
  encoding conversion clearly ran over that one field, and it is not mentioned in
  the README.
  *Impact is limited but real:* both judge paths put `required_points` first and
  the local fallback never uses `reference_answer` at all. But a reference answer
  of `"?????????????????4K 60p?"` is still fed to the judge as a field, and can
  only add noise.
- **`run_eval.py` (root) is a different, much weaker harness than the one the
  README describes.** It loads `test_data/eval_cases.json` — **33** cases, in a
  different schema (`expected_intent`, `keywords`) — not the 100-case set.
- **The README's "意图路由准确率 99%（99/100）" is not what the code computes.**
  `run_eval.py` collapses labels to `chitchat` vs `non_chitchat`:
  ```python
  coarse_actual = "chitchat" if act_intent == "chitchat" else "non_chitchat"
  ```
  A binary task that a trivial majority classifier solves at 95% (the set is
  60 % `knowledge_query`). The API scorer does compare fine-grained intents, but
  sits behind a different entry point.
- **"检索召回率 Recall@2" is not Recall@2.** In `evaluation.py`,
  `eval_retrieval(answer, keywords)` counts keyword hits **inside the generated
  answer** and divides by the number of keywords. It measures keyword coverage of
  the answer text, not retrieval. `scorers.py` does better (checks
  `retrieved_source_ids[:2]` **and** that required points appear in the cited
  evidence) — that is closer to a genuine Recall@2.
- **No cost accounting in either harness.** `0.132998 CNY` cannot be reproduced;
  `run_eval.py` never computes cost. `apps/api` does have `app/usage.py` and
  `record_usage`, so cost *is* tracked there.
- `judge.py` contains `"reason": "????"` and `"?????????"` as Chinese string
  literals — the same corruption pattern in source, which suggests a
  console/encoding step in the author's workflow rather than a one-off.

**What to reuse conceptually**
- Applicability gating (finding #7).
- Parent-child chunking (README): parent = whole section in Postgres, child =
  500 chars / 80 overlap, embedded into pgvector; retrieve on children, answer
  with the parent block. Good, standard, and cheap.
- `expected_tool_params` per-key checking — separates "chose the right tool" from
  "filled the right arguments". Worth keeping as two metrics.

**What NOT to copy:** `chromadb` + `redis` + `elasticsearch` together (three
stores for one job), and `paddleocr` + `paddlepaddle` (very heavy) for PDF
ingestion.

**Dependency complexity: high for its size** — ChromaDB, Redis, Elasticsearch,
`paddleocr`, `paddlepaddle`, `sentence-transformers`, `pymupdf`, `jieba`.

**Relevant directories:** `apps/api/app/evaluation/`, `apps/api/evals/`,
`apps/api/app/usage.py`, `vectorstore.py`, `es_client.py`.

---

### 3.4 `moondream69/Multi-Agent-E-commerce-System` — MIT

**What to reuse conceptually** (explicitly **not** the multi-agent part):
- **Effect-deferred approval** (ADR-0005): a high-risk action registers a
  parameter snapshot and produces **no external effect**; a human approves; then
  a single transaction applies the batch, with same-batch atomicity and
  whole-batch rollback on snapshot drift. This is the strongest existing
  implementation of the pattern behind our TODO T2 and T3.
- **`docs/adr/0008-agent-eval-golden-scenarios-and-judge.md` (8.8 KB)** — golden
  scenarios as YAML plus **mechanical anti-fabricated-citation checks combined
  with an LLM judge**. The two-track scoring (mechanical + judged) is the right
  compromise: mechanical checks cannot be talked around, the judge covers
  semantics.
- Citation provenance: `[n]` markers resolve to chunk id, document title, issuing
  body, page and chunk number.

**What NOT to copy:** the Manager/supervisor fan-out architecture, the
multi-agent decomposition, PG+Milvus+Langfuse+frontend stack, and the 303-file
footprint. Out of scope for v1 by definition.

**Dependency complexity: high** (Postgres, Milvus, Langfuse, React frontend).

**Evaluation quality: good and opinionated.** 15 data files, golden scenarios as
the source of truth, deterministic + judged double scoring, scores projected to
Langfuse.

**Relevant directories:** `docs/adr/0008-*`, `docs/evals/`,
`python-backend/src/python_backend/.../evals/`.

---

### 3.5 `WangWeiqiang-UCAS/E-commerce-Smart-Agent` — ⚠️ **NO LICENCE FILE**

**Licence finding: there is no `LICENSE` file.** Under default copyright, all
rights are reserved. **Nothing may be copied from this repo.** It can be read for
ideas only, and even that should not produce copied code.

**What to reuse conceptually (ideas only)**
- The `app/graph/` split: `state.py`, `nodes.py`, `tools.py`, `workflow.py`.
  Readable, and a reasonable shape — though note that `nodes.py` accumulating
  logic is the same drift that produced enterprise-v2's 64 KB `nodes.py`.
- A high-risk refund path that escalates to a human review queue with a Gradio
  admin console (task queue, session replay, one-click decision), and Celery for
  the irreversible parts (refund payment, SMS).

**Dependency complexity: high** — Postgres+asyncpg, pgvector, Redis,
`langgraph-checkpoint-redis`, Celery, Gradio, Alembic, `passlib`/`bcrypt`.

**Evaluation quality: weak.** 12 test files but all functional/version tests
(`test_v2_complete.py`, `test_v4_complete.py`), no metric computation, no golden
set, no task-success measure. One data file (`scripts/verify_db.py`).

**Relevant directories (read-only):** `app/graph/`, `app/services/refund_service.py`,
`app/models/`, `test/test_refund_rules.py`.

---

## 4. "README-driven development" findings

Repos where a claimed metric could **not** be tied to runnable code:

| Repo | Claim | Status |
|---|---|---|
| `zhiwei` | 意图路由准确率 99% (99/100) | ⚠️ Code computes **coarse binary** chitchat/non-chitchat in `run_eval.py`; the fine-grained scorer is a different entry point |
| `zhiwei` | 检索召回率 Recall@2 95% | ⚠️ `evaluation.py` measures keyword hits in the **answer**, not retrieval. `scorers.py` is closer |
| `zhiwei` | 总成本 0.132998 CNY | ❌ No cost computation in either harness (`apps/api` tracks usage but does not report this figure) |
| `zhiwei` | sample size 100 | ✅ True — `apps/api/evals/customer_service_v1.jsonl`. The root CLI uses a different 33-case set |
| `ECom-Bench` | 53 tasks, pass^k results | ✅ Code and data present; but `results2metrics.py` hardcodes filenames of result files absent from the repo |
| `ECom-Bench` | GPT-4o pass^3 = 16.98 | ⚠️ Not independently reproducible here: needs provider credentials hand-filled in `utils.py`, plus an Elasticsearch-dependent stack |
| `enterprise-v2` | Hybrid RAG quality | ✅ `evaluate_retrieval.py` computes Hit@K/MRR; runnable but needs 4 datastores |
| `dxl` | 50 offline evals | ✅ `evals/cases.jsonl` is exactly 50 lines and `dxl-agent-eval` is an installed entry point |
| `tau2` | 375 tasks / 698 docs | ✅ Counts verified directly |

**Pattern worth remembering:** in every case the *data* was real and the *metric
computation* was where claims drifted. So when we evaluate our own README later,
the thing to make reproducible is the **metric code path**, not the dataset.

---

## 5. Licence table

| Repo | Licence | Reusable? |
|---|---|---|
| `XiaoduoAILab/ECom-Bench` | Apache-2.0 | ✅ with attribution + NOTICE |
| `sierra-research/tau2-bench` | MIT | ✅ |
| `iwcdoit/enterprise-customer-service-agent-v2` | MIT | ✅ |
| `whichmen/dxl-commerce-agent` | MIT | ✅ |
| `SR88888888/zhiwei-ecommerce-cs-agent` | MIT | ✅ |
| `moondream69/Multi-Agent-E-commerce-System` | MIT | ✅ |
| `WangWeiqiang-UCAS/E-commerce-Smart-Agent` | **NONE** | ❌ **ideas only, no code** |

Note `ECom-Bench`'s own README states it is built on `tau-bench` and thanks
Sierra Inc. It also carries an explicit notice that its environment contains
simulated user data and is for non-production testing only, referencing PIPL
compliance. **We must not present ECom-Bench results as production evidence**,
and must keep that notice intact if we vendor anything.

---

## 6. Unverified claims

Recorded rather than assumed:

1. **ECom-Bench's `_remove_isolated_data()` runs inside `calculate_actions_reward()`
   before the hash comparison** — it is called after both datasets are loaded
   into memory, which works but means cleanup happens even if the comparison
   later throws. Whether this leaks cache dirs on exception paths was not traced.
2. **Whether `pass^k` in `results2metrics.py` is correct for unequal trial counts
   per task.** The formula assumes every task has `num_trials` trials; the code
   groups by `task_id` but divides by a global `comb(num_trials, k)`. It is
   probably fine because the runner enforces equal trials, but the guard was not
   located.
3. **Whether τ²'s `qwen_reranker` / `bge_reranker` run offline.** They appear to
   be local model wrappers, but this was not executed.
4. **Whether `zhiwei`'s `reference_answer` corruption predates or postdates its
   published metrics.** Cannot be determined from the repo.
5. **Whether `dxl`'s 50 cases all pass deterministically.** The runner was read,
   not executed; it requires the OpenClaw runtime for a real run.
6. **`moondream69`'s actual eval pass rate.** Golden scenarios exist; no results
   were read.

---

## 7. What we will actually do

**Adopted into our golden-set schema (Phase 1 deliverable):**

1. `required_tools` **and `forbidden_tools`** per case — from `dxl`. This is our
   only direct instrument for RQ-A's "wasted tool call" objective.
2. `required_documents` per case — from τ². Objective retrieval scoring, no judge.
3. **Applicability gating** — from `zhiwei`. A metric is computed only over cases
   where it applies, and each metric reports `passed/total` **with its coverage**.
4. Separate `expected_tool` from `expected_tool_params` — from `zhiwei`. "Right
   tool, wrong arguments" is a distinct, actionable failure.
5. `reward_basis` as a declarative per-case field — from τ². Makes applicability
   explicit in the data instead of inferred by the scorer.

**Adopted into the retrieval layer (Phase 2):**

6. Mirror τ²'s 4-stage pipeline naming
   (`document_preprocessors` / `input_preprocessors` / `retrievers` /
   `postprocessors`) so R0→R4 ablations are config-driven and legible.
7. Adopt the `evaluate_retrieval.py` shape — Hit@K and MRR over the retrieval
   chain **with no chat model in the loop**, so the ablation is cheap and
   deterministic.
8. Parent-child chunking from `zhiwei` (child 500/80 for retrieval, parent
   section for generation).

**Adopted into the tool layer (Phase 3):**

9. Per-run data isolation by copying the DB into a run-scoped directory, and
   correctness by comparing against the replayed expected state — from ECom-Bench.
   **Extended:** also assert that *forbidden* mutations did not occur, which
   neither ECom-Bench nor dxl covers at the DB level.
10. Effect-deferred approval with snapshot + transactional apply — from
    `moondream69` (this is TODO T2/T3).

**Explicitly rejected:**

- Multiplicative reward (ECom-Bench) — destroys failure attribution.
- Business logic inside graph nodes (`enterprise-v2`'s 64 KB `nodes.py`) —
  confirms the Phase-1 decision to keep v1 out of LangGraph.
- Multi-agent orchestration, voice, Elasticsearch, Milvus, MySQL, Kafka.
- Any code from `E-commerce-Smart-Agent` (no licence).

**External benchmark plan:** τ²/τ³ `banking_knowledge` **first** (MIT, light deps,
offline `bm25` config, and its retrieval configs double as a design reference),
then `retail` for tool-use. `ECom-Bench` second — closest domain match, but it
requires hand-filling provider credentials and an Elasticsearch-depending stack,
and its 53 tasks give wide confidence intervals. Its own non-production notice
applies.
