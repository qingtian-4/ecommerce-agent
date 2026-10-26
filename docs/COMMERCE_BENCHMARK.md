# 本地电商客服 Benchmark v1.1

本轮交付离线数据、真实业务执行和评分器校准，不评估付费模型的决策质量。
基线为 `feat/v3-shop-frontend` / `4f006c5f6a35343594e43d236d4df46f4ffec0dd`，
开发分支为 `feat/v3-local-agent-benchmark`。默认客服、Web Demo、历史评测和依赖锁不修改。
正式数据入口为 `data/eval/commerce_benchmark_v1_1/manifest.json`。

## 审计与复用

|已有资产|核查结论|本轮处理|
|---|---|---|
|`db_seed.py` / `scenarios.py` / `build_task_suite.py`|历史场景有60订单、旧冒烟输入及预置申请，不能直接当作新小数据|复用SCHEMA、SessionStore迁移和Fixture安全屏障；新生成3用户、8商品、18订单|
|`data/eval/multiturn_v1`|80题；`TASK_EVALUATION.md`已说明旧human-authored标签问题|保留数据和历史结论；不改题或复用为独立未见测试|
|M8 v1/v2 `multiturn_evaluation.py`|已有原生Graph、业务工具、应用确认/事务和故障注入执行器|调用原`run_task`，只在受限调用范围投影新Fixture、时钟、SQL快照、事实判据|
|M8.1 `live_evaluation.py`|开放回答有pass/fail/pending_review/execution_error|复用`score_episode`，未知改写进入待审；不运行付费入口|
|`local_return_tools.LocalKnowledge`、ToolRegistry|Demo实际使用本地BM25知识工具|真实执行`search_knowledge`；原生切块v5，700字、80重叠、上下文3、候选诊断6|
|`retrieval_eval.py`|已有证据组评价和数据校验|复用`load_cases`、`evidence_metrics`，不新增Embedding服务|
|`tau_langgraph_agent.py` / `tau_acs_agent.py`|官方环境是唯一工具执行者，Graph暂停交出请求，未知写回执不能盲重放|保留官方Adapter。新任务只调用本地业务工具，分数不称为官方Reward|

本地测试环境：Python3.13.13、LangChain1.4.7、LangGraph1.2.16、Pydantic2.13.5、
rank-bm25 0.2.2、FAISS1.15.1、OpenAI SDK3.26.0、tau2 1.0.1。
没有升级环境或引入新依赖。新Runner离线合同沿用M8的Episode限制：16次模型决定、
24次工具调用；这里的模型决定来自显式回放，不等于HTTP请求。未套用M8.1真实Pilot的8请求上限。

## 外部方法与实现来源

