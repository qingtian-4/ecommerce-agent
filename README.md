# Adaptive Customer Service Agent

**MVP: 一个可以在浏览器里真实对话的电商客服 Agent。**
它能自主决定是否检索知识库、是否调用业务工具，并把完整执行轨迹展示出来。

```bash
uv run python app.py      # 打开 http://127.0.0.1:7860
```

---

## 这一版是什么 / 不是什么

**是**（全部已实测跑通）：

```
User → Gradio UI → Naive Unified Agent (deepseek-flash)
                        ├─ search_knowledge → Dense RAG (FAISS)
                        └─ 业务工具 → SQLite
                   → 回答 + 执行轨迹 + 延迟/token/成本
```

- 手写 Function Calling Agent Loop（**没有用 LangGraph**）
- Dense RAG：Markdown → 小节切分 → Qwen3-Embedding-8B → FAISS
- 5 个只读工具（4 个业务查询 + 1 个知识检索）
- 左右布局 Web UI：左侧对话，右侧**可观测的执行轨迹**
- 会话级多轮历史、越权防护、错误兜底

**不是**（本阶段明确不做，见文末「下一步」判断依据）：

Adaptive Router、BM25、Hybrid、RRF、Reranker、Query Rewrite、Parent-child
Chunk、Multi-Agent、Memory、MCP、Langfuse/OTel、Redis/PostgreSQL/Milvus/
Elasticsearch、Mutating Tool、Golden Eval、外部 benchmark、React/Vue/FastAPI。

---

## 快速开始

```bash
# 1. 依赖
uv sync --extra dev

# 2. 配置
cp .env.example .env        # 填入 ACS_LLM_API_KEY 与 ACS_EMBEDDING_API_KEY

# 3. 建库 + 建索引（各一次）
uv run python scripts/init_db.py        # 25 订单 / 4 商品 / 19 物流
uv run python scripts/build_index.py    # 51 chunks / 8 文档

# 4. 启动
uv run python app.py                    # http://127.0.0.1:7860

# 5. 可选：命令行验证四个 Demo Case（UI 出问题时用来定位）
uv run python scripts/run_demo_cases.py
uv run python scripts/run_demo_cases.py --case 3

# 6. 测试
uv run pytest && uv run ruff check .
```

`app.py` 会在索引缺失时自动构建，所以第 3 步对启动不是必需的，但对调试有用。

---

## 页面

```
┌──────────────────────────────────────────────────────────────────────┐
│ Adaptive Customer Service Agent                                      │
├───────────────────────────────┬──────────────────────────────────────┤
│ 当前登录用户 [USER001  ▾]      │ Agent Execution Trace                │
│                               │  stop: answered  steps: 2  tools: 2  │
│ Customer Chat                 │  决策摘要：检索了知识库，调用了业务工具  │
│                               │            get_order，共 2 次工具调用  │
│ user: 订单 O10024 是我前天      │ ──────────────────────────────────── │
│       收到的耳机，现在还能退吗？  │ Step 1 · 工具调用 get_order           │
│                               │   {"order_id": "O10024"}             │
│ assistant: 可以的，您的订单      │ Step 2 · 工具返回 get_order           │
│   还在退货期内。根据退换货       │   {"status": "delivered", ...}       │
│   政策，无线耳机自签收之日       │ Step 3 · 知识检索 "无线耳机退货期限"    │
│   起 7 天内可申请无理由退货…     │   [0.7446] 退换货政策 return_policy#1 │
│                               │   [0.7316] 退换货政策 return_policy#2 │
│ [输入消息…]         [发送]      │   [0.6963] 保修与售后维修政策 #2       │
│ [清空对话]                     │ Step 4 · 最终回答                     │
│ Demo Cases: 6 个可点击示例       │ 运行指标                              │
│                               │  延迟 2.53s  输入 3298  输出 214      │
│                               │  总 token 3512  ¥0.002110  工具调用 2 │
└───────────────────────────────┴──────────────────────────────────────┘
```

轨迹只展示**可观测**的执行过程：工具名、参数、返回结果、检索命中文档与
余弦分数、延迟、token、成本。**不展示模型的隐藏思维链**。那行「决策摘要」
是从"实际调用了哪些工具"机械推导出来的，说明的是**做了什么**（轨迹里本来
就能看到），不是模型自称的**为什么**。见 `app.py::decision_summary`。

