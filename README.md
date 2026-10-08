# Adaptive Customer Service Agent

持续演进的电商客服 Agent，支持商品政策咨询、订单物流查询、退换货和价保判断。
通过执行轨迹定位问题，修复业务事实与评测缺陷，再用对照实验衡量回答质量和调用效率。

```bash
uv run python app.py      # 打开 http://127.0.0.1:7860
```

---

## 当前能力

```
User → Gradio UI → Naive Unified Agent (deepseek-flash)
                        ├─ search_knowledge → Dense RAG (FAISS)
                        └─ 业务工具 → SQLite
                   → 回答 + 执行轨迹 + 延迟/token/成本
```

- 手写 Function Calling Agent Loop（**没有用 LangGraph**）
- Dense RAG：Markdown → 小节切分 → Qwen3-Embedding-8B → FAISS
- 5 个只读工具（4 个业务查询 + 1 个知识检索）
- Web UI：客服对话与**可观测执行轨迹**、业务数据浏览、评测对照与逐条回放
- 会话级多轮历史、越权防护、错误兜底
- 35 条黄金用例、分层评分与重复运行，保存逐条答案、调用、错误和运行条件

当前业务工具均为只读查询，能够判断条件、解释流程，尚不能实际退款或提交售后申请。
界面中的用户选择用于模拟会话身份，尚未接入真实登录。

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
样例数据库按生成当日计算日期；跨日重演“前天收到”等示例时需重新生成。
对照评测使用固定参考日期，见问题记录中的运行条件。

---

## 页面

页面顶部可切换 3 个模拟用户；切换后会清空对话，并同步该用户的订单列表与详情。

| 页面 | 可交互内容 |
| --- | --- |
| 客服对话 | 发送问题、多轮追问；逐步查看工具调用、返回事实与知识命中，查看 token 和部分成本 |
| 业务数据 | 浏览 25 个订单、26 条明细、19 条物流；选择订单查看金额与时间线，选择商品查看价格历史 |
| 评测进展 | 查看三组共同 v4 对照；筛选 35 条用例，切换尝试次数，对比原始答案和失败原因 |

建议按下面的顺序体验：

1. 选择 `USER001`，在业务数据页查看 `O10024`：实付 728 元、两件商品、订单与物流签收日期一致。
2. 回到客服对话，点击“价保咨询”并发送：查询 `O10027`，观察订单事实与政策检索如何共同支持 30 元差价判断。
3. 打开评测进展，回放默认用例 `pp-no-difference`：首轮优化漏查政策、错误使用签收日起算；修正后按下单日判断。
4. 点击“体验此用例”，页面自动切换为 `USER002` 并填入问题；发送后运行当前 Agent。

评测回放读取保存的历史答案，不会调用模型；发送消息才会触发实时模型请求。
页面校验用例、原始记录的 hash 和评分版本，避免把不匹配的快照展示为当前结果。
业务数据页显示当前数据库；历史对照使用冻结的参考日期，两者应区分。

客服正文通过模型流式接口实时显示，收到文本片段即更新同一个聊天气泡。
若回答中途断流，保留已显示文字并明确提示未完成；缺少用量数据时标明成本统计不完整。
执行轨迹在运行过程中逐步显示，自动跟随新步骤；向上滚动可查看已完成步骤。
工具参数接收完整后才执行。
耗时只在客服回复上方显示实际已用时间与完成用时，不展示预测完成时间。
轨迹只展示可观测的工具调用、检索结果与统计，不包含模型的隐藏思维过程。
“决策摘要”由实际调用机械生成，说明做了什么。检索 embedding 尚未配置价格，
涉及检索的对话会标明“已定价成本（部分）”。

---

## 主要问题与改进

| 已验证的问题 | 解决方式 | 验证结果 |
| --- | --- | --- |
| 订单与物流签收日期矛盾 | 统一业务时间轴，补跨表回归测试 | 一致性从 0/11 到 11/11 |
| 购买价无法对应下单时价格 | 按订单补生效记录 | 一致性从 9/26 到 26/26 |
| 工具正确但错误答案也通过评分 | 补结论判据、数字边界与正反例 | 12 个错误反例从全部漏检到全部拒绝 |
| 检索消耗没进入请求统计 | 共享 usage 账本，记录完整耗时 | 固定探针从漏记到完整记录 3 次请求、247 token |

