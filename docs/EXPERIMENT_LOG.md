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

## E006 — 已认证的持久会话（阶段 1）

**Date:** 2026-10-12（Asia/Shanghai）。
**Status:** 阶段 1 完成；任务字段、确认提交和审核为后续阶段。

起点为 `76b0f21`，先冻结 [10 项验收](../data/eval/session_cases.json) 与
[业务事实指纹](../results/session_memory/frozen_manifest.json)，再实现持久化。
基线没有持久会话；新增测试首先因存储模块不存在而未能收集，这不是一个线上模型
成功率基线。用离线数据与固定脚本模型验证结构，再做少量真实浏览器检查。

演示账户认证使用当前 Gradio 内置接口，回调读取服务端身份；登录凭据不进模型上下文。
SQLite 追加迁移 v1，已有业务表、知识正文与索引不变；新表按会话与用户校验。
同一会话最多一个进行中请求，完整轮次原子保存，未完成工具组不能写为完成记录。
正常启动恢复仅支持单进程；跨进程读取完成历史不影响正在处理的请求。

验证命令：

```bash
uv run pytest
uv run ruff check .
```

最终完整回归 249 项通过，另加账户配置不覆盖和不输出密码的回归并通过；总计 250 项。
测试数据库按用例复制隔离，避免新会话写入污染共享种子。依靠这些约束的结构性测试
不需要 API 或网络。业务事实迁移前后校验记录见 `verification.json`。

真实浏览器检查匿名调用被 401 拒绝，登录后可读取自己的订单、保存原始工具轮次、
刷新后显式恢复、退出再登录继续追问、新会话为空及另一用户隔离。
实际重启前注入一个未完成只读请求；重登后历史保留，该请求标为中断且未自动重放。
首轮查询与恢复后追问分别有 197 / 11 次正文变化，页面无脚本错误。
记录见 [browser_check.json](../results/session_memory/browser_check.json)。

初始验证脚本没有等登录表单完成渲染，随后又对原生小写标签使用大写精确选择，
以及对虚拟表格使用不适用的整表文本方式。原失败记录全部保存；确认应用接口正常后
调整等待和 DOM 定位。后续账户与重启检查复用已完成的模型检查记录，不额外复跑模型。
这些脚本错误不计为应用或模型错误，也不报告稳定性、端到端任务成功率或延迟改善。

演示密码存入本机 `.env`，代码只提供配置模板；独立账户用于客户身份，管理员角色
随阶段 4 实现。业务工具依然只读，当前功能不创建真实售后申请或退款。

## E007 — 模拟用户与持久退货草稿（阶段 2）

**Date:** 2026-10-12（Asia/Shanghai）。
**Status:** 阶段 2 交付；E006 的客户登录按用户要求移除，历史认证结果仍保留。

起点 `66378c2`，先冻结 [任务场景](../data/eval/task_memory_cases.json) 和
[业务指纹](../results/task_memory/frozen_manifest.json)。基线缺少 `list_orders` 和草稿
存储；新增的 17 项回归首先失败。实现 v2 非破坏式迁移，保留 v1 会话与原业务表。
最终新增暂停正反例与 UI 场景，完整回归 271 项、Ruff 通过。

在线条件：`deepseek-flash`、temperature=0、thinking=false、BM25、无重排/补位，
相同 6 段、11 轮原始对话。使用应用实际 `on_send` 流式处理器，复制当前业务库到
临时数据库；各段新会话，实际接口调用，未替换真实模型。各轮保存状态、工具、答案、
停止原因与全链路用量，最终记录含实现文件 hash；不会修改演示库业务记录。

首测状态通过 **5/6**，失败是“先不用继续退货”导致工具清空草稿。加入明确中止
表达的后台约束与正反例后，同条件复测 **6/6**。随后收紧纯草稿回复范围，避免主动
做未要求的资格判断，减少新增上下文的原话重复展开，最终同条件再测 **6/6**。
前三次尝试均保存，不覆盖失败，不把已看到的用例继续当未见验证。

最终逐条人工复核 11 轮答案、工具及状态，6 段满足原草稿行为判据；仅指定的回顾轮
标注“重复索要已知字段 0 次”。不使用关键词或问号统计扩大这个结论，也不报告
完整售后成功率、长对话准确率或缓存造成的延迟/成本改善。

