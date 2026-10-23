# 技术选型与源码研究

本轮采用 LangChain create_agent 与 LangGraph SQLite Checkpointer；保留 Python、SQLite、
现有 RAG 和 Gradio。FastAPI 仅设计接口，不新增服务。依据为下面的实际源码检查与本轮测试，
没有以框架宣传代替本地可运行证据。

## 1. 环境与依赖

项目声明 Python >=3.11，.python-version 与 CI 为 3.13；原工作区 venv 为 3.13.13，
本轮 uv 选择已有系统 3.13.12，没有安装或升级解释器。3.11 仅被依赖解析支持，未做运行验收。
uv 为 0.11.8。新增 optional extra `v3`，默认运行入口与依赖安装命令保留。

| 包 | 本轮锁定版本 | 用途 |
| --- | --- | --- |
| langchain | 1.4.7 | create_agent、调用限额 middleware |
| langchain-core | 1.7.0 | BaseMessage、StructuredTool、Callback |
| langgraph | 1.2.16 | 原生图执行、流、恢复 |
| langchain-openai | 1.7.2 | 沿用 DeepSeek OpenAI 兼容 Chat Completions |
| langgraph-checkpoint-sqlite | 3.1.2 | 本机持久化 |
| openai / pydantic | 3.26.0 / 2.13.5 | 基线已有版本，未升级 |

锁文件比较显示原有包版本变化为零，新增 27 个传递或直接包，详见
[dependency_audit](../../results/v3_langgraph_poc_20261023/dependency_audit.json)。
CI 安装 v3 extra，确保新增测试确实运行；未安装该 extra 时旧工程可以运行，PoC 测试显式跳过。

## 2. 框架原生能力的取舍

create_agent 的 factory 实际创建 StateGraph 和 ToolNode，使用原生消息 reducer。
SqliteSaver 提供 checkpoints 与 writes 表及连接锁。Store 是跨 thread 的应用键值空间，
不适合替代订单事实库。Interrupt 提供暂停/恢复控制流，不提供业务授权。
这些判断同时核查了锁定安装包源码和[官方 Agents](https://docs.langchain.com/oss/python/langchain/agents)、
[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)、
[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、
[Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)。

| 选择 | 理由与已知代价 |
| --- | --- |
| 优先 create_agent | 原生 model/tools 图已满足只读循环；自行写完整 StateGraph 会重复现成控制流 |
| 只加模型协议 middleware | 拒绝截断/畸形调用及未开放工具，映射真实 Registry 错误；不新增意图正则和独立规划模型 |
| SqliteSaver 独立数据库 | 实验可丢弃且不修改业务 schema；跨库事务一致性仍待写链路设计 |
| stream updates/custom/messages | 在当前锁定版本实际运行；官方新版 v3 event streaming 尚未验证，不追着升级接口 |
| 同步持久化、单 worker | 先验证重启和事件终态；吞吐和完整请求级硬超时不作生产承诺 |
| 不引入 Store | 本轮只需要线程内历史；没有跨会话偏好需求 |
| 不把旧 LLMClient 包成第二层 Loop | 用 ChatOpenAI 提供原生消息/stream/callback，复用原有 DeepSeek 配置与关闭 thinking 方式 |

最小替代方案是继续旧 Loop，仅增加独立 session 恢复记录，依赖更少，但仍要维护中间执行恢复。
本轮实验选择框架，依据是恢复路径可运行；没有旧 Loop 与 Graph 的同条件性能/质量对照，
因此尚不能说 Graph 更快、更省或任务成功率更高。

## 3. 六项参考的实际实现与可取机制

源码版本、路径、链接和本地审查快照 hash 记录在
[research_manifest](../../results/v3_langgraph_poc_20261023/research_manifest.json)。
只审查与本任务有关的模块，没有声称穷尽这些仓库或验证它们的线上 SLA。

| 来源及固定版本 | 已读源码与实际机制 | V3 采用与边界 |
| --- | --- | --- |
| LangChain/LangGraph，见版本表 | agents/factory.py；prebuilt/tool_node.py；checkpoint/sqlite；store/base；types.interrupt；graph/state | 直接用其循环、消息、恢复和流。业务事务与确认继续项目实现 |
| [τ upstream 2.0.0](https://github.com/sierra-research/tau2-bench/tree/cc1df47baa1544a34bb8c8e5c4ca72ad223b96a2) | retail/environment.py 与 tools.py，data_model/tasks.py，evaluator/*，metrics/agent_metrics.py，HalfDuplexAgent | 复用官方环境、任务与 Reward；本轮仅比较源码，不运行付费评测 |
| [ECom-Bench](https://github.com/XiaoduoAILab/ECom-Bench/tree/bc5018daaf45eea12330941bce68cebd293cfa85) | 53 个 Task；envs/story/tasks.py 内直接存 Persona/多个意图；user/user.py 调模型；env.py 隔离数据与 action/search/output 评分 | 借用 Persona 与多诉求的生成、核验思路；不把其业务工具和本店规则混用 |
| [anker-haike](https://github.com/xiaokekehaha/anker-haike/tree/0e30077da488c31f50b69f33f3d55059b62b7386) | conversation/business.py 的主体、标识与来源；reduce.py 校验提案；reader/history/snapshot 接续；actions/gate.py、executor.py 授权和幂等回执；effects.py 持久展示效果 | 借用事实按主体绑定、引用保留来源、确认闸门、结果未知先核对的机制；不复制其产品规则和完整事件平台 |
| [anker-OntoCare](https://github.com/jiangenjing/anker-OntoCare/tree/3e0e0a4871f6a3e785f77f76d7371da607256ef4) | app.resolve_product、lookup_order；order_cards.py 源行快照；conversation_workflow.py 的步骤游标；qa_workflows.py 来源 hash；evaluation/run_suite.py 固定来源核验 | 借用候选消歧、工作流游标和来源冻结；其 S1 Pro 名称规则与固定 FAQ 不是通用任务系统 |

ECom 的论文方法是 Persona 驱动、多模态、多诉求用户模拟；公开代码确有 Persona 和模拟器调用，
但 `user/memory.py` 当前工具只返回占位文本，不能描述成完整长期记忆。部分工具是合成环境
字段修改，不能据此声称已对接真实售后。本轮不搬用其外部图片或用户信息到本店测试库。

haike 有实际执行器：写门逐项通过后 claim 幂等键，未知回执的后续尝试先按 client_ref 查服务单。
这支持设计借鉴，不能推导本项目已获端到端 exactly-once。OntoCare 的 order_cards 明确
ownership_verified=False，app 的 SESSIONS 为进程内字典；来源可追溯与归属认证、持久恢复应分别评价。

## 4. 费用与运行开销

真实冒烟使用官方 DeepSeek endpoint、deepseek-flash、non-thinking、最多 1024 输出 Token，
SDK max_retries=0。原生 callback 在每次请求前持久化预留，累计不退款、不重置，最多 40 次、
5 元。按[当日官方价格](https://api-docs.deepseek.com/quick_start/pricing/)的峰值 cache-miss
输入/输出 0.3/1.2 USD 每百万 Token，使用项目原有 7.2 汇率估算。
保守输入预留为序列化消息和 schema 的 UTF-8 字节数两倍再加 4096，实际 usage 不得超出预留。
该界限是本模型与当前请求格式的保守工程记账，不是供应商账单证明；错误或缺 usage 保留预留。

本轮不做 embedding、用户模拟器或付费语义裁判请求。旧 RAG 的召回、切分和索引版本不变；
PoC 仅暴露商品、订单、物流、价格六个业务工具，不把整篇文档查询宣称为 RAG 验收。
