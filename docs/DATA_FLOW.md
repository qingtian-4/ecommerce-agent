# 数据流走读（Data Flow）

本文保留 2026-10-07 初版代码走读，行号和同步输出示例属于历史版本。
当前客服正文与执行事件流式显示，知识索引为 48 个小节，评测支持逐轮判断与检索拒答；
当前能力与运行证据见 [README](../README.md) 和 [主要问题记录](PROBLEM_SOLVING.md)。

这份文档回答一个问题：**一次用户提问，数据在代码里怎么流动、在哪里被改变形状。**
每个环节都标了文件与行号，方便直接跳转。

配合阅读：`docs/EVAL_PLAN.md`（评测集方案）、`README.md`（当前缺陷与下一步排序）。

---

## 0. 一张图

```
浏览器 (Gradio Chatbot)
   │  ①用户输入
   ▼
app.py :: on_send()                                     L341
   │  ②append 到 UI history → ensure_agent() L89 → agent.chat(msg)
   ▼
service_agent.py :: CustomerServiceAgent.chat()          L133   ← 循环入口
   │  ③llm.chat(self.messages, tools=5 个 schema)         L144
   ▼
llm.py :: LLMClient.chat()                               L133
   │  ④拼 kwargs（thinking=disabled、tools、temperature）  L165-190
   │  ⑤OpenAI SDK → DeepSeek HTTP
   │  ⑥解析回包 → LLMResponse(text, tool_calls, usage)     L199-215
   ▼
   ├── tool_calls 为空 → 最终答案，跳出循环                L166-176
   │
   └── 有 tool_calls → 逐个执行                            L180-200
          │
          ▼
       cs_tools.py :: ToolRegistry.run()                  L199
          │  ⑦json.loads(arguments)                       L227
          │  ⑧剥离 user_id / current_user_id               L263-275
          │  ⑨spec.func(current_user_id=会话身份, **args)   L278
          │
          ├──► database.py :: BusinessDB.get_order()      L88   SQL + 权限校验
          └──► rag.py :: KnowledgeBase.search()           L255  向量检索
          │
          │  ⑩生成 TraceEvent（tool_call / tool_result / retrieval / error）
          ▼
       service_agent.py  ⑪messages.append({role:"tool", ...})  L194
          └ 回到 ③，最多 max_steps=6 轮
   │
   ▼
AgentResult(answer, trace[], totals{})                   L71
   │  ⑫render_trace()  L188 / render_stats()  L290 / render_header() L139
   │  ⑬pack() 返回 7 元组                                 L320
   ▼
浏览器渲染（左对话 / 右轨迹 / 底指标）
```

---

## 1. 初始化（进程启动时一次）

**`app.py:43`** `SETTINGS = get_settings()`；`config.py:155` 用 `lru_cache` 保证只解析一次 `.env`。

```
config.py:77 Settings
  ├─ .env 提供：llm_api_key / llm_model / embedding_model / embedding_dim …
  ├─ 代码默认：chunk_size=700、top_k=3、max_steps=6、db_path、docs_dir
  └─ config.py:142 _resolve_paths()  相对路径 → 项目绝对路径
```

**为什么必须绝对路径**：相对路径会让"从哪个目录启动"决定读哪个数据库。`acs info`、`pytest`、`app.py` 的 cwd 各不相同，这个坑很隐蔽。

**`app.py:496 build_app()`**：索引不存在则自举（L498-501），然后搭组件树并把 handler 接到 `outputs`。

**离线资产**（`scripts/` 生成，运行时只读）：

| 产物 | 生成者 | 内容 |
|---|---|---|
| `data/db/business.db` | `db_seed.py:build()` | 25 订单 / 4 商品 / 19 物流 / 11 价格记录 |
| `data/.index/kb.faiss` | `rag.py:185 build()` | 51 个向量（4096 维，已归一化） |
| `data/.index/kb_meta.json` | 同上 | chunk 正文 + 所属文档 + 建索引所用模型名 |

---

## 2. 会话状态：什么跨轮存活

只有三个东西活着：

```
gr.State
  └─ {"user_key": "USER001 · 张伟 · …", "agent": <CustomerServiceAgent>}
                                                   │
                                                   ├─ agent.messages        对话历史
                                                   └─ agent.current_user_id 会话身份
```

