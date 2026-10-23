# V3 统一架构与 LangGraph 最小 PoC 验收

日期：2026-10-23，Asia/Shanghai。独立分支 `feat/v3-langgraph-poc`，基线为远端 main
`f34981fa1324310a9d0f0de4430fe1980ba664e9`。实验 worktree：
`/tmp/ecommerce-v3-langgraph-20261023`；实际合成业务和 Graph 状态目录：
`/tmp/ecommerce-v3-langgraph-demo`。默认 Agent、原 Gradio 入口、生产开关、既有数据库与历史评测不改。

## 1. 结果与交付位置

**只读 PoC 真正跑通了 DeepSeek、原有工具、原生持久 Checkpointer、多进程会话接续和 Streaming。**
十轮冻结开发烟测和两轮协议审查后的追加验收均完成，原始工具决策由真实模型生成；
费用、异常、测试与语义复核分别记录，不以脚本回放当模型效果。

| 交付 | 路径 |
| --- | --- |
| 当前数据流、目标架构、一致性协议及边界 | [ARCHITECTURE_SPEC](../../docs/v3/ARCHITECTURE_SPEC.md) |
| 依赖选择、六项参考源码、实现与规划区别 | [TECH_SELECTION](../../docs/v3/TECH_SELECTION.md) |
| 逐模块迁移映射 | [OLD_TO_V3_MAPPING](../../docs/v3/OLD_TO_V3_MAPPING.md) |
| τ 旧版/2.0 协议与 Adapter、本地域 Gold 方案 | [BENCHMARK_PROTOCOL](../../docs/v3/BENCHMARK_PROTOCOL.md) |
| 运行命令、初始库、状态恢复、后续最小范围 | [IMPLEMENTATION_ROADMAP](../../docs/v3/IMPLEMENTATION_ROADMAP.md) |
| 实际 Runtime | [ReadOnlyPoc](../../src/acs/langgraph_poc.py)、[PocBudget](../../src/acs/poc_budget.py) |
| 独立入口 | [scripts/langgraph_poc.py](../../scripts/langgraph_poc.py) |
| 自动回归 | [test_langgraph_poc.py](../../tests/test_langgraph_poc.py) |

create_agent 原生构建 model/tools 图；本轮没有手写 StateGraph，没有完整 Interrupt 确认演示。
六个工具为 list_products、get_product、get_price_history、list_orders、get_order、get_logistics。
通过现有 ToolRegistry 进入 BusinessDB，Pydantic 禁止模型传身份或额外字段；归属在 SQL 再校验。
业务数据库与原始 [初始 SQL](initial_business.sql) 对应，烟测前后文件 hash 一致。
没有申请创建、订单修改、退款、审核或换货工具。

## 2. 环境、基线和研究证据

[repo_audit](repo_audit.json) 核实 main=f34981f、PR #4 仍 OPEN/draft、head=f6c3387。
原工作分支是 fix/tau-formal-recovery=f780f17；V2.1 集成和独立混合请求修复已审计，
没有为本轮只读实验整体引入。原工作区三项用户修改保留，见 [preservation](preservation.json)。
本实验基线已有的 856 份追踪历史结果文件没有修改。

Python 使用已有 3.13.12，原工作区 venv 为 3.13.13；项目声明 >=3.11、CI 为 3.13。
新增 v3 可选依赖，原有包版本变化为零，详见 [dependency_audit](dependency_audit.json)。
真实运行版本与初始代码 hash 见 [live_source_pre_review](live_source_pre_review.json)，
最终被测文件 hash 见 [tested_source_hashes](tested_source_hashes.json)。

官方文档与 43 个相关源码快照的版本/路径/hash 见 [research_manifest](research_manifest.json)。
τ 历史完整实验和后续 formal 均固定 dd8757d，包版本 1.0.1；当日上游 cc1df47 为 2.0.0。
[protocol_diff](protocol_diff.json) 显示零售 114 题解析后相同，但评价入口/通信评分设置已改变。
ECom Persona、多诉求、用户模拟器、环境工具以及两个 Anker 项目的来源绑定、接续、闸门、
消歧与评测机制均按具体源码区分；没有把占位 memory 工具或进程字典描述为生产持久记忆。

## 3. 自动验证

