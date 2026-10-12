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
                        ├─ search_knowledge → RAG（BM25 / Dense / RRF）
                        └─ 业务工具 → SQLite
                   → 回答 + 执行轨迹 + 延迟/token/成本
```

- 手写 Function Calling Agent Loop（**没有用 LangGraph**）
- RAG：共用 Markdown 小节，支持 BM25、Qwen embedding＋FAISS、Dense＋BM25＋RRF；
  支持可选 API 重排与核验后补位；候选正文经证据核验后再交给 Agent
- 5 个只读工具（4 个业务查询 + 1 个知识检索）
- Web UI：客服对话与**可观测执行轨迹**、业务数据浏览、评测对照与逐条回放
- 演示账户登录、SQLite 持久会话、历史选择与重启恢复；会话与订单按用户隔离
- 70 条公开开发用例（含 10 条多轮）与 15 条独立文件的新增验证，保存每轮答案、调用、证据、错误和运行条件；原 35 条历史对照保留

当前业务工具均为只读查询，能够判断条件、解释流程，尚不能实际退款或提交售后申请。
演示账户通过 Gradio 登录映射到合成业务用户，身份来自服务端请求，界面不能切换用户身份。
尚未接入真实电商账户或管理员审核。

---

## 快速开始

```bash
# 1. 依赖
uv sync --extra dev

# 2. 配置
cp .env.example .env        # 填入 ACS_LLM_API_KEY 与 ACS_EMBEDDING_API_KEY
uv run python scripts/configure_demo_accounts.py  # 首次生成独立演示密码，保存在 .env

# 3. 建库 + 建索引（各一次）
uv run python scripts/init_db.py        # 25 订单 / 4 商品 / 19 物流
uv run python scripts/build_index.py    # 48 chunks / 8 文档

# 4. 启动
uv run python app.py                    # http://127.0.0.1:7860

# 5. 可选：命令行验证四个 Demo Case（UI 出问题时用来定位）
uv run python scripts/run_demo_cases.py
uv run python scripts/run_demo_cases.py --case 3