| 对象 | 创建时机 | 生命周期 | 是否有状态 |
|---|---|---|---|
| `CustomerServiceAgent` | 首次发消息 / 切用户（`app.py:89`） | 整个浏览器会话 | ✅ `messages`、`current_user_id` |
| `LLMClient` | 随 agent（`service_agent.py:117`） | 同上 | ⚠️ `usages`，但**每轮开头清空** |
| `ToolRegistry` | 随 agent（`:118`） | 同上 | ❌ 无状态 |
| `BusinessDB` | 随 registry（`cs_tools.py:72`） | 同上 | ❌ 每次查询新开连接 |
| `KnowledgeBase` | 随 registry，懒加载（`:83`） | 同上 | ✅ 内存缓存 FAISS 索引 |
| `AgentResult` | 每轮（`:138`） | **单轮** | 用完即弃 |

**`service_agent.py:135`** `reset_usage()`：底部指标是**本轮**成本，不是累计。

---

## 3. 逐步追踪（真实数据）

以「订单 O10024 是我前天收到的耳机，现在还能退吗？」为例。

### ①→② 前端入口

```python
# app.py:341
history.append({"role": "user", "content": message})   # L354  仅给 UI 渲染
agent = ensure_agent(state_dict, user_key)             # L356
result = agent.chat(message)                           # L357
```

⚠️ **两份 history**：`history` 给 Gradio 画气泡；`agent.messages` 发给模型。长得像但不是同一个对象。

### ③ 进入循环

```python
# service_agent.py:133
self.llm.reset_usage()                                            # L135
self.messages.append({"role": "user", "content": user_message})   # L136
for step in range(1, self.max_steps + 1):                         # L141
    response = self.llm.chat(self.messages, tools=schemas, ...)   # L144
```

**`schemas` 来自 `cs_tools.py:191`**，把 5 个 `ToolSpec` 转成 function-calling 格式（`:54`）：

```python
{"type": "function",
 "function": {"name": "get_order",
              "description": "查询订单信息：订单状态、下单/发货/签收时间、商品明细…",
              "parameters": {"type": "object",
                             "properties": {"order_id": {"type": "string"}},
                             "required": ["order_id"]}}}
```

**这就是"模型自主决策"的全部机制**：给模型工具清单 + 参数 schema，让它自己选。
**代码里没有任何 if-else 决定该用哪个工具**——这正是将来做 Router 时的 baseline。

### ④→⑥ HTTP 往返

```python
# llm.py:158-190
kwargs = {"tools": schemas}
kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
kwargs["temperature"] = 0.0
resp = self._client.chat.completions.create(model=..., messages=..., **kwargs)
latency_ms = (time.perf_counter() - started) * 1000        # L191
```

回包解析成三块（`:199-215`）：

```python
LLMResponse(
  text       = message.content or "",
  tool_calls = [ToolCall(id, name, arguments)],
  usage      = Usage(prompt_tokens, completion_tokens, cached_prompt_tokens,
                     latency_ms, cost_cny, thinking, reasoning_chars),
)
```

第一轮真实返回：

```
text       = None（空）
tool_calls = [ToolCall(id="call_…", name="get_order",
                       arguments='{"order_id": "O10024"}')]
usage      = prompt_tokens≈1180, completion_tokens≈45
```

### ⑦→⑨ 工具执行与安全关卡

```python
# cs_tools.py:199 run()
events = [TraceEvent("tool_call", name, {"arguments": arguments_json})]  # L208
spec = self._tools.get(name)                  # L212  工具不存在？
arguments = json.loads(arguments_json)        # L227  不是合法 JSON？

# ★ 模型永远无法指定身份
for reserved in ("user_id", "current_user_id"):            # L263
    if reserved in arguments:
        events.append(TraceEvent("error", name,
            {"error_type": "identity_override_rejected", …}))
        arguments = {k: v for k, v in arguments.items() if k != reserved}

result = spec.func(current_user_id=current_user_id, **arguments)   # L278
```

`current_user_id` 来自**会话身份**，不来自模型 JSON。

### ⑨-分支A SQL 路径