---

## 四个 Demo Case 的实测结果

| # | 场景 | 用户 | 实际调用 | 命中 | 延迟 | tokens | 成本 |
|---|---|---|---|---|---|---|---|
| 1 | 纯 RAG | USER001 | `search_knowledge` ×1 | return_policy#2 (0.715) | 1.66 s | 2886 | ¥0.001436 |
| 2 | 纯 Tool | USER001 | `get_logistics` + `get_order` | — | 2.19 s | 3069 | ¥0.001428 |
| 3 | **RAG + Tool** | USER001 | `get_order` + `search_knowledge` | return_policy#1 (0.745) | 2.53 s | 3512 | ¥0.002110 |
| 4 | 越权 | USER001 | 无（直接拒绝） | — | 1.08 s | 1177 | ¥0.000462 |

单轮总成本约 **¥0.0055**。四轮合计 ¥0.0054。

**Case 1**（政策问答）→ 命中 `return_policy#2`「一、七天无理由退货」，
答出「7 天 + 不影响二次销售条件 + 运费承担」，未调用任何订单类工具。

**Case 2**（订单+物流）→ 同时调用两个工具，答案给出承运商、运单号、当前
分拨中心与完整轨迹。

**Case 3**（RAG + Tool，最重要）→ 先 `get_order` 拿到签收日，再
`search_knowledge` 拿政策，两者结合得出「还在 7 天内，可以退」，
并补充二次销售条件与运费说明。

**Case 4**（越权）→ 拒绝。注意：**这一轮模型没有调用工具就直接拒绝了**，
所以它走的是「模型自觉遵守规则」而不是「SQL 权限校验」。SQL 层的拦截由
`tests/test_database.py::test_cross_user_order_read_is_denied` 和
`tests/test_agent_loop.py::test_cross_user_order_is_denied_through_the_agent`
覆盖（后者会强制模型去查 O10030 并断言返回 `forbidden`）。
**这是当前最重要的一个 Bad Case，见下节。**

---

## 基线（35 条黄金用例 × 3 遍 = 105 次尝试）

`uv run python scripts/run_eval.py --passes 3` → `results/baseline_v3/`

| 指标 | 值 |
|---|---|
| success_rate | **1.000** |
| pass^3 | 1.000 |
| **strict_success_rate** | **0.676** |
| strict_pass^3 | 0.657 |
| flip_rate（3 次结果不一致） | 0.000 |
| 延迟 p50 / p95 | 1908 ms / 3352 ms |
| 平均 token / 次 | 3156 |
| 总成本（105 次尝试） | **¥0.31** |

### 为什么 success_rate = 1.0 不是好消息

合取判据只要求「该调的工具调了、禁止的工具没调、参数对、检索到、说到要点」，
**不把"多调工具"算作失败**。所以它已经饱和，无法再区分"高效"与"正确但浪费"。
一个饱和的指标测不出改进。

因此加了 **`strict_success`**：在同样的判据上额外要求**零冗余调用**。
差距就是 RQ-A 要优化的东西。

### 分层指标（每个都附适用条数与覆盖率）

| 维度 | 通过/适用 | 比率 | 覆盖率 |
|---|---|---|---|
| tools_complete（该调的调了） | 105/105 | 1.000 | 100% |
| **tools_exact（没有多调）** | **87/105** | **0.829** | 100% |
| no_forbidden_tools | 57/57 | 1.000 | 54% |
| args_ok（参数正确） | 54/54 | 1.000 | 51% |
| call_order_ok（调用次序） | 24/27 | 0.889 | 26% |
| retrieval_recall（Recall@3） | 60/60 | 1.000 | 57% |
| answer_containment | 54/54 | 1.000 | 51% |
| no_forbidden_text | 39/39 | 1.000 | 37% |
| no_errors | 105/105 | 1.000 | 100% |

`no_forbidden_tools` 覆盖率 54% 是 v1 有意为之：`forbidden_tools` 先只写在关键用例上。

### 浪费归因：157 次工具调用中 40 次是多余的（25.5%）

| 类型 | 次数 | 说明 |
|---|---|---|
| **同一工具重复调用** | **21** | 全部是 `search_knowledge` |
| **调用了不需要的工具** | **19** | `get_logistics` 9、`get_price_history` 5、`get_order` 3、`get_product` 1 |