# 6. 测试
uv run pytest && uv run ruff check .
```

`app.py` 会在索引缺失时自动构建，所以第 3 步对启动不是必需的，但对调试有用。
已有索引与文档、切分配置或版本不符时会明确报错，需重新构建；不会继续返回旧政策。
已有数据库直接启动应用即可追加会话迁移；`init_db.py` 是种子重建入口，不用于升级。
它发现已有会话时会拒绝重建，避免丢失历史与业务数据。
相关性标定绑定知识库、切分版本与 embedding 模型，不能直接套用到新语料。
样例数据库按生成当日计算日期；跨日重演“前天收到”等示例时需重新生成。
对照评测使用固定参考日期，见问题记录中的运行条件。

---

## 页面

首次运行账户配置脚本后，在本机 `.env` 的 `ACS_AUTH_ACCOUNTS` 查看登录名与密码。
`demo1 / demo2 / demo3` 分别映射 `USER001 / USER002 / USER003`，密码各不相同。
刷新或重新登录后先选择自己的历史会话；“新建会话”保留旧历史，但不会继承它的上下文。
退出登录后可使用另一账户，订单与历史始终按服务端认证身份查询。

| 页面 | 可交互内容 |
| --- | --- |
| 客服对话 | 选择历史或新建会话、发送问题、多轮追问；逐步查看调用、证据、token 和部分成本 |
| 业务数据 | 浏览 25 个订单、26 条明细、19 条物流；选择订单查看金额与时间线，选择商品查看价格历史 |
| 评测进展 | 查看检索、重排与补位的消融结果及候选回放；查看端到端开发回归与历史对照 |

建议按下面的顺序体验：

1. 登录 `demo1`，在业务数据页查看 `O10024`：实付 728 元、两件商品、订单与物流签收日期一致。
2. 回到客服对话，点击“价保咨询”并发送：查询 `O10027`，观察订单事实与政策检索如何共同支持 30 元差价判断。
3. 打开评测进展，回放默认用例 `pp-no-difference`：首轮优化漏查政策、错误使用签收日起算；修正后按下单日判断。
4. 退出并登录 `demo2` 后体验该用户用例。体验按钮只填入问题，不能改变当前账户身份。

评测回放读取保存的历史答案，不会调用模型；发送消息才会触发实时模型请求。
页面校验用例、原始记录的 hash 和评分版本，避免把不匹配的快照展示为当前结果。
业务数据页显示当前数据库；历史对照使用冻结的参考日期，两者应区分。

客服正文通过模型流式接口实时显示，收到文本片段即更新同一个聊天气泡。
若回答中途断流，保留已显示文字并明确提示未完成；缺少用量数据时标明成本统计不完整。
执行轨迹在运行过程中逐步显示，自动跟随新步骤；向上滚动可查看已完成步骤。
工具参数接收完整后才执行。
耗时只在客服回复上方显示实际已用时间与完成用时，不展示预测完成时间。
轨迹只展示可观测的工具调用、检索结果与统计，不包含模型的隐藏思维过程。
“决策摘要”由实际调用机械生成，说明做了什么。检索 embedding 和 reranker 尚未配置价格，
发生 embedding 或重排调用的对话会标明“已定价成本（部分）”。

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

本轮改变的是工具选择提示与工具描述文案，控制流、工具实现与数据结构未变。
结果反映提示词引导的效果，不能直接解释为 Agent 架构能力提升。
对照使用 35 条可见开发用例各跑 3 次；固定业务参考日期、模型配置、知识索引和定价假设。
初版出现质量回退后补回必要的政策检查，三组共 315 次在线尝试；
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
证据见 [历史 v4 对照](results/tool_efficiency/comparison.json)、
[v5 命名修正后的离线重评分](results/tool_efficiency/comparison_v5.json) 与
[完整问题记录](docs/PROBLEM_SOLVING.md)。

旧版 [baseline/summary.json](results/baseline/summary.json) 保留为历史记录。
其 100% 成功率存在答案漏检，成本未包含 embedding，延迟只累加对话 API 耗时；
因此不能与新口径直接比较。检索指标是多次检索累计的必需文档命中率，不是单次 Recall@3。

v5 将工具种类一致、调用总数一致、各工具调用次数一致分别记录为
`tool_set_exact`、`call_count_exact`、`tool_call_counts_exact`；严格成功要求普通成功且
各工具调用次数符合用例预算。累计必需文档命中为 `cumulative_document_hit`，
`cumulative_first_relevant_rr` 是拼接历次检索排名的首个相关文档倒数，均不是单次 Recall@3。
每次检索独立保存 query 与排名；重要政策检查 `support_section_hit`，库外检查
`knowledge_rejection_ok`。多轮会话使用同一个 Agent，但每轮分别评分，任一轮失败则会话失败。
原始历史文件不改写，命名修正没有提高旧对照成绩。

开发评测默认输出到独立目录；指定公开开发集与新目录：

```bash
uv run python scripts/run_eval.py --cases data/eval/development.yaml --passes 1 --out results/my_run
```

省略 `--cases` 时仍使用冻结的原 35 条用例，便于复现历史实验。
规则评分用于发现明确的事实和调用错误，尚不能完整判断回答语义；
合成用例未覆盖真实登录、写操作，结果应在这个范围内解释。

## 新增验证与检索修复（2026-10-09）

改动 Agent 行为前冻结 15 条新问题，首次规则通过 **11/15（73.3%）**，严格通过 10/15，
含 1 次 embedding 异常。原提示词复跑及四项消融为 11–13/15，样本小且有服务波动，
不能直接判定是否过拟合。用例作者已读过项目，它不是严格盲测；结果公开后不再是未见集。
正式提示词和工具描述本轮保持不变。

随后扩充公开开发集并修复过期索引、纯标题段落与库外检索。首版阈值造成三条型号问题
误拒，保留失败结果，再补齐商品编号元数据、核验分数重叠区间的候选证据。

| 同一 70 条开发用例、v5、各 1 遍 | 修复前 | 首版过滤 | 修正后 |
| --- | ---: | ---: | ---: |
| 规则通过 | 53/70 | 58/70 | 58/70 |
| 严格通过 | 50/70 | 55/70 | 57/70 |
| 累计必需文档命中 | 32/37 | 30/37 | 33/37 |
| 库外拒答 | 0/4 | 4/4 | 4/4 |
| 多轮会话通过 | 7/10 | 8/10 | 7/10 |
| 端到端耗时 p50 | 2216 ms | 2270 ms | 2487 ms |

最终比基线多通过 5 条，有 6 条改善、1 条回退；单次运行不证明稳定性或显著性。
修正后额外触发 5 次证据核验、2111 token、已定价小计 0.002870 元，全部纳入请求统计。
平均 token 基本持平，中位延迟增加，不能宣称性能全面提升。
P002 快充仍漏检索；部分规格问题多查商品，正确答案也可能触发狭窄规则误判。
尚未新增实际退款、售后写操作。

证据：[首次新增验证](results/holdout/first/summary.json)、
[同条件开发回归](results/review_regression/comparison.json)、
[修正后逐条记录](results/review_regression/after_v2/per_case.jsonl)。
根因、过程与下一步见 [主要问题记录](docs/PROBLEM_SOLVING.md)。

---

## 章节级检索对照（2026-10-11）

先冻结 52 条问题与所需证据章节，再用相同语料、单路候选 10 条、最终最多 3 个片段
比较 Dense、BM25 与 RRF。32 条开发问题用于选择方案，20 条验证在选择后首次运行；
作者已读过语料，不是严格盲测，结果公开后不再称为未见集。

| 指标 | Dense | BM25 | Dense＋BM25＋RRF |
| --- | ---: | ---: | ---: |
| 开发集排序 Recall@3（24 条有效问题） | 81.2% | 100% | 97.9% |
| 开发集核验后 Recall@3 | 77.1% | 100% | 97.9% |
| 开发集库外问题接受 | 0/8 | 0/8 | 0/8 |
| 验证集核验后 Recall@3（14 条有效问题） | 92.9% | 100% | 100% |
| 验证集库外问题接受 | 1/6 | 0/6 | 0/6 |
| 开发集配对检索耗时 p50 / ms | 930 | 527 | 1312 |

Recall 按问题所需事实的证据组计算：同组可接受任一充分章节，多个事实都需要覆盖。
P002 快充正确章节原本排第 9，BM25 排第 1、RRF 排第 2；这说明原先的检索失败发生在
前三名排序。BM25 在当前小知识库的覆盖和耗时更好，因此在看到验证结果前选为默认。

耗时包含证据核验，并对 Dense/RRF 复用同一次 embedding 的实测用时；不含 Agent 的
回答生成，不是独立端到端性能对照。BM25/RRF 对每次候选做正文核验，开发集各新增
32 次核验，分别使用 16765 / 15044 token；Dense 仅灰区核验 4 次、1840 token。
因此覆盖提升同时增加了核验消耗。检索成功不能代替回答正确性，也不证明真实用户泛化。

页面“评测进展”可以比较六行结果，按问题回放原始候选、核验结果和目标章节。
实时轨迹区分 Dense、BM25 与 RRF 分数；RRF 额外显示各路排名。
通过 `ACS_RETRIEVAL_MODE=dense|bm25|hybrid` 选择方案，重启应用生效。
BM25/RRF 分数不会套用旧 cosine 阈值；保留原 Dense 规则作为对照。
语料与 embedding 输入未改动，本轮沿用已验证的 v5 索引。

```bash
uv run python scripts/run_retrieval_eval.py --split development --out results/retrieval_new_dev
uv run python scripts/run_retrieval_eval.py --split validation --out results/retrieval_new_validation
```

脚本校验冻结输入并拒绝覆盖结果；复跑已看过的验证问题属于回归。
完整记录见 [对照证据](results/hybrid_retrieval/comparison.json)、
[问题解决过程](docs/PROBLEM_SOLVING.md)、[实验条件](docs/EXPERIMENT_LOG.md)。
独立 query rewrite 和售后写操作仍属于后续工作。

## API 重排与核验后补位（2026-10-11）

一般退货问题的 RRF 前三名包含保修和政策简介，正文核验后只剩七天条款，
排第四的期限对照表没有补位。新增两项可独立验证的改进：

- 核验后补位：先核验最多 10 个候选，再取最多 3 个有效片段。
- API 重排：用问题与章节正文的配对分数重新排序，同样保留正文证据核验。
  重排分数与原始 Dense、BM25、RRF 分数分别记录，不能套用旧 cosine 阈值。

原 52 条已公开问题全部归入开发证据，另冻结 20 条新验证；标签不按命中结果筛选。
开发集 38 条有效问题，RRF 核验后 Recall@3 从 **98.68% → 100%**，
所需事实全部覆盖从 **37/38 → 38/38**；补位和重排分别达到这一结果。
五种方案均拒绝 14 条库外问题，无执行错误。重排将 RRF 首个有效章节的平均排名倒数
从 **0.877 → 1.000**，但没有超过 BM25 的最终证据覆盖率。

补位的核验 token 从 24143 增至 66683（约 2.76 倍）；重排新增 52 次请求、61632 token，
自身 API 耗时 p50 为 302 ms。开发配对检索 p50：BM25 576 ms、RRF 1606 ms、
RRF＋重排 1977 ms，含服务波动，不包含回答生成。embedding 与重排未定价，
不能将已定价小计当作完整成本。根据开发结果，在新增验证运行前冻结选择，保留 BM25 默认。

新增验证（14 条有效、6 条库外）的核验后 Recall@3：BM25 **85.71%**、RRF **75%**、
RRF＋补位 **100%**、RRF＋重排 **85.71%**、重排＋补位 **100%**；库外接受均为 0/6。
所有 360 个正式检索尝试无执行错误。多事实问题仍可能被多个相似章节占满前三名，
重排让相关章节更靠前，但不保证覆盖第二项事实。本轮保留预先冻结的默认选择；
这些新增问题之后归入开发，后续部署变化需再用新冻结问题验证。
标签审计发现一条质保问题遗漏了“质保卡”替代片段，原标签和分数保留；
这里是冻结参考章节的覆盖率，不能等同于穷尽所有有效证据或判断生成回答错误。

配置已列在 `.env.example`。当前接口使用
[硅基流动 rerank API](https://docs.siliconflow.cn/docs/api/rerank-post)：

```dotenv
ACS_RERANKER_BASE_URL=https://api.siliconflow.cn/v1
ACS_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
ACS_RERANKER_API_KEY=
ACS_RERANKER_TIMEOUT_S=15
# 实时体验 RRF＋重排；正常默认仍为 bm25 / false
ACS_RETRIEVAL_MODE=hybrid
ACS_RERANKER_ENABLED=true
# 独立启用核验后补位，会增加核验 token
ACS_EVIDENCE_BACKFILL=false
```

空 reranker 密钥仅在其地址与 embedding 地址完全相同时复用 embedding 密钥；
不同服务需单独填写。修改 `.env` 后重启服务。API 的不完整排名、重复或越界索引、
非法分数与请求错误会明确失败，不静默回退；请求耗时与可用 token 进入共享账本，
缺少用量时界面标为部分统计。重排是 `search_knowledge` 内部阶段。

前端「评测进展」提供五方案对照与逐条回放，区分原始候选、重排排名、核验池和最终证据。
这一轮测量的是检索覆盖，不是生成回答正确率；公开过的验证问题之后也只作为开发证据。
复现命令与条件见 [EXPERIMENT_LOG.md](docs/EXPERIMENT_LOG.md#e005--api-重排与核验后补位)。

## 会话记忆与登录（2026-10-12）

按照 [NEXT_ITERATION.md](docs/NEXT_ITERATION.md) 先交付阶段 1：客户演示账户登录、
持久对话、历史选择与用户隔离。原消息、完整工具调用与结果、回复、停止原因和运行统计
保存在 SQLite。浏览器传来的聊天历史只用于显示，模型上下文从已授权会话的存储记录恢复。

请求开始即保存用户输入，回复完成后原子保存完整轮次；同一会话同时只允许一个请求。
断流或取消保留中断标记，硬重启时未完成轮次不作为成功回答、不自动执行重试。
历史业务事实可能过期，恢复后提示重新查询；后续退货提交还须后台强制重校验。

正常启动追加版本迁移，不重建现有业务表。数据库已有会话时，旧建库脚本会拒绝重建。
当前只支持单进程应用；多实例部署需先补运行实例与存活检查。
本期尚未实现结构化退货任务、确认提交、售后单或管理员审核。

登录与恢复验收包括刷新、退出再登录、重启、工具配对、跨用户拒绝、并发与种子保护。
这些是持久化和权限约束的验证，不能当作真实模型任务成功率或当前完整 Agent 的评测成绩。
代表性真实浏览器记录见 [browser_check.json](results/session_memory/browser_check.json)，
根因与边界见 [PROBLEM_SOLVING.md](docs/PROBLEM_SOLVING.md#9-刷新后丢失对话同一用户无法继续原会话)。

## 目录结构

```
app.py                        Gradio UI（单命令启动）
src/acs/
  config.py                   配置 + 定价 + 评测参考日期
  auth.py                     演示登录校验 + 服务端身份映射
  sessions.py                 SQLite 迁移、会话与完整轮次存储
  llm.py                      对话 / embedding / rerank API + 共享记账
  service_agent.py            手写 Function Calling Agent Loop
  cs_tools.py                 工具注册表 + 执行 + 轨迹事件
  rag.py                      共用小节检索 + 证据核验
  retrieval.py                中文分词 + BM25 + RRF
  retrieval_eval.py           冻结章节级检索对照
  rerank_eval.py              固定候选池的重排 / 补位消融
  database.py                 只读数据访问（含权限校验）
  db_seed.py                  SQLite 种子数据
  eval_runner.py              分层评分 + 运行证据
  eval_view.py                历史评测校验与界面回放
  obs/trace.py                结构化 trace（复用，本版未启用 JSONL 落盘）
data/
  docs/*.md                   8 篇知识文档（带 front-matter）
  db/business.db              25 订单 / 4 商品
  .index/                     kb.faiss + kb_meta.json
  eval/                       冻结历史集 / 新增验证 / 公开开发集 / 检索标定样本
scripts/
  init_db.py  build_index.py  run_demo_cases.py
  configure_demo_accounts.py  首次配置独立演示账户密码（保存在 .env）
  run_eval.py  validate_cases.py
  run_prompt_ablation.py  calibrate_relevance.py  run_retrieval_eval.py  run_rerank_eval.py
  summarize_rerank.py          从原始记录离线复算检索覆盖与共享账本
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