```python
# database.py:88
rows = SELECT o.*, u.name FROM orders o JOIN users u WHERE o.order_id = ?   # L89
if not rows: return {"found": False, "reason": "订单不存在…"}                # L94

if order["user_id"] != current_user_id:                                     # L105
    return {"found": False, "error_type": "forbidden", …}                    # L106
```

**把模型不该自己算的东西全部算好**（`:131-153`）：

| 字段 | 本次值 | 为什么由 SQL 算 |
|---|---|---|
| `today` | `2026-10-08` | 机器时钟可能与服务端不同 |
| `delivered_date` | `2026-10-07` | 模型曾把它说成 10-08 |
| `delivered_days_ago` | `1` | 模型算日期不可靠 |
| `within_return_window` | `True` | 把 7 天规则固定进代码 |
| `within_price_protection` | `True` | 15 天，**起算点不同**（下单日） |
| `items[].price_delta` | `60.0` / `20.0` | 现价 − 购买价 |

**核心规则**：凡模型不该重算的派生值，都在数据层算完。

### ⑨-分支B 向量检索

```python
# rag.py:255
if self._index is None: self.load()                  # L257 首次读 kb.faiss
vector = self.llm.embed([query], stage="kb_search")   # L263 调 embedding API
matrix = self._normalise(np.asarray(vector))         # L264 L2 归一化
scores, indices = self._index.search(matrix, k)      # L265 内积 = 余弦
```

**L264 归一化是让 `score` 可解释的关键**：`IndexFlatIP` 是内积，只有归一化后内积才等于余弦（0~1），界面上显示的分数才有意义。

返回带 provenance：

```python
{"found": True, "query": "无线耳机退货期限",
 "documents": [{"chunk_id": "return_policy#2", "document_id": "return_policy",
                "title": "退换货政策", "category": "policy",
                "content": "## 一、七天无理由退货…", "score": 0.7154}]}
```

### ⑩ TraceEvent：右侧面板的唯一数据源

```python
# cs_tools.py:32
type:   "tool_call" | "tool_result" | "retrieval" | "error" | "final"
label:  工具名
detail: {...}     # dict，不是自然语言
```

结构化 `detail` 才让 `render_trace()`（`app.py:188`）能按类型分别渲染：`retrieval` 展开 `documents` 并显示 `score`，`error` 画红框。

### ⑪ 喂回模型的两个细节

```python
# service_agent.py:179-200
self.messages.append(self._assistant_message(response))   # ★ 必须先放 assistant
for call in response.tool_calls:
    payload, events, error = self.registry.run(...)
    self.messages.append({"role": "tool", "tool_call_id": call.id,
                          "content": json.dumps(payload, ensure_ascii=False, default=str)})
```

1. **必须回传请求工具的 assistant 消息**（`:226`），否则 `role:"tool"` 没有 `tool_call_id` 可挂，API 直接报错。
2. **`default=str`**：SQLite 可能返回不可序列化的类型，降级成字符串而不是让 loop 崩。

### ③→⑥ 第二轮及以后

`agent.messages` 的真实形状（5 条）：

```
[0] system      你是一名专业的电商客服助手…
[1] user        订单 O10024 是我前天收到的耳机，现在还能退吗？
[2] assistant   tool_calls=[get_order]        ← 空 text，只有工具请求
[3] tool        {"found": true, "delivered_date": "2026-10-07", …}
[4] assistant   tool_calls=[search_knowledge]
[5] tool        {"documents": [return_policy#2 0.7154, …]}
```

最终轮 `wants_tools=False`：

```python
result.answer = response.text.strip()                        # L167
result.trace.append(TraceEvent("final", "answer", …))        # L171
self.messages.append({"role": "assistant", "content": result.answer})  # L175 ← 多轮对话在此实现
break
```

### 兜底：`for…else`

```python
for step in range(1, self.max_steps + 1):     # L141
    ...
else:                                          # 跑完未 break
    result.stop_reason = "max_steps"
    result.answer = "…帮您转接人工客服？"
```

模型无限调工具时，6 步后强制收口并记 `max_steps_exceeded`。

---

## 4. AgentResult：交给 UI 的载荷