真实浏览器 5 项检查通过，首轮聊天正文变化 162 次；实际重启最终实现后，恢复草稿、
暂停保留与输入可用 3 项检查通过，页面无脚本错误。当前无需登录，用户切换清理
界面上下文且订单/历史按所选用户过滤。模拟身份不是认证；旧 E006 的匿名 401 已失效。
六张业务表指纹未变，索引与政策正文保持原样。
重启检查脚本第一次在服务启动完成前访问，返回连接拒绝，未发起模型请求；原日志保留。
确认 HTTP 200 后再运行通过，没有调整产品判据。

复现：`uv run python scripts/eval_task_memory.py --out results/task_memory/new_attempt`。
输出目录中的原始文件须不存在，避免覆盖尝试。状态检查可复算，回答行为人工判定另存。

证据目录 [results/task_memory](../results/task_memory/) 包括冻结条件、四次原始记录与
汇总、人工复核、浏览器记录和可查看截图。正式申请、资格规则、凭证、确认、幂等与
管理员审核仍按后续阶段实现；当前只读查询及草稿不作实际退款或售后承诺。

提交前补充工具错误分类回归：仅草稿校验异常属于参数错误，存储或索引异常继续作为工具故障。
发布代码再次运行相同 6 段仍为 6/6，完整离线 271 项通过。在线 hash 后的一处 Ruff
消息折行保存前后 hash 与 AST 等价证明，不改写原始评测记录。

## E008 — 受控退货申请与政策分支（阶段 3）

**Date:** 2026-10-13（Asia/Shanghai）。
**Status:** 申请创建交付；管理员审核及实际退款未实现。

基线 `d138e65` 只有持久草稿。先冻结 [18 项结构场景和 5 段对话](../data/eval/return_submission_cases.json)，
保存政策与六张业务表指纹；新增申请测试首先因模块不存在而无法收集，不把能力缺口
计成线上成功率 0%。v3 迁移追加凭证、预览、确认、申请与审计，不重建历史或业务表。
同一功能的实现、回归、文档及证据合并提交。

在线条件保持 `deepseek-flash`、temperature=0、thinking=false、BM25、top_k=3、无重排
或补位，固定日期 2026-10-13。每段复制当前演示库的原业务事实到临时数据库，仅在
临时副本清理旧会话与申请；使用实际 `on_send` 和页面操作处理器。6 轮模型回答、
操作结果、预览、确认前后行数、轨迹及全链路用量分别保留。合成图片仅测试格式，
不能证明实际故障。原文件独占创建，后续尝试用新目录。

| 尝试 | 状态检查 | 逐条阅读发现 |
| --- | --- | --- |
| `online` | 网络连接失败，5 段未完成 | 技术阻塞，不计作模型能力基线 |
| `online_network_allowed` | 5/5 | 3 段添加未规定的赠品/发票条件；2 段质量问题误用 7 天窗口 |
| `online_policy_grounded` | 5/5 | 返回完整同源政策后，仍有 1 段将无理由二次销售条件套到质量问题 |
| `online_scoped_policy` | 5/5 | 改为当前原因政策分支，保留逐条原记录 |
| `online_release` | 5/5 | 开发检查；运行期间仍有后续修改，不用作最终代码证明 |
| `online_final` | 5/5 | 前述政策错误未再发现；取消订单回复仍有英文开场白 |

状态检查判据没有放宽，5/5 前后不变；改进的是本组对话的政策解释。
上述回答错误数是复核诊断，不是事后新增的未见测试成功率。用例已转开发证据；
所有原记录和原 hash 保留，[manual_review.json](../results/return_submission/manual_review.json)
单独记录具体发现，不把正确调用工具视为正确回答，也不隐藏英文表达问题。

最终完整离线回归 **310 项通过**，Ruff 通过。验证包括 7/8 与 15/16 天、当前订单
状态及商品身份变化、整数数量与竞争占用、跨用户、过期及政策 hash、上传/撤回凭证、
提交后禁止编辑/中止、确认幂等、响应丢失可查询、事务回滚和 v2 历史保留。
模型工具没有正式提交入口；页面按钮触发后，由写事务消费确认、生成待审核单并写
创建审计。待审核/通过占用数量，拒绝释放；管理员修改状态目前只在离线测试中模拟。
新回归曾复现确认未比较商品身份，补充材料字段比较后通过。

初次浏览器 8 项检查通过，首轮正文变化 247 次，USER001 的 10 笔订单可在 190px 区域
滚动到末笔。实际按钮创建一张合成待审核单，刷新查询和用户切换不重复创建或泄漏；
该记录保留在演示库。最终服务重启后的恢复及文件撤回检查另存
[browser_final_check.json](../results/return_submission/browser_final_check.json)，4 项通过，
无页面脚本错误，未新增申请。浏览器驱动
最初用通用 `Clear` 定位误点历史选择清空按钮，失败日志保留；改为凭证组件内定位，
上传后文件输入节点消失的定位错误也保留，最终改用组件标签定位。复用已完成模型
轮次继续检查，不调整应用判据或为脚本错误重复跑模型。