按用例类型：

| category | 平均冗余/次 | 典型表现 |
|---|---|---|
| **price_protection** | **1.00** | 每次尝试都多调 1 次 |
| rag+tool | 0.53 | 检索两次 |
| order | 0.43 | 问状态却调了物流 |
| security / product | 0.33 | |
| policy | 0.25 | 检索两次 |
| logistics / fallback | 0.00 | 干净 |

### 三个已定位的浪费模式

1. **`search_knowledge` 被调两次**（21 次，占浪费的一半）。
   典型：`p-return-quality`「质量问题退货的期限和普通退货一样吗？」
   模型检索两次（一次"退货期限"、一次"质量问题"），但 `search_knowledge`
   本身就吃自然语言 query，一次就能覆盖。**这是工具设计问题，不是模型问题。**

2. **订单状态问题却调了 `get_logistics`**（9 次）。
   如 `o-pending`「O10026 发货了吗？」。系统提示第 8 条写了
   "用户问『到哪了』『还能退吗』时，通常需要同时调用订单与物流工具" ——
   这条**为了修 Case 2 而加的规则被过度泛化了**，连不涉及物流的问题也照做。

3. **价保问题调了 `get_price_history`**（5 次）。
   `get_order` 返回体里已经带了 `current_price` 和 `price_delta`，
   再调一次价格历史是重复劳动。**工具返回体已经给足了信息，模型没意识到。**

### 评测集自身的可信度

`scripts/validate_cases.py` 在跑任何 LLM 之前，把用例里每条事实断言（价格、
总价、天数、窗口标志）逐条对 SQL 校验，并断言两个"陷阱用例"仍然成立：

- `O10027`：**退货窗口已过（签收 8 天 > 7 天）但价保窗口仍在（下单 10 天 < 15 天）**
  —— 两个窗口起算点不同，最容易答错。
- `O10032`：价保被排除是因为**状态是 cancelled**，不是因为天数。

第一版基线跑出 84.76%，逐条读失败后发现**相当一部分是判据本身的缺陷**，已修正：

| 问题 | 性质 |
|---|---|
| `must_not_contain: ["50"]` 命中"配合充电盒总续航 50 小时" | 判据太宽，模型正确 |
| 价保问题却要求命中 `return_policy` | 判据写错文档 |
| 「这个充电宝」有歧义，模型要求澄清反被判失败 | 用例问法有歧义 |
| 越权用例要求必须调工具，而直接拒绝更安全 | 判据把偏好当要求 |
| `must_not_contain: ["在保修范围内"]` 命中「**不**在保修范围内」 | 子串检查不识别否定 |

最后一条已修：`must_not_contain` 现在是**否定感知**的（见
`eval_runner._contains_unnegated`）。这是启发式而非解析器，局限写在方案文档里。

---

## 当前 Bad Cases（来自基线实测，不再是手工观察）

1. **25.5% 的工具调用是浪费的**（40/157），其中一半是 `search_knowledge` 重复调用。
2. **价保类问题每次尝试都多调一次**（平均冗余 1.00）。
3. **`tools_exact` 只有 0.829**，但 `tools_complete` 是 1.000 ——
   该调的从没漏，多调的很多。
4. **调用次序不稳定**：`l-in-transit` 三次里有两次先调 `get_logistics`，
   一次先调 `get_order`（`call_order_ok` = 0.889）。答案都对，但路径不定。
5. **系统提示第 8 条的措辞被过度泛化**，导致订单状态问题也去调物流工具。
6. **`forbidden_tools` 只覆盖 54%**，安全维度仍有 16 条未评估。

---

## 下一步 Top 3（来自实际运行问题，不是来自早期设计文档）

按「解决上面哪个已观测问题」排序，而不是按「听起来高级」排序：

### 1. 先补一个最小评测集，把「变好」变成可测量（解决缺陷 1/2/3）

上面 6 个 Bad Case 里有 3 个是**无法判断严重程度**的，因为只有单次观测。
在做任何优化之前，先把 4 条 case 扩到 **30 条左右**，每条记录
`expected_tools` / `forbidden_tools` / `expected_docs`，然后记录
pass^k 与 P50/P95。