| 检查 | 实际结果与证据 |
| --- | --- |
| 最终 PoC 专项 | **20 passed，106.01 秒**，[poc_final_tests.log](poc_final_tests.log) |
| 全量原有回归加候选 | **718 passed、1 failed、1 skipped**，原有断流计时超阈值，[pytest_final.log](pytest_final.log) |
| 原计时断言新进程连续复验 | **3 次均通过**，不改断言，[timing_recheck.json](timing_recheck.json) |
| 相关旧模块回归 | **114 passed**，含 Agent、会话、权限、提交、审核与 HTTP 断流，[related_regression.log](related_regression.log) |
| Ruff | 通过，[ruff.log](ruff.log) |
| diff whitespace | 通过，[diff_check.log](diff_check.log) |
| CLI help、拒绝覆盖初始目录、跨用户 inspect | 退出码分别 0/1/1，拒绝路径无历史 stdout，[cli_checks](cli_checks.json) |
| 冻结安装 | `uv sync --extra dev --extra v3 --frozen`成功，只重建项目 editable 包，原依赖不升级 |
| 恢复实证 | [restored_snapshot](restored_snapshot.json)，最终 native checkpoint 无 pending 节点，保留 9 条消息 |

20 项专项覆盖：真实商品工具与最终 checkpoint；跨 reopen 的约束、更正和插问历史；
订单权限；缺字段、错类型和身份参数三种参数错误；未开放写工具；模型 length 截断；
关闭流的两个时机；DB 执行错误 status 一致；13 调用批次只执行 12 个且下一轮仍可继续；
模型失败后恢复不重放已完成读；实际子进程 os._exit(17) 后恢复 pending ToolNode；
跨用户 snapshot/ask/resume；竞争 worker；跨重启 40 次请求限制；真实 callback 路径零 HTTP
超限拒绝；金额上限前置拒绝；usage 计账与缺失时停止。

脚本模型只用于这些接线、安全和故障合同测试，不作为真实语义通过率。
复核按 check 的架构、权限及四个对抗角度执行，结果见 [review_results](review_results.json)。

## 4. 真实 DeepSeek 验收

真实模型：deepseek-flash，官方兼容接口，thinking 关闭，temperature=0，max output=1024，
SDK retries=0。在调用前冻结 [smoke_plan](smoke_plan.json)，输出由模型自然决定，
每条 ask/resume 都是独立 OS 进程，接续同一 SQLite Checkpointer。

| 冻结任务 | 观察到的执行与判断 |
| --- | --- |
| p1 商品枚举 | list_products，400 元内耳机候选；超预算款单列，不可售款说明现状 |
| p2 多轮追加条件 | 保留耳机条件、改为入耳/250 元，仅 V201，使用此前目录依据 |
| p3 商品代词 | get_product(V201)，179 元及黑/白可售 |
| o1 订单与物流 | get_order/get_logistics(S20004)，pending、暂无物流 |
| o2 订单代词与商品 | S20004 关联 V209，get_product 返回 119 元 |
| i1 临时插问 | get_logistics(S20010)，取消状态、没有物流，不虚构运单 |
| i2 返回原商品 | 回到 V201，先前真实 SKU 结果支持 ANC，不绑定到插问订单 |
| a1 多候选澄清 | list_orders 后列出多个充电器订单并询问选择 |
| a2 选择候选 | get_order(S20004)，不猜其他订单 |
| c1 明确更正对象 | get_product(V202)，369 元，提醒旧预算但不覆盖新指定对象 |
| final1 协议修正后双诉求 | 两个真实只读工具，分别回答 V201 售价与 S20004 状态 |
| final2 协议修正后重启接续 | 新进程按“那单”查询 S20004 物流，未编造承运商 |

原始每轮事件在 live_*.jsonl，首个网络失败和 resume 分别保存。
[smoke_results](smoke_results.json) 保存十轮答案、工具和业务 hash；
[semantic_review](semantic_review.json) 给出每条冻结期望与证据。
十轮期望均满足，两轮追加验收也满足；复核者是编码助手，不是独立人工盲审或付费语义裁判。
这 12 轮是开发烟测，不能推广为未知任务成功率、pass^k 或框架相对旧 Loop 的质量提升。

十轮基于协议审查前源码；final1/final2 使用 [final_source](final_source.json) 对应的三项协议修正后源码。
之后仅修正关闭生成器的终态和原生限额策略，由最终 20 项测试验证；没有把先前真实调用冒充
这些异常分支的线上复现。真实模型未自然产生错误参数，参数错误只在离线注入测试中验证。

## 5. 失败记录与修正

1. 首轮联网在沙箱中抛 OpenAIConnectionError，没有 usage。完整 stderr 和原会话保留；
   获准联网后对原 thread 运行 resume，真实完成。该例不是供应商服务故障，也不是自然重试率证据。
2. 截断响应原本会报完成、Registry 错误原生 status 原本为 success、未开放工具被拒后仍可继续
   生成办理声明。三个反例均先红，见 [review_red](review_red.log)。在 native 模型 middleware
   校验完成状态和工具集合，工具异常映射为 ToolException；[review_green](review_green.log)15 项通过。
