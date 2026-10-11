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
| `pytest` | 原记录 129 未获证据支持；审核用当前依赖回放对应源码为 98，见 PROJECT_REVIEW.md |
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

---

## E002 — 事实与测量修复后的工具调用对照

**Date:** 2026-10-08
**Commit:** before `b74eced` / initial `9c633de` / after `4724792`
**Status:** complete — 315 次在线尝试已完成，原始结果保留并统一按 v4 离线重评分。

本轮先修复数据、评分和统计范围，再比较工具选择提示与描述。
原始 105 次 baseline 保留为历史证据，其旧评分与耗时口径不参与本次性能对照。

| 字段 | 固定条件或版本 |
| --- | --- |
| model | deepseek-flash |
| embedding | Qwen/Qwen3-Embedding-8B |
| prompt_version | 两组 summary.run.prompt_sha256；工具描述版本见 conditions.json |
| dataset_version | 三组相同的 35 个问题与用户，各 3 遍；判据统一使用 v4 重新评分 |
| kb_version | 相同的 knowledge_sha256 与 index_sha256 |
| temperature / thinking | 0.0 / false |
| top_k / max_steps | 3 / 6 |
| seed | 未设置；温度 0 不代表服务输出完全确定 |
| reference_date | 2026-10-08 |
| pricing | 固定 peak=true，USD/CNY=7.2；embedding 未定价，成本为 LLM 估算小计 |

实际运行命令，两组共用同一个临时合成数据库：

```bash
ACS_DB_PATH=/tmp/acs-tool-efficiency-m9bx40sj/business.db ACS_REFERENCE_DATE=2026-10-08 ACS_LLM_ASSUME_PEAK=true uv run python scripts/run_eval.py --passes 3 --out results/tool_efficiency/before
ACS_DB_PATH=/tmp/acs-tool-efficiency-m9bx40sj/business.db ACS_REFERENCE_DATE=2026-10-08 ACS_LLM_ASSUME_PEAK=true uv run python scripts/run_eval.py --passes 3 --out results/tool_efficiency/after
```

初版在 `9c633de` 运行时也使用 `after` 目录，完成后移至 `initial` 保留。
三组在线运行共 315 次；用以下命令按当前评分规则生成共同口径的对照，原始文件不变：

```bash
uv run python scripts/summarize_tool_efficiency.py
```

重新实验时，使用本节指定的源码版本，以固定参考日期重新生成数据库，分别选择新输出目录；
原文件拒绝覆盖。运行条件和 hash 见 [conditions.json](../results/tool_efficiency/conditions.json)，
主要问题与结果解读见 [PROBLEM_SOLVING.md](PROBLEM_SOLVING.md)。

### Result

共同 v4 判据下，before / initial / after 的规则成功率为 93.3% / 89.5% / 100%，
严格成功率为 63.8% / 86.7% / 95.2%，工具调用数为 160 / 123 / 122。
最终比基线减少 23.8% 工具调用；平均 token 增加 4.2%，p50 延迟基本持平。
原始报告的版本为 v2 / v2 / v3，最终两条规则误判有成对正反例验证，详见问题记录。