研究[ECom-Bench源码](https://github.com/XiaoduoAILab/ECom-Bench/tree/bc5018daaf45eea12330941bce68cebd293cfa85)：
`envs/story/tasks.py`、`envs/story/env.py`、`envs/base.py`、`user/user.py`及README。
借鉴Persona、多意图、参考状态构造和搜索行为检查。实际`UserBased`实现也调用LLM，
不能把README中的规则驱动描述当作本轮已实现的模拟用户。
其输出评价包含子串命中，空outputs也可能使all条件成立；本项目不照搬为语义成功判据。
不移植其自动空响应重试、缓存权限放宽、客户记录或商品数据。

研究[历史τ²任务Schema](https://github.com/sierra-research/tau2-bench/blob/dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd/src/tau2/data_model/tasks.py)
及`evaluator/evaluator_env.py`：参考操作构造目标状态，代理可采用另一条安全路径达到相同终态。
借鉴这一点区分SQL终态、执行是否合法及参考路径覆盖；不以参考调用顺序作为必需答案。
历史Adapter仍固定1.0.1 / `dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd`。
另核查当时main `0c69df456a96aa76a1162591574dad9c427a34d7` 的README，属于τ-bench2.0；
没有混用新版政策、任务或评分。本轮没有运行官方环境或产生官方分数。

ECom-Bench为Apache-2.0，τ²为MIT；本轮仅引用设计和复用本仓库代码，没有复制上游源码、任务、
Persona记录或业务数据。所有新用户、商品和订单为本地合成。

## 数据与任务

生成器：`src/acs/commerce_benchmark_data.py`。固定seed `20261026`，逻辑时钟
`2026-11-06T04:00:00+00:00`，政策期限不依赖执行机器的当前日期。
数据包括3用户、8商品、18订单、19订单项、16 SKU、13物流、16价格历史和13篇知识文档。
商品规格重新编写；4篇政策复用本项目现有文本，保留来源及哈希；没有生产数据。

每题单独建立全新业务SQLite/Graph，用户身份由原应用层绑定；正式Fixture保留原禁止审核、
退款及任意源表修改的Trigger。仅CB12/CB14经既有受信任应用桥创建模拟待审核申请。
确认、申请、审计和幂等回执必须一致，Checkpoint不作为申请成功依据。

|任务|目标|类型|
|---|---|---|
|CB01|预算、佩戴约束与代词规格追问|共同能力|
|CB02|多候选订单澄清后查物流|共同能力|
|CB03|商品咨询与部分草稿保存混合请求|共同能力|
|CB04|插问政策后更正商品并预览|共同能力|
|CB05|更正订单清除旧商品、数量和原因|共同能力|
|CB06|否定、引用、假设取消并咨询商品|共同能力|
|CB07|明确取消且不创建申请|共同能力|
|CB08|第7天边界与第8天只读咨询|共同能力|
|CB09|跨用户拒绝、本人未发货订单无物流|共同能力|
|CB10|过期预览不得作为有效预览|共同能力|
|CB11|政策hash改变使旧预览失效|共同能力|
|CB12|应用确认、重复点击只创建一份申请|V3应用增量|
|CB13|拒绝确认，不提交且保留草稿|V3应用增量|
|CB14|COMMIT后回执丢失、重开Graph并只读核验|V3应用增量|

每题有用户目标、Persona、2–3条真实聊天输入（另外可含应用动作）、初始业务数据、
逐步SQL Gold、工具权限/禁止副作用、回答事实和失败条件。
4题development、10题validation；RAG是12题development、24题validation。
这些任务和Gold由同一工程作者编写，运行后全部为已见开发证据，不是独立盲测或泛化成绩。
测试校验没有原样复制M8完整输入序列。

`agent_tasks.json`是规范；`replay_contracts.json`是单独的正向执行控制，不送入Runtime提示词。
本轮只在V3实际执行；没有给旧Loop新跑一组结果，也不声称V3胜过旧Loop。
Persona仅约束已编写的输入风格，不是在线LLM用户模拟器。

RAG为24条商品规格、8条政策/多事实、4条无直接答案问题。
Gold从冻结原文的字面证据映射到切块；同一事实允许多个证据块，多个必要事实分别计分。
检索结果不参与Gold生成。SKU有货状态与精确库存件数严格区分。

## 评分合同

`src/acs/commerce_benchmark.py`适配已有执行器，不增加Agent Loop或业务事务。
以下指标独立保留，不压成额外综合分数：

1. SQL正确：逐步草稿字段、revision/预览有效性、申请及确认/审计计数、源表不可变，最后再读权威SQLite。
2. 工具合法：检查允许/禁止工具、执行回执和预期拒绝。合法替代路径不因漏走参考工具而失败；参考覆盖仅诊断。
3. 回答：raw/display分别对照独立编写的事实和SQL；SQL成功不能替代语义核验。
   新措辞未匹配参考时为pending_review，不当作自动失败或成功，保留review_queue。
   Grounding自身规则不为自己打分。
4. 恢复：CB14实际提交后故障，再关闭/重新打开Graph，从完整业务回执确认结果；
   这是进程内生命周期重开和回执丢失注入，不宣称新做了SIGKILL/断电实验。
5. RAG：候选6与上下文3的证据Recall、all-evidence、MRR；无答案问题记录非空上下文暴露。
   这不是生成回答的忠实度，非空上下文不能被称为模型幻觉，相关安全政策仍可能有帮助。
6. 执行次数和耗时：回放模型决定数、HTTP数、本地执行延迟。没有SDK/云模型Token用量，Token为null而不是虚构为0。

13个对抗评分控制包括：实际漏写、错订单、错数量、未经授权取消尝试、SQL正确但虚假提交回答、
正确改写待审，以及完整/缺失/无关/不存在证据、无答案空/非空和工具错误。
控制使用真实业务工具和SQLite，RAG评分控制是显式构造的错误检索ID；不得将控制命中率当作Agent质量。

## v1到v1.1的明确修订

初始v1和完整运行原样保存在`data/eval/commerce_benchmark_v1`及
`results/commerce_benchmark_v1_20261026`。后续效度复核新增两条测试，原版都失败：
合法`get_price_history`被权限名单遗漏，政策对照表能完整支持答案却不在证据组。

v1.1补全实际可用的只读工具，并给RQ25/26增加政策表格证据替代。
只改README、agent_tasks工具许可及rag_cases证据组；用户输入、初态、文档和回放决定均不变。
可字节重建v1，两个manifest独立，不能将v1.1称为原合同的无变化复现。
这是测量缺陷修正，不是检索或Agent质量优化。没有为提高分数改用户问题、业务规则或模型Prompt。

## 复现

环境复用项目现有锁：`uv sync --extra dev --extra v3 --extra tau --frozen`。
以下命令从仓库根目录运行；每次out必须是不存在的新目录，拒绝覆盖或重置历史数据库。

```bash
uv run python scripts/eval_commerce_benchmark.py --mode verify
uv run python scripts/eval_commerce_benchmark.py --mode build --out /tmp/commerce-input-repro
uv run python scripts/eval_commerce_benchmark.py --mode fixture --out /tmp/commerce-fresh-fixture
uv run python scripts/eval_commerce_benchmark.py --out /tmp/commerce-offline-run
uv run pytest tests/test_commerce_benchmark.py
```

默认输入为v1.1，`--suite`可显式选择原v1；`--revision local-commerce-v1`可重建原版。
`--ids CB03 CB14`仅选择Agent子集，仍执行完整RAG；`--without-controls`可省略对抗控制。
Runner没有live模式。执行范围内禁止socket连接，发现被内部吞掉的联网异常也会令验收失败，
模型与Embedding密钥在此进程置空，不影响并行运行的Demo。

输出public JSON包含逐题Scorecard、SQL对账、raw/display已知合成回答、RAG证据和Badcase。
新目录`.private-evidence/CBxx`保留业务SQLite、Graph、工具回执、费用文件、完整模型输入/输出。
私有目录权限0700，CLI使用umask077；原始数据库、Prompt及大体积中间文件不入Git。
公开报告仅收录合成事实、字段白名单和源码指纹；请求次数和费用均为0。

## 本轮边界与后续门禁

本轮完成离线执行及评分器自检后停止。未运行DeepSeek、LLM Judge、Dense/RRF或全量官方Benchmark。
Dense/RRF需要真正离线语义Embedding和新语料的相关性标定，不能用假向量制造质量结论。
新语料在BM25上的证据遗漏及无答案暴露保留为Badcase，不在本轮更改默认检索链路。
证据组尚不能穷尽全部同义段落，未知回答需要独立审查。下一阶段若要比较模型质量，
需另行冻结输入/模型/权限/预算、获得付费授权，并保留原始与展示答案的独立语义审查。
