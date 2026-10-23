# V3 标准 Evaluation 方案

本轮只审计协议、提出 Adapter 与本地域扩展方案。没有运行完整收费 Benchmark，
没有修改任何官方任务、参考动作、政策或评价代码。小规模真实烟测单列于本轮 REPORT，
不能记为 τ 或 ECom 成绩。

## 1. 先分清三个版本轨道

| 轨道 | 固定来源 | 可比较范围 |
| --- | --- | --- |
| 历史完整 τ 两臂 114×3×2 | main 中 tau_base/frozen_manifest.json 固定 `dd8757d`，版本 1.0.1 | 保留历史 pass^1/pass^3；不得给旧分数贴上新版协议名称 |
| 后续三臂 pilot/formal/recovery | 独立分支 protocol_v6.json 固定 upstream `dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd`，pyproject version=1.0.1 | 以每次冻结的任务、trial、评价口径及未完成项为准；V2.1 修复不自动获得原 V2 分数 |
| 当日上游 2.0.0 | `cc1df47baa1544a34bb8c8e5c4ca72ad223b96a2` | 新建独立实验；即使任务相同也不能混入上述结果 |

实际比较 `dd8757d` 与当前 2.0.0：零售 tasks.json 都是 114 题，解析后的内容相同；
HalfDuplexAgent 源码相同；Task 新增可选 agent_opener。评价入口已改变，需要显式传入
communicate_scoring，并可指定 customer_language；当前 COMMUNICATE 支持 semantic 与 substring
模式，ALL 对不在 reward_basis 中的通信项不再无条件运行通信评价。
任务内容相同不能证明用户模拟、依赖、语言或费用口径相同。