保留全量接口错误，并另报两组均无执行错误的 99 个同一尝试；未按任务是否成功筛选。
该子集平均耗时 3278 → 2773 ms，但网络与缓存波动仍未完全控制。
成本仅为固定费率下的 LLM 小计，依据 [供应商价目](https://api-docs.deepseek.com/quick_start/pricing/)
核对费率，不代表实际完整扣费。

147 项离线测试、ruff、35 条业务事实校验通过；共同口径对照可由脚本离线复算。
首轮优化跳过必要政策的失败被保留。后续应扩充独立用例与多轮流程，
而不是将这批 35 条用例的 100% 当作通用质量结论。

## E003 — 新问题验证、覆盖扩充与检索拒答

**Date:** 2026-10-08 至 2026-10-09；业务参考日固定为 2026-10-08。
**Status:** 完成首次验证、公开开发回归和检索修复；写操作尚未实现。

| Field | Value |
| --- | --- |
| commit | 首次验证 `f8b098b`；开发基线 `f6cd51e`；首版过滤 `e1eb715`；修正检索 `db238d6` |
| model | deepseek-flash；Qwen/Qwen3-Embedding-8B |
| prompt_version | 正式提示词 SHA `7ac76014e198d0807d6a07b18f5bef94f51ed0bad44bab026327df962cf2e6d1`，工具描述不变；消融仅隔离进程变化 |
| dataset_version | holdout.yaml 15 条首次验证；development.yaml 70 条公开开发、含 10 条多轮；具体 SHA 见各 summary.run |
| kb_version | 正文 SHA `d80b1c841915869422ca059a68100ee67f3ea299ea5ba054f07a36877cad15fc` 不变；51 → 48 小节；切分版本 5，商品标题保留编号 |
| scoring_version | 新增首次验证 v4；三组开发回归均 v5，未改用例或放宽判据 |
| temperature / thinking | 0.0 / false |
| top_k / max_steps | 3 / 6 |
| seed | 未设置；每阶段单遍，不证明稳定性 |
| pricing | peak=true，汇率 7.2；embedding 未定价，小计不能当作完整账单 |

冻结 15 条新增问题后才改变行为；首次 11/15、严格 10/15，包含一次 embedding 错误，
即使最终回答正确也不剔除。原提示词复跑 12/15，四项消融 11–13/15；
小样本与一次复跑不能按任意分数阈值断言过拟合，也不能据此删除安全规则。
用例作者已读过代码，此集不是严格盲测；看过结果后不再称为未见集。

公开开发集三阶段规则成功 53 → 58 → 58 /70，严格成功 50 → 55 → 57 /70；
文档命中 32 → 30 → 33 /37，库外拒答 0 → 4 → 4 /4。首版回退保留后，
补商品编号并仅对实测分数重叠区间核验证据；没有引入 Judge 作为评测主判据。
最终多轮仍 7/10，中位耗时 2216 → 2487 ms，平均 token 基本持平，未证明性能改善。
最终 5 次核验、2111 token、小计 0.002870 元全部记账。

真实检索采样 81 条：有效问题保留 57/58，库外拒绝 23/23；validation 子集 10+6 均通过。
阈值只用 calibration/regression 选择，不用 validation；其余公开开发 query 参与修复，
因此开发回归不能充当独立泛化证据。未命中候选与狭窄文本规则误判继续披露。

实际命令（临时合成数据库已冻结在 2026-10-08；重新执行请指定新输出目录）：

```bash
ACS_DB_PATH=/tmp/acs-review-20261008/business.db ACS_REFERENCE_DATE=2026-10-08 ACS_LLM_ASSUME_PEAK=true uv run python scripts/run_eval.py --cases data/eval/holdout.yaml --passes 1 --out results/holdout/first
ACS_DB_PATH=/tmp/acs-review-20261008/business.db ACS_REFERENCE_DATE=2026-10-08 ACS_LLM_ASSUME_PEAK=true uv run python scripts/run_prompt_ablation.py --out results/holdout/ablation
ACS_DB_PATH=/tmp/acs-review-20261008/business.db ACS_REFERENCE_DATE=2026-10-08 ACS_LLM_ASSUME_PEAK=true uv run python scripts/run_eval.py --cases data/eval/development.yaml --passes 1 --out results/review_regression/after_v2
uv run python scripts/calibrate_relevance.py --extra-samples data/eval/retrieval_regression_samples.yaml --threshold-from data/eval/relevance_calibration.json --out results/relevance/evidence_gate.json
```

对照条件自动校验了数据集、提示词、数据库、知识正文、核心非检索源码与模型配置，
原始记录不覆盖，检索和索引版本分别留档。
证据：[开发对照](../results/review_regression/comparison.json)、
[首次验证](../results/holdout/first/summary.json)、
[采样核验](../results/relevance/evidence_gate.json)；原因与逐步纠正见
[PROBLEM_SOLVING.md](PROBLEM_SOLVING.md#6-旧索引会返回过期政策库外问题也总能命中)。

验证：201 项离线测试与 ruff 通过；新版服务实际浏览器检查通过，评测页显示四阶段与
历史 v5 回放。发票问题检索为空并拒答，P002 LDAC 命中 `headphone_b#5`；
两次回答完成前分别观察到 58 / 119 次正文增量更新，输入框可见并恢复，无页面脚本错误。
证据：[真实浏览器记录](../results/ui_review/browser_check.json)。

## E004 — 章节排序对照：Dense、BM25 与 RRF

**Date:** 2026-10-11（Asia/Shanghai）。
**Status:** 开发选择与冻结验证完成；不含独立 query rewrite、reranker 或售后写操作。

| 条件 | 固定值 |
| --- | --- |
| 起点 | `b1128fb`；主提示词不改 |
| 数据 | `retrieval_cases.json`，先冻结；32 条开发（24 有依据、8 库外），20 条验证（14/6） |
| 语料 / 索引 | 相同 8 文档、48 小节；v5 指纹与 FAISS SHA 见 frozen_manifest |
| 模型 | Qwen/Qwen3-Embedding-8B；正文核验 deepseek-flash，temperature=0、thinking=false |
| 检索参数 | 每路候选 10，融合后最多 10；上下文最多 3；BM25 k1=1.5、b=0.75，RRF k=60 |
| 中文处理 | jieba 搜索分词、HMM=false；统一大小写，保留 SKU、接口、协议与数字单位 |
| 门控 | Dense 使用既有 cosine 标定；BM25/RRF 对前三个候选做正文证据核验 |
| 统计口径 | 逐问题证据组 Recall；任一充分章节覆盖同组事实，多组事实分别要求 |

先写 [冻结清单](../results/hybrid_retrieval/frozen_manifest.json)，再运行开发对照：

```bash
uv run python scripts/run_retrieval_eval.py --split development --out results/hybrid_retrieval/development
```

Dense / BM25 / RRF 的开发排序 Recall@3 为 **81.2% / 100% / 97.9%**，
核验后为 **77.1% / 100% / 97.9%**，库外拒绝均 8/8。候选 Recall@10 均 100%，
说明当前主要缺口在排序与门控。BM25 开发所需证据全部保留 24/24，RRF 23/24；
RRF 的失败是一般退货问题遗漏质量条件，并没有因融合自动获得全面规则。

将 BM25 选为默认，写入 [部署选择](../results/hybrid_retrieval/deployment_decision.json)，
再执行验证；不根据验证结果调标签、分词、融合或门控：

```bash
uv run python scripts/run_retrieval_eval.py --split validation --out results/hybrid_retrieval/validation
```

冻结验证的核验后 Recall@3 为 **92.9% / 100% / 100%**，库外拒绝 **5/6 / 6/6 / 6/6**。
Dense 对 P001 游戏延迟误拒，对 P002 颜色问题误接受。三方案共 156 个检索尝试均无
执行错误；全部原始记录保留。20 条验证由读过语料的作者编写，不是严格盲测，公开后
不能继续用作未见集。单遍与小样本不足以证明泛化和稳定性。

开发配对检索 p50 为 **930 / 527 / 1312 ms**，验证为 **1402 / 730 / 2174 ms**。
同一 query vector 供 Dense/RRF 复用，执行顺序轮换；耗时按共享 embedding 的实测值
加排名与核验用时计算，不是三组独立 Agent 请求，也未控制服务缓存与网络波动。
最多三个片段是相同上下文数量预算，片段 token 长度可不同，不能当作严格等 token 对照。

开发正文核验调用为 **4 / 32 / 32**，token 为 **1840 / 16765 / 15044**；
验证为 **4 / 20 / 20**、**1531 / 9990 / 9099**。实际请求账本含共享 embedding 与
全部核验，embedding 未定价；不据此宣称完整成本下降。
检索评测不生成回答，覆盖改善不能直接表述为回答正确率提升。

复现文件 hash、依赖版本、逐条候选与所有失败见
[完整对照](../results/hybrid_retrieval/comparison.json)、
[开发记录](../results/hybrid_retrieval/development/per_case.jsonl)、
[验证记录](../results/hybrid_retrieval/validation/per_case.jsonl)。
前端新增对照与只读回放；RRF 分数、各路排名与 Dense cosine 分开标示。

验证：216 项离线测试、ruff、156 条记录的指标离线复算通过，现有 v5 索引与冻结语料
一致。真实浏览器确认六行对照与回放切换，无页面脚本错误；一般退货回答同时提到
7 天无理由与 15 天质量条件，P002 快充回答约 4 小时，会员积分问题无命中并说明缺少
规则。三次回答分别观察到 90 / 74 / 81 次聊天正文变化，输入框可见且完成后可再次输入。
这些是三条功能检查，不能当作端到端评测成功率。
证据：[浏览器记录](../results/hybrid_retrieval/browser_check.json)。

## E005 — API 重排与核验后补位

**Date:** 2026-10-11（Asia/Shanghai）。
**Status:** 五方案开发消融与首次新增验证完成；部署选择在验证运行前冻结。

| 条件 | 固定值 |
| --- | --- |
| 起点 | `1cc2ea3`；主提示词、业务工具与知识正文不变 |
| 用例 | 原 52 条全部转开发（38 有依据 / 14 库外）；20 条新增验证（14/6） |
| 语料与索引 | 同一 8 文档、48 小节，切分 v5；文件 hash 见 frozen_manifest |
| 候选与上下文 | 每路 10、RRF 后最多 10、最终最多 3；BM25 k1=1.5、b=0.75、RRF k=60 |
| 重排 | 硅基流动 `BAAI/bge-reranker-v2-m3`，输入标题＋正文，返回完整候选排名；timeout=15s |
| 正文核验 | deepseek-flash，temperature=0、thinking=false、JSON 索引；timeout=60s |
| 补位 | 先核验整个有界候选池，再取前三个有效片段；核验池与最终上下文分别记录 |
| 对照 | BM25、RRF、RRF＋补位、RRF＋重排、RRF＋重排＋补位 |
| 成本假设 | LLM 固定 peak=true、汇率 7.2；embedding 与重排未定价 |

先冻结 [用例与条件](../results/rerank/frozen_manifest.json)，开发问题用于选择，
不依据检索命中筛掉问题。新验证运行前复核标签时，发现“列全音频编码”不能由只提到
LDAC 的段落充分支持，收紧该条标签并重新冻结；没有执行任何新验证 query。
原 17 条已完成开发问题的 85 个尝试及原冻结文件保留在 `initial_interrupted`，
中断中的请求可能没有完整用量记录，不将其视为免费请求，不纳入正式对照。

```bash
uv run python scripts/run_rerank_eval.py --split development --out results/rerank/development
```

开发核验后 Recall@3 为 **100% / 98.68% / 100% / 100% / 100%**，
完整证据组覆盖 **38 / 37 / 38 / 38 / 38**（分母 38），库外均拒绝 14/14。
RRF 排序 MRR 0.877，重排后 1.000；一般退货遗漏的第四名期限表被补入。
核验 token 分别 **26755 / 24143 / 66683 / 27966 / 66679**，
两种重排方案均另需 52 次逻辑请求、61632 token（实验实际共享同一次重排，不能重复加总）。
重排本身 API p50 **302 ms**。补位核验 token 约增至原 RRF 的 2.76 倍。

BM25 同样覆盖全部开发证据且消耗更低，因此先写
[部署选择](../results/rerank/deployment_decision.json)，保留 BM25、关闭重排与补位，
再执行首次新增验证：

```bash
uv run python scripts/run_rerank_eval.py --split validation --out results/rerank/validation
```

| 首次新增验证 | BM25 | RRF | RRF＋补位 | RRF＋重排 | 重排＋补位 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 候选 Recall@10 | 92.86% | 100% | 100% | 100% | 100% |
| 排序 Recall@3 | 85.71% | 75% | 75% | 85.71% | 85.71% |
| 核验后 Recall@3 | 85.71% | 75% | 100% | 85.71% | 100% |
| 完整证据组覆盖 | 11/14 | 10/14 | 14/14 | 11/14 | 14/14 |
| 库外误接受 | 0/6 | 0/6 | 0/6 | 0/6 | 0/6 |
| 核验 token | 10147 | 9317 | 25453 | 10144 | 25456 |
| 额外重排 token | 0 | 0 | 0 | 24953 | 24953 |
| 配对检索 p50 / ms | 628 | 1072 | 995 | 1426 | 1500 |

20 条新问题暴露了多事实覆盖缺口；重排 MRR 为 0.929，但没有保证两种条件全部入选。
同事实的多个相关章节仍会挤掉退款时效或输入电压等第二项事实；补位恢复了参考证据组。
验证后审计发现 `charger-warranty-difference` 的冻结标签遗漏了 `charger_a#5` 的质保卡，
该片段也可支持 18 个月期限。保留原标签与首次分数，明确这是参考章节覆盖，
不将未命中指定章节判作生成答案错误；标签审计说明见 comparison.json。
后续应扩充并冻结新的充分替代标签，不能把本批已公开问题继续当作未见集。

正式两组共 360 个检索尝试无执行错误。查询 embedding、BM25/RRF 排名及一次重排
在同一问题内共享，正文核验独立执行且轮换顺序。共有 504 条客户端逻辑调用账本记录，
计费未知与 SDK 内部重试不能据此视为零消耗。单遍、服务缓存和网络没有完全控制，
开发 RRF p95 达 22.9 秒，主要受 embedding 慢请求影响；不比较不同日期结果来宣称性能提升。
最终片段数同为 3，但正文核验与最终 token 长度不同，不能声称严格等 token 实验。

开发配对检索 p50 **576 / 1606 / 1768 / 1977 / 1953 ms**，含核验与共享请求的实测耗时，
不含回答生成，不是独立端到端 Agent 请求。重排结果高分仍须事实核验，不用 cosine 标定。
缺少请求用量时标明部分 token；所有错误保留，不静默回退。

离线复算命令：

```bash
uv run python scripts/summarize_rerank.py
```

该脚本从逐条记录独立核对 360 条证据组覆盖、总数、错误与共享账本，结果见
[comparison.json](../results/rerank/comparison.json)，原始报告不改写。
前端新增十行五方案对照与回放，原 E004 对照保留。
启用重排的真实浏览器检查：退货回答含 7/15 天，会员积分无依据拒答；
正文分别有 121/82 次增量变化，输入框可见并恢复，无页面脚本错误。
证据：[browser_preview.json](../results/rerank/browser_preview.json)。

最终页面检查确认十行重排消融与六行 E004 对照同时可见，切换
`charger-input-output` 回放后，原候选、核验池与重排消耗可查看，输入框可用，无脚本错误。
证据：[browser_progress.json](../results/rerank/browser_progress.json)。
237 项离线回归与 ruff 通过，正式数据和 v5 索引保持一致；整轮实现、测试、文档和
评测证据合并为一个完整可运行的提交。