复现：`uv run python scripts/eval_return_submission.py --out results/return_submission/new_attempt`。
冻结输入指纹、原始记录 hash、最终实现 hash、测试与数据库检查见
[verification.json](../results/return_submission/verification.json)。照片、用户条件仍未审核；
图片验证可读格式，视频当前只验证 MP4 文件头，最大 10MB。代码与当前运行服务一致，
六张原业务表、知识正文及索引没有修改。v3 不自动降级；旧 v2 程序不能直接打开 v3 库。
用量按真实请求链路记录，但单次、缓存与本组对话不能支持 p95、成本改善或线上成功率结论。

## E009 — 完整目录、实际能力与小知识库简单基线

**Date:** 2026-10-15（Asia/Shanghai）。
**Status:** 交付 A 完成，默认仍为 BM25；全文模式保留为有已知错误的实验方案。

修改前冻结 [8 个开发场景、11 轮](../data/eval/catalog_knowledge_cases.json)，基线
`be96c21`。六张业务表、8 篇知识正文、两份索引文件均保存指纹；临时数据库复制
业务事实，仅清理副本会话/申请。实际 `on_send` 链路、固定日期 2026-10-15、
deepseek-flash、temperature=0、thinking=false、BM25、无重排/补位；不重置演示库。
新增 10 项结构回归在隔离基线全部失败，最终回归通过；完整离线 326 项、Ruff 通过。

目录返回当前有效价而非种子价格，硬条件无匹配明确说明。知识来源分别为原片段
检索、一次提供所有版本化正文、模型选择整篇文档；后两者不调用 embedding、FAISS
或 reranker。应用能力说明约束实际入口、公开模型名称和不存在的转人工/价保提交。
未加入新的业务写工具或 API 账号。

| 保存的尝试 | 自动检查 | 事实/入口诊断 | 主要发现 |
| --- | ---: | ---: | --- |
| `baseline` | 5/8 | 4/8 | 目录无法查全、虚假页面指引、质量第15天误判过期 |
| `attempt1/retrieval` | 8/8 | 8/8 | 本批主要事实与入口未发现上述错误 |
| `attempt1/full_context` | 8/8 | 5/8 | 价保申请承诺、Q45充电盒、会员/活动页面 |
| `attempt1/documents` | 8/8 | 6/8 | 两段回答引用未实现的订单详情售后入口 |
| `retrieval` | 8/8 | 8/8 | 强化能力与商品归属后的开发复测 |
| `full_context` | 8/8 | 6/8 | 仍串用充电盒并推荐商品/结算页面 |
| `documents` | 8/8 | 8/8 | 本批前述入口问题未再发现 |

自动检查不改，仍仅判断完成、必要词组和无申请写入。复核逐条读取答案与证据，
由 Codex 标注，未经外部人工独立审核；其计数是额外诊断，不回写原 raw，也不是
完整语义或未见测试成绩。表达含混和知识术语问题另作 caveat，全部尝试保留。

最终 BM25/全文/整篇文档工具调用 11/3/8，记账 API 请求 26/14/18，token
79,111/118,438/70,894。全文比 BM25 多约 49.7% token，整篇文档少约 10.4%；
这批已见问题不足以支持切换默认。已定价成本分别约 0.024869/0.021373/0.022039
元，缓存影响实际计价；不能用总 token 直接推出人民币成本。基线为 69,917 token、
0.021965 元，本次 BM25 也未改善这两个数字。

修复后三方案新增完整处理器和首段正文时间，覆盖准备、生成与落库；耗时 p50 为
3609/2200/2806 ms。基线未测这个口径，不和旧 Agent 内部耗时混比。三方案在独立
进程/数据库中并行单遍，网络和资源竞争也影响结果，不报告 p95 或稳定效率结论。

在线完成后给评测表追加复核列、raw 关联校验和说明，保存
[前后 hash 与处理器 AST 不变证明](../results/catalog_knowledge/post_eval_view_change.json)。
提交前修正工具文件空白，另存 [AST 相等证明](../results/catalog_knowledge/post_eval_format_change.json)；
Agent、提示构建与评测脚本保持原字节。浏览器在重启后的服务检查
完整目录现价、流式正文、真实人工能力和表格；咨询不新增申请。最终结果与数据检查
见 [verification.json](../results/catalog_knowledge/verification.json)。