```python
# service_agent.py:71
answer              : str                 # 给用户看的回答
trace               : list[TraceEvent]    # 右侧面板全部内容
steps               : int                 # 用了几个循环步
tool_calls          : int                 # 工具调用次数
retrieval_queries   : list[str]           # 检索过什么
retrieved_documents : list[dict]          # 累计命中文档
errors              : list[str]            # 结构化错误码
stop_reason         : str                 # answered | max_steps | llm_error
totals              : dict                # token / 延迟 / 成本
```

**`decision_summary()`（`:82`）** 是唯一"像解释"的字段：从 `trace` 反推（`[e.label for e in trace if e.type == "tool_call"]`），**不是模型说的**。所以界面那句话只描述"做了什么"，不暴露思维链。

---

## 5. 渲染层

```python
# app.py:341
result = agent.chat(message)                                     # L357
history.append({"role": "assistant", "content": result.answer})  # L374
return pack(history, render_trace(result), state_dict,
            render_stats(result), card, render_header(user_key))  # L375-382
```

**`pack()`（`app.py:320`）是唯一定义返回值个数的地方**：

```python
return history, "", trace, state_dict, stats, card, header
#      ↑chat  ↑清空输入  ↑轨迹  ↑状态  ↑指标  ↑侧栏  ↑顶栏   （7 个）
```

必须与 `build_app()` 的 `outputs=[…]` 顺序和个数一致。**不一致时 Gradio 不报错，只在服务端 500**——这是上一轮那个 bug 的成因。

| 渲染函数 | 行 | 消费 | 输出 |
|---|---|---|---|
| `render_trace` | L188 | `result.trace[]`、`retrieved_documents`、`errors` | HTML 卡片（按 `type` 配色） |
| `render_stats` | L290 | `result.totals`、`tool_calls` | HTML 指标格 |
| `render_header` | L139 | `_kb_stats()`（缓存 51/5）+ `SETTINGS` | HTML 头部 |

`render_trace` 内所有动态内容都过 `html.escape()`——工具返回与模型文本都算不可信输入。有测试专盯这条。

---

## 6. 三条边界（设计约束的代码体现）

| 约束 | 代码位置 | 效果 |
|---|---|---|
| **业务事实只来自工具** | `database.py:88-154` 返回派生值；`service_agent.py:34` 系统提示第 1/2 条 | 模型没有"记忆"通道 |
| **身份不由模型提供** | `cs_tools.py:263-278` 剥离；`database.py:105` WHERE 校验 | 越权在两处被挡 |
| **不展示隐藏思维链** | `service_agent.py:82` 从 trace 反推；`llm.py` 只记 `reasoning_chars` 不取内容 | UI 上无 `reasoning_content` |

---

## 7. 错误路径

全部**不向浏览器抛异常**：

| 错误 | 位置 | 模型收到 | trace `error_type` |
|---|---|---|---|
| 工具名不存在 | `cs_tools.py:212` | `{"error": "unknown tool", "available": [...]}` | `tool_not_found` |
| 参数非合法 JSON | `:227` | `{"error": "arguments are not valid JSON"}` | `invalid_arguments` |
| 参数名错/缺失 | `:279` TypeError | `{"error": …, "expected_parameters": [...]}` | `invalid_arguments` |
| 工具内部异常 | `:298` | `{"error": "tool X failed: TypeError"}` | `tool_exception` |
| LLM 调用失败 | `service_agent.py:147` | — | `llm_api_error` |
| 超过 max_steps | `service_agent.py` for-else | — | `max_steps_exceeded` |

前三种**仍把结果喂回模型**（`:194`），模型可自行纠正或追问。

---

## 8. 形状总结

```
不可信输入（用户文本、模型输出）
        │
        ├─► 模型只能"请求"调工具（cs_tools.py:263 剥离身份）
        │        └─► 事实出自 SQL / FAISS（database.py / rag.py）
        │                 └─► 结构化 TraceEvent（给 UI）
        │                     + JSON 字符串（给模型）
        │
        └─► 所有输出经 html.escape() 后才进 HTML
```

**模型在链路里是"提议者"：不是事实来源，也不持有权限。** 三条边界都在代码里，不在 prompt 里。