依据：[旧任务 Schema](https://github.com/sierra-research/tau2-bench/blob/dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd/src/tau2/data_model/tasks.py)、
[新版评价入口](https://github.com/sierra-research/tau2-bench/blob/cc1df47baa1544a34bb8c8e5c4ca72ad223b96a2/src/tau2/evaluator/evaluator.py)、
[新版通信评价](https://github.com/sierra-research/tau2-bench/blob/cc1df47baa1544a34bb8c8e5c4ca72ad223b96a2/src/tau2/evaluator/evaluator_communicate.py)。
逐项源码比较记录在本轮 [protocol_diff](../../results/v3_langgraph_poc_20261023/protocol_diff.json)。

## 2. 评价协议保持官方语义

从官方 Task 复用 id、description、user_scenario、initial_state、evaluation_criteria。
initial_state 的初始化数据/动作独立于模型可见信息；目标动作产生的期望最终状态由官方环境
执行参考动作构造，DB 检查依据官方重放/状态比较，不自己改为“调用过正确工具”。
DB、COMMUNICATE、NL_ASSERTION、ACTION、ENV_ASSERTION 按该版本和每个 task 的 reward_basis
决定组合，不在 basis 的诊断分单列。NL 断言按官方实现可能调用模型，费用必须事前预留。
审核诊断或额外语义检查不能未经说明替换官方 Task Reward。

每题 n 个独立 trial 中成功 c 次，pass^k 的任务级估计为 C(c, k)/C(n, k)，再按任务平均；
这是重复 k 次均成功的指标。pass^1 为一次成功率，不是“多次里成功过一次”。不足 k 次、
费用中止或基础设施失败按固定上游实现和预注册报告，不把缺失样本补成成功，也不拼接新旧版本。
依据：[官方指标源码](https://github.com/sierra-research/tau2-bench/blob/cc1df47baa1544a34bb8c8e5c4ca72ad223b96a2/src/tau2/metrics/agent_metrics.py)。

## 3. LangGraph Adapter 的具体接线方案，尚未实现

现有 `scripts/tau_acs_agent.py` 位于外部评测/V2 分支。其 ACSAgent 依赖旧 agent.messages、
chat_stream、RunBudget、AgentResult.trace 与 stop_reason，因此不能把 loop_class 直接改为 PoC。
OfficialRegistry 的官方 schema、消息 ID 配对、返回协议校验和 uncertain_write 保护可借用；
预算账本、任务冻结、官方 Orchestrator 和评分入口继续复用。

拟新增 `scripts/tau_langgraph_agent.py`，实现官方 HalfDuplexAgent 的 get_init_state、
generate_next_message 与 stop，注册一个候选 Agent 工厂。

1. get_init_state 使用官方 tools 的 schema 和原 domain_policy，导入完整配对的官方消息；
   每个 task/trial/arm 使用独立 thread 与官方隔离环境。模型配置、thinking、输出上限、
   timeout、重试策略与旧 Loop 对照臂冻结一致。
2. 工具调用之前用原生暂停边界返回 AssistantMessage(tool_calls)，由官方 Orchestrator 执行。
   不允许 native ToolNode 在本地直接执行官方写工具，也不在 Adapter 内另跑第二次工具。
3. 原 ToolMessage 返回后验证 id/name/序列，按对应 ToolCall 注入 Graph 消息并推进暂停边界。
   并行批次先实现串行出口：每次只交付一个尚未返回的调用，不丢失已排队意图。
4. 官方工具的业务错误按同一协议映射；未知写/畸形成功回执均停止并核对，原始返回保留。
   Graph Checkpoint 不能代替官方环境的写回执。结束时关闭模型流/客户端，清理本段执行资源。
5. 输出自然回答时生成官方 AssistantMessage 和原始 usage/审计字段。evaluate_simulation
   接收未改的官方 Task、trajectory、最终环境和冻结的 evaluation_type。

本店 PoC 的六工具白名单、中文 prompt、PocBudget 只适用本轮只读冒烟；官方 Adapter 必须
动态接收官方工具和政策，不能把本店身份工具、商品、退货预览或知识文档塞进官方 Domain。
本店业务服务和外部 Benchmark 应共用 Runtime/消息/事件契约，通过 Domain 工具提供者区分，
不要求共享一套错误的业务政策。

Adapter 离线门槛包括消息往返、坏参数、多调用序列、官方错误、执行后断流、重复结果 ID、
pending write 恢复、各臂工具入口计数一致、官方最终 DB/reward 一致。先用官方环境的小型
脚本合同测试验证接线，不将它叫作真实模型能力。后续付费 paired pilot 需要新的任务冻结与预算授权。

## 4. 本地扩展 Domain 的 Schema 映射，尚未生成大规模数据

| 本地数据 | 官方 Task/Domain 表达 | Gold 构造与隔离 |
| --- | --- | --- |
| users、session owner | initial_state 中合成用户；user_scenario 给可知身份 | 应用注入身份，越权订单作为反例，不向模型泄露后台真值 |
| products、SKU、price_history、版本化政策 | Domain DB 与原生只读工具；Domain policy | 从冻结 SQL/政策导出事实；不要从 Agent 答案反向造 Gold |
| orders/order_items/logistics | initial_state DB、工具返回 | 时间统一 reference_date，物流返回来自冻结业务记录 |
| 多诉求及插问 | 同一 Task 的多个目标、用户场景已知/未知信息、沟通/自然语言判据 | 每项 task/entity 单独核验；不能只核查最终一个工具 |
| 草稿/预览/材料 | 本地域业务工具和状态；ENV_ASSERTION 检查中间禁区 | “用户声明”和“后台核验”分开，预览不算提交 |
| 页面明确确认 | 本地域用户工具或明确确认事件，由模拟器按任务指令操作 | 用户模型文本不能替代确认；确认绑定 revision、policy_hash、TTL |
| return_requests/reviews | 初始/目标 DB 状态与合法参考动作 | 原 ReturnService 执行参考动作，验证恰好一次创建、剩余数量、独立审核；不生成假退款 |
| 拒绝/缺条件/多候选 | 目标 DB 不变、COMMUNICATE/NL_ASSERTION、负动作约束 | 同时检查数据库不变与回答没有宣称已办，单靠 DB 不变不够 |

Gold 计划：由冻结 catalog/scenario SQL、当前政策与日期推导，用业务服务执行经过人工核验的
参考动作得到目标状态；对等待澄清/等待确认任务设定不变状态及沟通判据。作者编写后由维护者
逐题检查对象、数量、日期、授权与参考动作，争议题先隔离，不进入正式评分。
为每类判据构造已知正确/错误轨迹，包括漏答一项、选错商品、旧确认、重复写、未写却声称成功，
确认评价器能拒绝反例。实际验收前冻结 task/policy/tool/DB/判据 hash。

ECom 的 [Task 与 Persona](https://github.com/XiaoduoAILab/ECom-Bench/blob/bc5018daaf45eea12330941bce68cebd293cfa85/envs/story/tasks.py)
可用于设计耐心、信任、表达方式和意图切换维度；其
[环境评分](https://github.com/XiaoduoAILab/ECom-Bench/blob/bc5018daaf45eea12330941bce68cebd293cfa85/envs/story/env.py)
实际组合 action/search/output。复用方法时保留独立标签；本地扩展不冠以“官方 τ Retail”或
“官方 ECom”分数，不合并不同评价器的成功率。

## 5. 下一轮预注册清单

固定：上游 SHA/依赖、Domain/tool/policy/DB/Task hash、task split 与 trial seeds、各臂源码、
模型与用户模拟器及 judge 配置、通信模式、ALL/reward_basis、strict_replay、超时和步数、
请求重试、预算与未完成样本处理方式。新增验证集先冻结；本轮已见十轮属于开发烟测。

Agent、用户模拟器、评价器、工具内模型/embedding 如有调用，都计入全链路请求数、Token、
耗时和费用。无法定价或没有 usage 的部分保留预留并标记不完整。用户模型成本和 judge 成本
不摊成 Agent 自身“变贵/变便宜”。不改官方判据换取高分，若发现上游问题就记录版本与反例。