复现：`uv run python scripts/eval_catalog_knowledge.py --knowledge-mode documents --out results/new_catalog_attempt`。
原文件独占创建；模式可换 `retrieval` 或 `full_context`，不覆盖已有目录。
当前已有审核前申请，管理员审核、数据扩充、上下文预算与外部原版基准仍为后续交付。

## E010: 管理审核、终态约束与客户查询（交付 B）

日期：2026-10-15。基线 `e658407`；先冻结 16 项结构场景与 5 段模型对话，原业务、
会话、申请、正文与索引指纹见 [冻结清单](../results/return_review/frozen_manifest.json)。
首批 15 项结构回归在原代码失败，缺少审核方法；不把能力缺口计作线上正确率 0%。

管理页采用独立 Gradio 认证，角色来自服务端请求，不来自表单或模型；后台重复校验。
v4 非破坏式迁移追加唯一审核表和单向终态约束。管理员填写原因后，同事务更新
状态、记录审核与审计；并发审核仅一次成功，审计故障全部回滚。客户只读本人结果，
通过继续占用数量、拒绝释放；不执行退款或取消订单，不提供模型审核工具。

真实模型用当前业务副本中已有合成申请，可信评测参与方先在副本设置待审/通过/
拒绝，再通过实际 `on_send` 查询；其余场景为自称管理员和跨用户查询。没有改
订单日期、政策或商品，也没有操作演示库原单。固定日期 2026-10-15，deepseek-flash、
temperature=0、thinking=false、retrieval/BM25、无重排/补位；两次运行条件相同。

| 尝试 | 状态检查 | 主要事实/入口诊断 | 发现 |
| --- | ---: | ---: | --- |
| `online_first` | 5/5 | 3/5 | 通过回复漏真实原因并承诺退件查询；越权拒绝指向客户登录 |
| `online_final` | 5/5 | 5/5 | 补能力边界后前述错误未再发现；泛指联系店铺仍记为表达限制 |

自动检查只验证完成与模型未改变申请/审核，阅读诊断另存，不回写原始答案或放宽
条件。诊断为 Codex 逐条阅读，非外部人工独立审核；本批已见、单遍，不是总体
成功率。工具调用两次均 3 次、记账 API 均 8 次；token 28,651 → 29,040，不宣称
成本、延迟或长期稳定性提升。完整调用与用量随原记录保留。

完整离线回归 345 项、Ruff 通过。旧测试允许通过后改判拒绝，与本轮终态边界冲突，
改为分别测试合法终态；迁移版本断言同步到 4，另修正新测试的 Row/tuple 比较。
早期失败日志保留，不把测试断言修正当作模型提升。框架/Pandas 的 4 项弃用警告
不影响结果，本轮没有无关升级依赖。

浏览器 8 项流程检查通过，在临时副本产生 2 条审核、2 条对应审核审计。最终只读
复核 3 项通过，复用这些记录；管理页检查 1280/375/320px，无脚本错误。初次浏览器
启动被 uv 联网重试拖延，尚未调用模型或审核；改用已有缓存离线运行，原日志保留。
在线后只调整展示和开发观察开关，保存 [界面变更证明](../results/return_review/post_eval_ui_change.json)，
聊天、售后处理器及审核服务不变，未改写模型记录的原 hash。

演示库升级前备份，原业务、会话、待审核单、知识与索引保持；管理员凭据由用户
自行配置，不设默认密码，客户不恢复登录。管理员审核是独立业务操作，不是人工
接管聊天或实际退款。状态、原始 hash、测试与浏览器证据见
[verification.json](../results/return_review/verification.json)。

复现：`uv run python scripts/eval_return_review.py --out results/new_review_attempt`。
输出目录不可覆盖，当前依赖冻结事实和原合成待审单；原单已审核时，应使用评测前
数据库副本，不能改回终态或重置演示库。独立可移植场景快照在 C 交付。
下一项为独立数据快照、共享运行链路、上下文/整轮预算与故障
验证；外部原版基准在本地冻结任务就绪后再适配。

凭证显示补充：原“图片元素可见”检查未识别 16×16 纯白演示图只能显示成小白点。
保留原文件，补适配预览、原尺寸和大小、空白说明及原文件下载。新增渲染回归
先 4 失败、1 通过，再通过；完整回归 350 项。实际浏览器测量显示高度 16→320px，
白图与隔离彩色图下载 hash 均与原文件一致，无页面错误；申请、审核、审计和凭证
记录未改。这是交互与验证修正，不报告模型正确率变化；原 E010 记录不改写。