3. 消费者在 started 后关闭漏记中断、在 completed 后关闭又记中断，两个边界均先红，
   见 [events_red](events_red.log)。初始 yield 纳入 try，已完成运行不再追加中断，最终 20 项通过。
4. 原生工具限额设为 error 时，13 调用批次会冻结在 after_model，恢复不能前进。
   [tool_limit_red](tool_limit_red.log)先复现，改用原生 continue 策略：超出项形成 error 回执，
   实际最多 12 个只读调用；模型可说明失败，后续轮次不被卡住。
5. 初次完整回归在沙箱的既有 HTTP TestClient 测试停滞，保留 [pytest.log](pytest.log)，
   没有算作通过；结束该测试进程后，在可用本机通信环境单独重跑认证测试，
   [http_auth_recheck](http_auth_recheck.log)1 项通过。完整回归随后以空模型密钥重新执行。
6. 本轮完整回归最终出现既有断流计时断言失败：2055.35ms，原阈值 1800ms。
   该类波动在 [既有问题记录](../../docs/PROBLEM_SOLVING.md)中已有，本轮旧 Runtime 和该测试
   字节未改。连续三次新进程复验均通过，日志 timing_recheck_1/2/3.log 保留；不删整套失败，
   不放宽断言，也不将定向通过说成整套已通过。该本机计时稳定性仍是限制。

本轮未增加业务表达正则，也没有做通用自由文本幻觉审查。即使没有工具调用，模型仍可能
生成不实文字；这里的协议拒绝只能阻止被识别为未开放工具的执行意图后继续生成声明。
模型事实绑定、混合任务完整性与自由文本真实性仍是后续独立质量验收范围。

## 6. 费用与停止条件

调用前记录最多 40 次、累计保守预留 5 元、不退款、不自动供应商重试、缺 usage/超 Token 界限停止。
预留先写账本再发请求；离线 MockTransport 测试证明超限时实际 HTTP 次数为 0。
初始预计 20 至 30 次请求、约 30000 输入/5000 输出 Token、峰值估算 0.108 元；实际历史消息输入更多。

最终 [requests.jsonl](requests.jsonl) 与 [cost_summary](cost_summary.json)：

| 项目 | 数值 |
| --- | --- |
| 请求预留尝试 | 23 |
| 成功供应商响应 / 连接失败 | 22 / 1 |
| 成功请求输入 / 输出 Token | 65,989 / 1,567 |
| 其中 cache-read 输入 Token | 52,864 |
| 累计不可退款保守记账 | **1.63685664 元**，小于 5 元 |
| 成功请求按峰值、全部输入 cache-miss 估算 | **0.15607512 元** |
| 失败请求费用 | usage 未知，保留其完整预留；不当成免费或 0 Token |
| embedding / 用户模拟器 / 付费裁判 | 均 0 请求 |

使用当日官方峰值输入/输出 0.3/1.2 USD 每百万 Token 与项目原有 7.2 汇率。
估算不等于供应商账单；保守记账包含失败预留，不以缓存优惠或失败退费扩大预算。
真实模型请求已停止，没有完整 τ/ECom 收费 Benchmark。

## 7. 五项结论与下一轮

1. **PoC 真正跑通**：有真实 DeepSeek 工具决策、native checkpoint、多进程多轮接续、
   真实连接失败后的恢复、子进程退出恢复、流事件与费用账本，代码和测试路径均可直接运行。
2. **LangGraph 已承担执行循环与中间状态恢复**，不再为该只读子图手写 generator 执行栈。
   它未解决业务授权、对象语义、混合诉求漏项、跨库原子性、写操作幂等、旧确认有效性、
   自由文本真实性或可靠 SSE 重连。本轮无同条件旧 Loop 对照，不宣称更快、更省或质量更高。
3. **完整退货迁移仍需联合改动**：ChatService、SessionStore 边界、ReturnService 预览/确认/提交、
   ToolRegistry、Gradio 确认事件、Graph 业务回执与本地域评测。原事务、revision、TTL、
   policy_hash、身份和剩余数量校验必须保留；本轮没有迁移实际退货写入。
4. **τ Adapter 可以部分复用**：冻结/预算/官方环境/返回协议校验可用；旧 ACSState 和
   chat_stream/messages/trace transport 须重写成 Graph 暂停与 tool_call_id 注入。不能直接替换
   loop_class；候选 Adapter 本轮未实现，也未取得新官方成绩。
5. **Go：下一轮仅做固定版本官方 HalfDuplex Adapter 离线合同**，随后另行申请小额同条件 pilot。
   **No-Go：默认 Loop 替换、完整退货迁移、换货、React 或生产开关、大规模付费 Benchmark。**

完整模块清单、接口、确认一致性与验收条件见五份设计文档。实验按本轮边界结束，
不把下一阶段设计当作已完成能力。