**为什么排第一**：如果不做这一步，第 2、3 项改完之后**无法证明改好了**——
只会变成又一次"感觉变好了"。这也正是早期调研里所有仓库的共同教训：
数据集是真的，指标代码才是漂移的地方。

### 2. 修检索的两个具体缺陷：标题 chunk 与元数据过滤（解决 Bad Case 5/6）

- `return_policy#1` 这类**只有标题没有正文的 chunk** 不应该是独立候选，
  要么并入下一节，要么在索引时过滤掉。
- 问「充电器保修」时应能按 `product_type` 过滤，避免召回充电宝的内容。

**为什么排第二**：这是**唯一有直接证据的检索缺陷**（实测 top-1 排错），
且修起来小、可验证。**不要现在上 BM25/Hybrid/RRF**——没有任何证据表明
稠密检索本身不够用，问题是切分与过滤，不是召回算法。

### 3. 把越权拒绝从「模型自觉」变成「结构必然」（解决 Bad Case 1）

现在 Case 4 是模型自己拒绝的。应改成：**无论模型是否去查，服务端都记录并
呈现一次权限判定**；理想形态是让 `get_order` 在返回 `forbidden` 时，由
框架而不是模型决定最终话术。

**为什么排第三**：它是唯一的安全性问题，但当前有测试覆盖、且模型行为恰好
偏保守，所以实际风险低于前两项。它更像是 V1 该做的事，不是今天必须做的。

> **明确不做**：Adaptive Router 与 Hybrid Retrieval。目前**没有任何观测
> 支持"路由是瓶颈"**——4 条 case 里 3 条只调了 1–2 个工具、2–3 步就结束，
> 连多跳都还没出现过。等第 1 项的评测集跑出来，如果发现有相当比例的问题
> 走了多余路径、或简单问题被强行塞进复杂链路，再引入路由。

---

## 目录结构

```
app.py                        Gradio UI（单命令启动）
src/acs/
  config.py                   配置 + 定价（复用自 Step 0）
  llm.py                      OpenAI 兼容客户端 + 记账 + embed（复用）
  service_agent.py            手写 Function Calling Agent Loop
  cs_tools.py                 工具注册表 + 执行 + 轨迹事件
  rag.py                      Dense RAG（小节切分 + FAISS）
  database.py                 只读数据访问（含权限校验）
  db_seed.py                  SQLite 种子数据
  obs/trace.py                结构化 trace（复用，本版未启用 JSONL 落盘）
data/
  docs/*.md                   8 篇知识文档（带 front-matter）
  db/business.db              25 订单 / 4 商品
  .index/                     kb.faiss + kb_meta.json
scripts/
  init_db.py  build_index.py  run_demo_cases.py
  check_connectivity.py  check_secrets.py  smoke_llm.py
tests/                        129 tests
docs/                         EXPERIMENT_LOG / REPO_ANALYSIS / TODOS
```

**已知的结构债**：`src/acs/` 下同时存在 `agent/`、`tools/`、`retrieval/`、
`kb/`、`eval/` 这些**空占位包**（早期 Phase 1 的目录规划）。其中 `agent/`
与 `tools/` 与 MVP 模块重名，导致 MVP 模块被迫命名为 `service_agent.py`
与 `cs_tools.py`。这些占位包目前无害（`retrieval/` 等未被 import），
但如果后续真要填充，应先决定是"扁平模块"还是"子包"，不要两者并存。

---

## 设计约束（有意为之，不要"顺手"改掉）

1. **业务事实一律来自工具**。订单状态、物流位置、价格、日期都不允许模型
   凭记忆或推算产生。`database.py` 直接返回 `delivered_date`、
   `delivered_days_ago`、`within_return_window`、`price_delta` 等派生值。
2. **身份由服务端注入**。模型无法传 `user_id`；`cs_tools.py` 会显式剥离
   并记 `identity_override_rejected`。权限判定在 SQL 的 WHERE 里，不在 prompt 里。
3. **工具不为可预期情况抛异常**。订单不存在返回 `found: false`，
   让模型能据此追问，而不是让 loop 崩掉。
4. **thinking 默认关闭**。DeepSeek 默认开启且会忽略 `temperature`，
   实测 2.5× 延迟、23× 输出 token。详见 `docs/EXPERIMENT_LOG.md`。
5. **不展示隐藏思维链**。只展示可观测执行。