解决过程、修复提交、指标定义与证据见 [主要问题与解决过程](docs/PROBLEM_SOLVING.md)。
历史提交审核见 [PROJECT_REVIEW.md](docs/PROJECT_REVIEW.md)。
这些数据分别说明事实一致性和测量能力的改善，不能混作模型正确率提升。

## 工具调用效率对照

本轮对照使用 35 条用例各跑 3 次；固定业务参考日期、模型配置、知识索引和定价假设，
改变工具选择提示与描述。初版出现质量回退后补回必要的政策检查，三组共 315 次在线尝试；
全部原始答案统一按 v4 规则重新评分。

| 指标 | 优化前 | 修正后 |
| --- | ---: | ---: |
| 工具调用总数 | 160 | 122（减少 23.8%） |
| 超出用例所需次数的调用 | 43 | 5（减少 88.4%） |
| 严格成功率 | 63.8% | 95.2% |
| 规则成功率 | 93.3% | 100% |
| 平均 token / 次 | 3202 | 3338（增加 4.2%） |
| 端到端耗时 p50 | 2199 ms | 2222 ms |

原始耗时存在 embedding 服务故障波动。两组均无执行错误的 99 个同一尝试中，
平均耗时为 3278 → 2773 ms，p50 基本持平。结果主要支持调用效率改善；
token 与中位延迟尚未改善。100% 仅是本批合成用例的规则通过率。
证据见 [共同口径对照](results/tool_efficiency/comparison.json) 与
[完整问题记录](docs/PROBLEM_SOLVING.md)。

旧版 [baseline/summary.json](results/baseline/summary.json) 保留为历史记录。
其 100% 成功率存在答案漏检，成本未包含 embedding，延迟只累加对话 API 耗时；
因此不能与新口径直接比较。检索指标是多次检索累计的必需文档命中率，不是单次 Recall@3。

开发评测默认输出到独立目录；如需指定目录，使用：

```bash
uv run python scripts/run_eval.py --passes 3 --out results/my_run
```

规则评分用于发现明确的事实和调用错误，尚不能完整判断回答语义。35 条合成用例
也未覆盖真实登录、写操作和复杂多轮业务，结果应在这个范围内解释。
后续优先扩充实际失败案例，再判断是否需要改进检索或引入路由。

---

## 目录结构

```
app.py                        Gradio UI（单命令启动）
src/acs/
  config.py                   配置 + 定价 + 评测参考日期
  llm.py                      OpenAI 兼容客户端 + 记账 + embed（复用）
  service_agent.py            手写 Function Calling Agent Loop
  cs_tools.py                 工具注册表 + 执行 + 轨迹事件
  rag.py                      Dense RAG（小节切分 + FAISS）
  database.py                 只读数据访问（含权限校验）
  db_seed.py                  SQLite 种子数据
  eval_runner.py              分层评分 + 运行证据
  eval_view.py                历史评测校验与界面回放
  obs/trace.py                结构化 trace（复用，本版未启用 JSONL 落盘）
data/
  docs/*.md                   8 篇知识文档（带 front-matter）
  db/business.db              25 订单 / 4 商品
  .index/                     kb.faiss + kb_meta.json
scripts/
  init_db.py  build_index.py  run_demo_cases.py
  run_eval.py  validate_cases.py
  check_connectivity.py  check_secrets.py  smoke_llm.py
tests/                        离线回归测试（无需 API）
docs/                         PROBLEM_SOLVING / PROJECT_REVIEW / EXPERIMENT_LOG
```

---

## 设计约束（有意为之，不要"顺手"改掉）

1. **业务事实一律来自工具**。订单状态、物流位置、价格、日期都不允许模型
   凭记忆或推算产生。`database.py` 直接返回 `delivered_date`、
   `delivered_days_ago`、`within_return_window`、`price_delta` 等派生值。
2. **身份由服务端注入**。模型无法传 `user_id`；`cs_tools.py` 会显式剥离
   并记 `identity_override_rejected`。访问权限由数据层强制校验。
3. **工具不为可预期情况抛异常**。订单不存在返回 `found: false`，
   让模型能据此追问，而不是让 loop 崩掉。
4. **thinking 默认关闭**。DeepSeek 默认开启且会忽略 `temperature`，
   实测 2.5× 延迟、23× 输出 token。详见 `docs/EXPERIMENT_LOG.md`。
5. **不展示隐藏思维链**。只展示可观测执行。
