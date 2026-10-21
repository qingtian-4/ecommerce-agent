# 电商客服 Agent 架构改进方案

审查日期：2026-10-20。本文是源码审查与实施提案，生产修复尚未实施。

本仓库审查基线为 `3a4de7c2d9e1da5dd74335b8e5a72d6c1daead21`，工作分支为
`main`。参考项目固定到下列提交，避免将后续变化混入结论：

| 项目 | 审查提交 | 取证方式 |
| --- | --- | --- |
| anker-OntoCare | `32ab8fa405cacc680db434ed837d63bf3604d6fd` | 阅读实际接口、规则、回答生成与评测代码 |
| anker-haike | `adb90ba405c18519882834ce57122c234db5c7cf` | 阅读解释、归约、对象绑定、任务、审核、执行与持久化代码 |

参考代码只作静态阅读，未安装或运行其服务、模型与评测程序。本轮没有调用付费 API，
没有重跑大型 Benchmark，也没有改写冻结任务或原始结果。

## 1. 决策与范围

**优先补足业务状态的结束条件，推荐 P0 方案 A：受限的订单草稿完整性检查。
本轮不需要 Fact Ledger，也不需要新增状态机平台或审查模型。**

三项最值得做的改进按以下顺序推进：

| 优先级 | 改进 | 解决的问题 | 本轮状态 |
| --- | --- | --- | --- |
| P0 | 明确退货请求的草稿完整性检查 | 模型查到订单、询问商品，却漏存已知订单 | 根因已复现，给出两个方案，暂不修改生产逻辑 |
| P1 | 按业务对象组织的来源与上下文投影 | 区分查询对象、办理对象、用户声明与后台事实 | 设计边界，等待独立失败证据决定扩展范围 |
| P2 | 回答与证据、执行回执的一致性约束 | 有工具结果仍复述错事实，或错误归因历史申请 | 提出分层约束与验证方法，不建设通用语义审查器 |

继续沿用 Python、自定义 Agent Loop、SQLite、Gradio 和现有 RAG。
保留“预览 → 用户明确确认 → 事务提交 → 管理员审核 → 查询”的业务边界。
不扩展实际退款、真实换货或实时人工接管；不引入 LangGraph、WeKnora、Neo4j、
Multi-Agent、后训练或新的产品 API。

范围与历史执行约定继续以 [PROJECT_SCOPE.md](PROJECT_SCOPE.md) 和
[EXECUTION_PLAN.md](EXECUTION_PLAN.md) 为准。P1、P2 不与 P0 捆绑实施。

## 2. 架构对照：应该借鉴什么

| 维度 | 本仓库 | OntoCare | anker-haike | 对本项目的判断 |
| --- | --- | --- | --- | --- |
| 服务对象与事实 | SQLite 订单与明细、知识文档、草稿字段来源 | Service Ontology 实际落在产品、订单、Q&A、工作流与来源标识上 | BusinessContext 将对象标识、用户信息与后台观察分开 | 复用现有 SQL 与文档元数据，补对象绑定，不建图数据库 |
| 决策 | ReturnService 做资格、预览与提交校验 | `allowed_actions` 从案件状态产生允许与禁止动作 | reducer 管理状态，gate 汇总资格、证据、确认、安全、幂等与审查 | 本项目执行权限已较完整，缺口主要在解释到状态写入之间 |
| 原话、否定、更正 | 原始轮次和 `return_tasks.sources` 已持久化；更正会清理依赖字段 | 对话历史、产品匹配、规则和用户信号共同决定回复 | FactCandidate 带 quote、subject、polarity；更正归约保留替代关系 | 借鉴候选解释与权威状态分离，不能把模型抽取直接当事实 |
| 多诉求 | 单会话退货草稿加普通查询，尚无显式查询对象投影 | 主要按当前案件焦点推进 | tasks 绑定业务对象；插问查询不消费办理任务 | 先区分查询焦点和草稿目标，无证据时不建通用任务队列 |
| 最终回答 | 自由文本，记录检索和工具轨迹 | grounded_response 输出 reply/source_ids，校验失败退回规则回复 | 独立 review 审业务前提，gate 后 executor 才能执行 | 引用、事实、回执分别校验；不直接增加一个评审模型 |
| 持久化与恢复 | SQLite 会话、活跃轮次、草稿 revision、事务与幂等提交 | 主对话 `SESSIONS` 是进程内字典 | PostgreSQL 会话与 checkpoint，加独立执行幂等、租约和 fencing | 不替换现有存储；checkpoint 不等于业务操作幂等 |
| 评测 | 合成本店多轮任务、轨迹与独立 τ 零售适配 | 红方生成或脚本追问，蓝方走真实接口，规则加可选语义估计 | 原话、业务对象、执行协议有针对性的测试和约束 | 借鉴困难结构与故障注入，不搬运商品数据或引用其得分 |

### 2.1 OntoCare 的实际运行链路及局限

[O1] 的接口维护案件，调用 `allowed_actions`，组织知识引用，然后在适用路径调用
[O3] 的 `generate`；生成失败保留规则回复。Service Ontology 和 Decision Ontology
在这里的实用价值是“对象、来源、决策与表达有明确边界”，不依赖本体数据库。
[O2] 从表格来源生成带命名空间的标识，处理旧编号冲突；[O4] 给引用提供原始表格定位。
这些做法适合借鉴为本项目的来源协议，不能作为另建产品事实库的理由。

`grounded_response.generate` 把用户声明、订单快照、知识与执行凭证分开，要求模型
输出 `reply` 和 `source_ids`，随后检查引用有效性、语言、段落及部分越界措辞。
引用编号由程序插入，而非让模型自行编造。[O3]

局限也很明确：来源 ID 存在不代表它支持正文每个断言；正则无法覆盖所有虚假完成表达。
当前生成载荷中的 `executed_actions=[]`、`ownership=NOT_VERIFIED` 也说明这条链路
不是经过真实退款执行器验证的交易系统。其 `action_gateway.py` 主要投影可公开的数据，
不能因文件名就把它当作真实操作网关。[O1][O3][O5]

红蓝评测让红方提供脚本或模型追问，蓝方实际走 Flask 接口，裁判分开记录规则检查与
可选语义估计；runner 隔离会话、固定参考时间并检查故障注入是否命中。[O6][O7][O8][O9]
应借鉴对象互换、否定、反证、故障恢复这些测试结构。动态红方和语义裁判会增加调用，
本轮不运行；引用可解析和模型裁判通过均不能单独证明回答真实。
蓝方在评测期间还额外拼接评测指南，不能把其结果直接视为未注入指南的生产配置结果。
其中 `answer_ttft_ms` 记录完整接口返回耗时，不能直接当作我们流式界面的首 Token 延迟。[O8]

### 2.2 anker-haike 的实际运行链路及局限

`interpret.py` 将模型输出限制为解释候选：每个请求有原话，事实有对象、肯定/否定/
不确定、时间和引用，更正引用既有事实。它还包含确定性的否定修正与事实补全，说明
结构化输出并不能替代语义校验。[H1]

`reduce.py` 验证引用是否来自输入，按输入 ID 防重，按 issue 保存事实及修订；更正时
标记旧事实被替代或撤回，变更会使旧计划和待确认输入失效。`business.py` 将用户信息
与后台观察分开，要求回执对象匹配请求对象；任务对象可以在副本上恢复，避免插问移动
聊天焦点后，把办理结果附到另一订单。`tasks.py` 进一步把并列诉求绑定到对象。
这些模块由实际的 sessions graph 调用，不只是未接入的设计文件。[H2][H3][H4][H9]

`actions/review.py` 将客户原话、订单行与业务前提交给独立审查，并非让执行模型自行
宣称已核实；`gate.py` 汇总资格、证据、用户确认、安全、幂等和审查；`executor.py`
在通过检查后执行。响应不确定时，使用 `client_ref` 查询既有结果，再决定是否执行，
配合执行存储的租约与 attempt 防止旧执行者覆盖新结果。[H5][H6][H7][H10]
会话/checkpoint 与执行幂等是两套职责；持久化确实接入 PostgreSQL，而不是只有
内存状态。[H8]

局限：逐字引用只证明用户说过，不能证明理解了否定作用域或业务事实为真；这些结构
仍需领域规则。独立审查还引入额外模型调用、超时和不可用分支，部分路径允许绕过
模型审查。不能把它等同于人工确认，更不能替代后台权限与事务。本项目已经有真实的
预览、确认、提交和管理员审核，P0 不应复制整套审查与执行平台。

## 3. 已确认的缺陷与证据边界

### 3.1 `orders-02`：已知订单没有保存为部分草稿

冻结证据是 [local_after/raw.jsonl](../results/iteration_v2_recovery/local_after/raw.jsonl)
第 21 行，`id=orders-02, trial=1`。该文件 SHA-256：

```text
151d1419169076d50b95cdeed30c9533c06f69f03740953d7f50a372d902e9aa
```

四轮用户原话依次为：

1. `我想退S20017。`
2. “这单里有两个商品吗？”
3. “我还没想好退哪件。”
4. “请让我选择，不要自行决定明细。”

首轮 `get_order(S20017)` 成功，订单属于 `USER001`，有两个明细。模型正确地追问商品，
但四轮均未调用 `update_return_task`，最终 `task=null`，无运行异常。
任务定义要求 `order_id=S20017, item_id=null`，不要求猜商品或创建申请。
因此这是状态遗漏，不能解释为缺少商品就不能存订单，也不是应放宽的评分错误。
任务定义见 [cases.json](../data/eval/multiturn_v1/cases.json) 中 `orders-02`。

源码证据：

- [service_agent.py](../src/acs/service_agent.py) 第 92 行附近已提示保存已知字段；
  第 311 至 321 行遇到无工具的模型回复就设置 `answered`，未校验业务状态。
- [chat_service.py](../src/acs/chat_service.py) 第 107 至 112 行直接 `finish_turn` 并发出
  `completed`。它校验会话生命周期，没有检查明确诉求对应的草稿是否存在。
- [sessions.py](../src/acs/sessions.py) 第 297 至 383 行允许只写订单，校验归属并记录原话。
  因此数据库能力并不缺失；现有直接调用存储层的测试未覆盖“模型遗漏调用”的完整链路。
- [历史台账](../results/iteration_v2/historical_issue_ledger.json) 也记录过同一缺陷。
  之前增加提示仍未可靠阻止遗漏，继续只改 Prompt 不构成修复证据。

根因分两层：模型这次没有发出必要的状态更新工具调用；运行时又把“生成完回答”作为
本轮结束条件，没有独立检查已明确的最小业务义务。首轮没有选商品是正确行为，不能
通过自动选择明细来修复。

本轮用真实 ChatService、ToolRegistry、SQLite 和假的模型响应做了离线回放，禁止网络，
重放成功订单查询及原始四轮回答。结果为 **1 项预期失败、2 项对照通过**：最终草稿
确实为空；直接存订单成功；“只查询订单，不办理退货”不会建草稿。附录提供复现代码。
另运行现有 `tests/test_task_memory.py`、`tests/test_chat_service.py`，28 项通过。
这些结果证明控制流缺口和已有存储能力，不代表真实模型遗漏概率或修复后的成功率。

### 3.2 回答有来源，仍可能把对象属性说错

同一冻结文件第 23 行，`orders-01, trial=1`：首轮 `list_orders` 返回 S20008 的
A1 耳机 SKU `V201-2` 为白色，回答却将 S20008 与 S20003 合并称为黑色。
这里已人工对照实际工具载荷与回答，不能仅以裁判标签为依据。
该条确定性检查通过，说明“调用了正确工具”和“最终任务字段正确”仍不能覆盖属性复述。

当前 [service_agent.py](../src/acs/service_agent.py) 直接接受最终自由文本，
没有把订单、明细、颜色等断言逐项绑定到实际结果。这为 P1 的对象投影和 P2 的事实
呈现提供依据；尚不能据此断言当前数据库草稿发生跨订单污染。

### 3.3 历史虚假完成有证据，但部分防线已经存在

[历史台账](../results/iteration_v2/historical_issue_ledger.json) 的 `boundary-06, trial=2`
记录过：当前没有草稿，纯聊天文本模拟点击，模型却把已有申请说成“这次申请已经成功
创建”。原始来源及哈希均在台账中。这是历史证据，不能说当前版本一定仍有相同漏洞。

当前 [cs_tools.py](../src/acs/cs_tools.py) 已在 `get_return_requests` 增加
`current_session_request_id`；[returns.py](../src/acs/returns.py) 的
`confirm_and_submit` 只能由明确确认路径触发。应该加强回执约束与反例测试，复用这些
防线，不能再造一套提交网关或以聊天文本代替确认。

## 4. 三项改进的最小设计

### 4.1 P0：给明确诉求增加独立的状态后置条件

**复用：** ChatService 轮次边界、原始输入、当前工具结果、SessionStore 更新及来源、
ReturnService 的所有既有约束。借鉴 haike 的“解释候选须归约为状态”和 gate 的明确
条件列表，不复制全量解释器、review 模型或任务队列。

**最小实现：** 对一个范围明确的退货请求，终轮检查“订单已保存且未猜明细”。仅在用户
原话明确请求退此订单、同轮已成功查询本人订单、无既有草稿记录时，允许通过现有存储
方法补存 `order_id`。其他情况只记录不满足原因或请求澄清。详细条件见第 5 节。

**替代：** 后置条件失败后给原模型一次受限工具修复机会。它仍须经过相同校验，不能
因为又加了一段提示就算成功。两种方案都不需要完整 Fact Ledger。

**范围与风险：** 增加一个可独立关闭的内部检查；不改业务表结构、公开工具或
ReturnService。主要风险是错误识别用户意图、重复补写导致 revision 增加、终态显示与
持久化不一致。必须以否定反例、原子条件写入及会话集成测试控制。

**验收：** 冻结轨迹在开关关闭时仍复现遗漏，开启时只保存订单；所有不应补写的输入
零新增草稿、零覆盖、零新增申请。新增模型/embedding 调用均为零。

### 4.2 P1：对象与来源明确的上下文投影

**证据与不足：** `orders-01` 证明模型会合并不同订单的属性；历史对象类型混淆见
[contrast_before_development/raw.jsonl](../results/iteration_v2_recovery/contrast_before_development/raw.jsonl)
的 `negation-01-positive`：曾把订单号传给 `get_product`。后者是历史开发证据，
不应当作当前版本仍失败。`return_tasks.sources` 记录字段原话，但不表达完整的
对象、否定与事实冲突；ChatService 当前还主动从模型草稿投影中去掉 `sources`，以控制体积。

**参考与复用：** 借鉴 haike 的 subject/reference 与后台观察归属，而非复制事实账本。
复用当前 task、revision、来源 turn_id、工具调用参数和 SQL 返回值。用户说“没收到”
与物流记录“已签收”可以同时保留，不能用一个布尔值覆盖另一种来源。[H1][H2][H3]

**最小方案：** 在现有上下文构建处生成有界投影，区分办理对象 `task_order_id/item_id`
与查询结果的 `query_order_id`；每个业务观察附请求对象和当前轮结果引用。
仅为当前有效草稿字段附短来源引用，原话仍从原始轮次追溯，不复制整段历史。
更正继续走现有更新方法，清理依赖字段和过期预览；插问只增加查询观察，不改变办理对象。
不确定指代不创建新绑定，要求澄清。用户文本继续作为数据隔离，不能提升为系统指令。

这是受限的上下文组织改进，不宣称可以识别任意自然语言否定或更正。若要阻止模型主动
调用工具串单，还须针对已复现的模式增加写入前的原话与对象检查；投影本身不是权限边界。

**替代与范围：** 可以先只改订单候选的确定性展示，减少模型自由合并；收益更窄，风险
更小。只有观察到跨任务、跨会话的长期冲突无法由来源和 revision 表达时，才评估增加
结构化事实历史。P1 预计限于 context/chat_service 与对应测试，不先新增表或任务调度器。

**风险与验收：** 投影体积上升、过期事实被重复使用、缺失指代被误绑定。
用现有否定、更正、插问、冲突成对任务验证：退 A 查 B 后仍办理 A；用户明确改为 B 时
清空 A 的明细；“不是坏了”不得变成质量故障；用户陈述与 SQL 记录分别呈现。
统计每组对象误绑定、重复索取已知字段、输入字符与实际 Token；未做实验前不宣称提升。

### 4.3 P2：让关键事实与办理状态由证据约束

**证据与复用：** `orders-01` 的颜色错误和历史 `boundary-06` 的错误完成归因。
复用 `retrieved_documents`、工具结果、文档版本/哈希、草稿 revision、预览和申请回执。
现有 RAG 已有 chunk/document 标识及索引版本校验，无须另建检索系统。

**参考：** OntoCare 把规则、证据、回答和引用分开，生成失败退回有依据的规则回复；
haike 把审查与执行、执行与回执分开。引用存在、审查模型赞同、工具调用成功三者
都不能直接推出“退款已完成”。[O3][H5][H6]

**最小方案：** 先约束可确定验证的事实。订单候选的订单号、明细、颜色由 SQL 结果
确定性呈现；草稿保存、预览生成、申请创建、管理员审核四种状态分别依据真实回执，
不让自由文本把它们合并为“办理完成”。查询历史申请时保留申请号与当前会话归属，
响应丢失按现有结果查询与幂等路径恢复。没有成功回执时只能说未确认办理结果。

政策和规格则携带产品/文档/片段/版本引用；应用校验引用确实属于本轮证据集合，
按对象展示来源入口。涉及多个产品或政策时，不把同一引用泛化到全部对象。
这一层只能保证来源可追溯，不能保证每句自然语言被来源蕴含。无依据的政策结论应
回到已有文档或澄清，不用一个通用正则假装解决所有语义一致性。

**替代与范围：** 更强方案是模型输出 claim/source_refs 后再做语义核对，或增加独立
review 模型；需要新增协议、误拒测量和额外预算，当前不选。最小方案优先修改确定性
事实呈现与现有 UI 操作结果展示，随后才扩展回答协议，不改变执行权限。

**风险与验收：** 引用有效但内容不支持、把用户声明当 SQL 事实、额外模型审查增加成本，
以及流式文本已展示后才拦截。测试颜色互换、来源 ID 正确但产品错误、旧申请冒充本次、
预览冒充提交、审核通过冒充退款、响应丢失后重试。规则分数、语义未判定和人工复核
分别报告；P2 不借助本次 P0 通过结果声称已经解决语义一致性。

## 5. P0 两个候选方案

### 5.1 方案对照与 Fact Ledger 决策

| 项目 | A：受限的确定性检查与补存，推荐 | B：后置条件加一次模型工具修复 |
| --- | --- | --- |
| 意图依据 | 当前用户原话的完整白名单表达，加同轮本人订单结果 | 相同的原话与对象约束；不把模型提到订单当授权 |
| 遗漏处理 | 通过现有存储层仅补 `order_id` | 原模型最多一次请求，只准补对应订单字段；工具后查状态并确定性收尾 |
| 结束条件 | 已存则不写；可补才补；冲突或证据不足明确记录 | 模型未修复、超预算或状态不符均算未修复，不能强行通过 |
| 模型成本 | 不增加任何调用 | 最多增加一次调用及其输入、输出 Token |
| 工程改动 | ChatService 终轮挂点、纯检查、原子防覆盖和开关 | 还需 Loop 续跑、受限工具协议、共享预算、消息与流式收敛 |
| 覆盖与局限 | 有意只覆盖明确有限表达；未识别不自动补存 | P0 入口与 A 相同；仍可能遗漏、误解或超时 |
| Fact Ledger | 不需要 | 不需要；需要一次受控续跑的状态记录，而非全局事实账本 |
| 适用判断 | 能直接修复当前已证实的遗漏，零额外模型消耗 | 后续确有更复杂的更新需求且额外预算获授权时再评估 |

P0 所需证据已经存在：原始 conversation_turn、草稿字段来源、订单归属、revision、
工具结果和执行轨迹。增加全局 Fact Ledger 既不能保证模型发出更新调用，也会引入
事实撤回、版本、对象归并和双份存储一致性问题。因此选择更简单的 A。
若以后真正需要多个独立办理任务、长期反证与可回溯事实合并，再用具体失败评估账本。
方案 B 在当前订单单字段问题上没有覆盖优势。扩大自然语言入口还需单独证明解释与
原话绑定可靠，不能靠增加一次模型请求就宣称获得更广的安全覆盖。

### 5.2 方案 A 的触发条件与拒绝边界

检查只在普通 `answered` 的终轮、`finish_turn` 之前执行，此时轮次仍为 running。
中断、超时、模型错误和超步数不做自动补存。读取当前权威状态，不以模型自述为准。
沿用本轮 RunBudget，检查前核对剩余时间，不重置截止时间；最多补写一次，单列服务端
状态写入与耗时，不抹去已有工具调用或错误记录。

第一版完整匹配当前用户原话的有限表达，例如 `我想退S20017。`、`我要退 S20017`。
只允许空白和句末标点规范化，不删除否定、引号、条件或其他词；模板中的订单标识还须
与工具返回完全对应。多个分句、多个订单、第三人称、转述、引用、假设、否定与暂停
均不匹配。它是已知路径的窄入口，不是通用意图识别器，不通过追加关键词扩大覆盖。

匹配后依次检查：

1. 当前用户、session、turn 均由应用绑定。不能接受模型提供的身份或会话编号。
2. 当前草稿已是同一订单：视为满足，零写入、零 revision 变化；不清空已知明细。
   已有不同订单、cancelled 或 submitted 记录：不自动覆盖或复活，记录冲突。
3. 仅在完全没有草稿记录、且同轮 `get_order` 成功返回该用户的同一订单时允许补存。
   校验实际参数与实际结果，不使用答案文本、历史工具结果或候选列表作为授权。
4. 不存在订单、他人订单、查询失败、缺少查询或结果对象不一致：不写，不增加模型
   调用或隐式后台补查；记录缺口并说明尚未保存。订单归属仍由存储层再次校验。
5. 补存只能包含 `order_id`。不推断 item、数量、原因、商品状况、资格、预览或确认。
   只有调用存储层成功并重新读到预期状态后，才能说已记录订单、尚待选择商品。

“请查 S20017”“不是要退 S20017”“别人说‘我想退 S20017’”“如果我要退 S20017”
以及模型自己提到 S20017 都不能触发补存。原话明确办理才是意图依据，订单号只用于
绑定对象。更复杂表达保持现有 Agent 路径或澄清，不默认新建草稿。

### 5.3 数据结构、原子性与执行位置

只增加本轮内的检查结果，不增加业务表。轨迹事件可包含：检查版本、status、
意图原话及 turn_id、order_id、订单结果的 trace 索引、检查前后 revision、补存结果
或拒绝原因。status 区分 `not_applicable/satisfied/repaired/blocked/conflict/error`。
原话是用户诉求来源，SQL 结果是订单存在与归属来源，二者不能互相冒充。

补存继续调用 `SessionStore.update_return_task`，复用权限、字段验证和
`sources.order_id={turn_id,user_text,type:user_claim,...}`。不要伪造一次模型工具调用；
用单独的 `state_check` 轨迹说明这是服务端执行的受限补存。

现有更新方法即使重复写相同值也会增加 revision；直接调用还可能覆盖已有订单。
因此最小必要调整是在同一个既有 `BEGIN IMMEDIATE` 事务里增加仅供内部使用的
`only_if_absent` 条件，默认关闭，不暴露给模型：

- 先验证会话归属与当前活跃轮次；提交态仍按现有规则拒绝。
- 该条件只允许订单单字段补存且不能与 cancel 同用。事务内发现任意既有任务时返回
  当前状态，不覆盖、不增加 revision，由调用方区分已满足和冲突。
- 无记录才执行原有校验和保存。重复进入检查、检查与实际写入之间出现变化时也不会
  覆盖。正常工具更新的默认语义不变。

这不是新增 ReturnService，也不是新数据层抽象；它是复用现有事务时所需的窄条件写入。
不应依赖“通常不会并发”或一个事务外的先查再写来保证防覆盖。

执行顺序：

```text
当前原话 + 当前工具实际结果 + 权威草稿状态
  → 受限意图与对象检查
  → 已满足 / 不适用 / 有条件地仅补存订单 / 明确阻塞或冲突
  → 重读草稿、记录检查轨迹、同步终轮展示
  → finish_turn → completed
```

若需追加“已记录订单，尚未提交申请”或“订单草稿尚未保存”的确定性说明，必须同时
更新 `AgentResult.answer`、`agent.messages` 中的最终 assistant 消息和 final 轨迹，
再由 `finish_turn` 保存。不能多插入一轮伪造对话，否则会破坏现有消息与结果一致性检查。
补存失败不宣称成功，不发起无限重试，既有草稿与业务申请都保持原状。

当前流式文本在终轮前已经展示。这个挂点能约束最终保存状态和最终结果，不能撤回用户
已经看到的中途文字，更不能据此宣称实现了全面的流式回答审查。若最终文字与实际状态
存在可确定冲突，终轮必须明确更正；更强的关键办理结果延迟展示属于独立 P2 设计。

### 5.4 修改范围、开关与回退

后续实现只允许触及以下职责；必要的实现、回归测试和问题记录组成一个完整可回退改动：

| 文件 | 计划改动 |
| --- | --- |
| `src/acs/return_state_check.py`，新增 | 受限意图识别、证据与状态检查，不调用模型 |
| `src/acs/chat_service.py` | 终轮检查挂点、重读草稿、最终文字一致性 |
| `src/acs/sessions.py` | 既有事务中的内部 only-if-absent 条件，默认行为不变 |
| `src/acs/config.py` | 独立布尔开关 `return_state_check_enabled` |
| `.env.example` | 说明 `ACS_RETURN_STATE_CHECK_ENABLED` 与受限覆盖范围 |
| `tests/test_return_state_check.py`，新增 | 纯检查及真实 ChatService/SQLite 的无网络集成测试 |
| `docs/PROBLEM_SOLVING.md` | 实施后记录失败、根因、实际结果与局限 |

不修改 `returns.py` 的提交和审核语义，不新增工具、产品 API、表结构、索引或持久化
事实账本。没有必要时不调整 Agent Loop 本体。

开关默认关闭；候选实验显式开启，同一提交可用关闭状态作消融。关闭时不运行检查、
不追加来源投影或检查文字，保持原路径。只有通过离线验收后才单独决定默认启用，不能
在比较时偷偷改变其他设置。回退关闭开关即可，不删除已经合法保存的草稿，也不需要
数据库迁移。若模型仍主动错误写入，这个遗漏检查并不能修复，必须另列缺陷。

## 6. 测试计划与度量

### 6.1 本轮已完成与尚未完成

已完成源码阅读、原始记录核对、无网络的红测试和两个对照，以及 28 项既有任务记忆/
会话测试。红测试的失败是预期的缺陷证据，不是修复后的验收失败。
尚未实现开关、补存或新的生产检查，尚无任何本轮成功率、延迟、Token 改善结果。

后续先把附录红测试迁入正式测试，再冻结下表的成对输入与预期，使用 ScriptedLLM
驱动真实 Loop、ChatService、ToolRegistry、SessionStore 和临时业务快照。不是只测
意图函数或直接调用存储层；所有网络访问应被测试夹具禁止。

### 6.2 P0 必须覆盖的任务

| 任务组 | 可复现输入或故障 | 开启检查后的断言 |
| --- | --- | --- |
| 原始遗漏 | 原始 orders-02 四轮，模型只查订单不更新 | 首轮保存 S20017，最终 item=null；无申请 |
| 对照消融 | 同轨迹关闭检查 | 仍无草稿，证明实际走到新增机制 |
| 模型已更新 | 模型正常调用 update_return_task | 不重复写，revision 与关闭组相同 |
| 单纯查询/提及 | `只查询S20017，不办理退货。`；答案里提订单 | 不创建草稿 |
| 否定/假设/转述 | `我不想退S20017。`；`如果我要退S20017`；引用别人原话 | 不创建草稿 |
| 多对象/复杂表达 | `退S20017还是S20001我再想想`；`我想退S20017，但先别保存` | 不自动选对象，不补写 |
| 缺少必要证据 | 明确退货但未查订单、查询错误、结果对象不匹配 | blocked，无隐式查询或补存 |
| 权限 | 返回未找到、他人订单、错误 user/session/turn | 拒绝，无他人数据及草稿写入 |
| 已有任务 | 已有同订单草稿、另一订单草稿、cancelled、submitted | 同订单零写；其余不覆盖、不复活 |
| 插问 | 已在办理 A，查询 B 后继续 A | 查询不改变 A；不自动创建 B 草稿 |
| 重入/并发变化 | 同挂点两次；首次检查后另一写入先完成 | 最多一次新增，不覆盖，revision 无额外增加 |
| 商品选择 | S20017 两明细且用户没有选择 | item、quantity、reason 等均保持未知 |
| 终态一致 | 补存成功或抛验证/存储异常 | 回答、final trace、历史、任务面板与实际状态一致 |
| 中断/预算 | 流断开、模型错误、超时、超步数 | 无自动补存，保留原异常恢复语义 |
| 恢复 | 补存后 finish_turn 前中断；重启后加载与重试 | 草稿可恢复，同订单不重写；不自动提交 |
| 预览与提交 | 已有预览的同订单 no-op；旧预览、超量、缺材料、重复确认 | 既有约束全部保留，no-op 不使预览无故失效 |
| 审核边界 | 非管理员审核、非法状态转换 | 既有拒绝有效，检查无审核权限 |

冻结时给每个反例配一个明确可补存或合法更新的正例，防止“全部拒绝”也通过。
目前看过结果的 orders-02 和新增探针均是开发/回归材料，不宣称未见验证集。
新验证集必须先冻结、再在候选冻结后运行；看过并参与调优即转为开发材料。

### 6.3 成功、回归、延迟与 Token 的验收口径

| 指标 | 分母与验收条件 |
| --- | --- |
| 遗漏修复 | 确定进入受限路径、查询成功、无草稿且模型漏更新的探针；全部恢复订单字段 |
| 误补存 | 所有不应新增草稿的负例；新增草稿数必须为 0 |
| 覆盖/重复 | 已有任务、重入和并发探针；错误覆盖、重复补写、额外 revision 均为 0 |
| 业务安全 | 申请、确认、审核、权限、事务相关断言；不得有新增失败 |
| 回归 | 开关关时通过而开时失败的用例数，除以关时通过数；目标 0，失败不得丢弃 |
| 机制延迟 | 单独测检查和事务的 p50/p95，记录硬件、样本数及失败；候选目标 p95 ≤ 20 ms，不作为已测结果 |
| 完整延迟 | 相同脚本、快照和条件下的端到端及首文字延迟；离线耗时不能替代线上模型延迟 |
| 模型消耗 | 同一脚本下 API/embedding 调用与 usage 差值必须为 0；服务端写入不能假记为模型工具调用 |
| 上下文代价 | 单列恢复后下一轮新增的草稿上下文字符；没有实际 usage 时不冒称 Token 为零或节省 |
| 未判定 | 缺少模型语义判定、环境失败、数据缺失单列，不强行记成功 |

上述零新增模型调用指补存机制本身。真实 Agent 的后续轮次可能因草稿投影改变而改变
输入 Token 和工具选择，真实总量收益须另行对照，不能由假模型推断。

P0 实施阶段先跑针对性测试，再执行完整 `pytest` 和 `ruff check`，最后做本地页面
保存草稿、预览、确认、审核、查询和重启恢复验收。使用临时数据库、独立结果目录，
冻结源码、任务、快照、检查版本、开关及预算；结果同时保留失败与错误。

本轮及 P0 无网络验收不运行 `eval_multiturn` 或 `task_eval.run_episode` 的真实 Agent。
**关闭 judge 不等于关闭模型调用**，不能把 `judge=False` 当作免费测试模式。
如以后另行授权在线评估，才按相同任务和重复协议比较，不追加挑选成功的尝试。

历史 80 段回归两组严格结果均为 **140/160**，确定性 158→159、未判定 9→14，
见 [最终报告](../results/iteration_v2_recovery/final/REPORT.md)。这些是历史结果，
不能称为本提案或未来检查机制已提升成功率。

τ 零售继续使用官方政策、商品数据库、任务、工具、用户模拟器与评分，适配的是本项目
Loop 的工具接口，不是把扫地机器人替换为本店耳机或重写任务。它测外部业务环境的
工具执行与任务能力，不能直接证明本店 ReturnService、RAG 或 P0 的草稿修复效果。
本提案不升级上游、不改官方分数，也不重跑全量 τ 评测。

## 7. 风险与实施顺序

| 风险 | 控制与回退 |
| --- | --- |
| 白名单意图覆盖有限 | 明确只承诺已冻结入口；未识别保持原路径，不自动放宽表达 |
| 引用或否定被错误规范化 | 不删除语义词，不做子串触发；成对测试引用、否定、条件和多对象 |
| 同订单重复写使预览过期 | satisfied 不写；事务内 only-if-absent 防重入和覆盖 |
| 数据已存、轮次未完成 | 重启读权威草稿；允许合法部分状态存在，重试不重复更新或提交 |
| 最终结果和已流出的文字冲突 | 同步终轮三处文本并明确更正；不宣称可撤回流式内容 |
| 增加来源造成上下文膨胀 | P0 不增加通用来源投影；P1 另做限额和 Token 对照 |
| 有限检查被误认为完整语义保护 | 明示不处理模型主动错写、不保证任意事实蕴含；P1/P2 独立验收 |
| 看过失败后围绕分数调题 | 冻结输入、判据与开关；保留原始数据和全部失败，分开报告评分修正 |

交付顺序如下：

1. 当前交付此提案、源码依据与离线复现结论，生产代码保持原样。
2. 在原分支迁入红测试，冻结正反例与开关消融协议；不调用付费 API。
3. 仅实现方案 A 的最小检查和原子补存，连同测试、问题记录一起形成完整可回退改动。
4. 完成离线回归、工程检查与本地恢复验收，报告修复率、误写、回归及实际开销。
5. P0 证据充分后分别决定 P1、P2 的具体子项；若离线证据不足以证明真实模型收益，
   明确保留这一限制，不擅自启动付费 Benchmark 或额外审查模型。

## 附录 A：可复现的无网络红测试

在仓库根目录，将以下代码保存到临时目录的 `test_orders02_probe.py`。使用既有
`.venv` 执行后面的命令。代码显式不读取 `.env` 中的密钥，阻止网络连接，知识使用
临时场景的 documents 模式。不会导入两个参考项目，也不会改写冻结结果。

```python
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "tests"))
from conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall
from acs.chat_service import ChatService
from acs.config import Settings
from acs.cs_tools import ToolRegistry
from acs.database import BusinessDB
from acs.scenarios import build_scenario, scenario_settings
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore


@pytest.fixture
def local(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in this probe")
    monkeypatch.setattr(socket.socket, "connect", denied)
    root = tmp_path / "scenario"
    build_scenario(root)
    settings = scenario_settings(
        Settings(_env_file=None, llm_api_key="", embedding_api_key=""), root
    )
    return settings, SessionStore(settings.db_path)


def test_orders02_required_state_after_replaying_recorded_omission(local):
    settings, store = local
    rows = ROOT / "results/iteration_v2_recovery/local_after/raw.jsonl"
    with rows.open() as handle:
        row = next(r for r in map(json.loads, handle) if r["id"] == "orders-02")
    responses = [ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("order", "get_order", '{"order_id":"S20017"}')
    ])] + [ScriptedResponse(text=e["result"]["answer"]) for e in row["events"]]
    fake = ScriptedLLM(responses)
    agent = CustomerServiceAgent(
        settings=settings, llm=fake, registry=ToolRegistry(settings)
    )
    service = ChatService(settings, store)
    sid = None
    for event in row["events"]:
        update = list(service.run_turn(
            event["query"], "USER001", sid, agent=agent
        ))[-1]
        sid = update.session_id
        assert update.result.stop_reason == "answered"
    assert BusinessDB(settings).get_order("S20017", "USER001")["found"]
    task = SessionStore(settings.db_path).get_return_task(sid, "USER001")["task"]
    assert task is not None, "Required partial draft is absent"
    assert task["order_id"] == "S20017" and task["item_id"] is None


def test_existing_store_accepts_an_order_only_draft(local):
    settings, store = local
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    view = store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"})
    assert view["task"]["order_id"] == "S20017"
    assert view["task"]["item_id"] is None
    assert view["task"]["sources"]["order_id"]["user_text"] == "我想退S20017。"


def test_reading_an_order_does_not_itself_authorize_a_draft(local):
    settings, store = local
    fake = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("order", "get_order", '{"order_id":"S20017"}')
        ]), ScriptedResponse(text="已查询订单，本次只查询。")
    ])
    agent = CustomerServiceAgent(
        settings=settings, llm=fake, registry=ToolRegistry(settings)
    )
    update = list(ChatService(settings, store).run_turn(
        "只查询S20017，不办理退货。", "USER001", agent=agent
    ))[-1]
    assert store.get_return_task(update.session_id, "USER001")["task"] is None
```

```bash
# 将实际临时文件路径替换到命令中，不要放进冻结结果目录。
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest /tmp/你的临时目录/test_orders02_probe.py -q
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python -m pytest tests/test_task_memory.py tests/test_chat_service.py -q
```

在审查基线上第一条命令预期退出码 1，结果 `1 failed, 2 passed`；第二条预期全部通过。
重放固定模型决策可以稳定证明运行时遗漏，不能模拟真实模型响应分布或计算在线成功率。

## 附录 B：参考源码定位

以下链接均固定到本次阅读的提交。表中的能力与限制均依据实际代码，不依据 README 宣传。

[O1]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/app.py
[O2]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/ontology_catalog.py
[O3]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/grounded_response.py#L28-L77
[O4]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/qa_workflows.py#L119-L123
[O5]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/action_gateway.py
[O6]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/benchmark/agents/red_team.py
[O7]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/benchmark/orchestrator/runner.py
[O8]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/benchmark/agents/blue_team.py#L6-L20
[O9]: https://github.com/jiangenjing/anker-OntoCare/blob/32ab8fa405cacc680db434ed837d63bf3604d6fd/OntoCare/benchmark/agents/judge.py
[H1]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/conversation/interpret.py#L66-L144
[H2]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/conversation/reduce.py#L148-L342
[H3]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/conversation/business.py#L140-L264
[H4]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/conversation/tasks.py
[H5]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/actions/review.py#L148-L256
[H6]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/actions/gate.py#L43-L90
[H7]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/actions/executor.py
[H8]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/sessions/runtime.py
[H9]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/sessions/graph.py#L1105-L1248
[H10]: https://github.com/xiaokekehaha/anker-haike/blob/adb90ba405c18519882834ce57122c234db5c7cf/orchestrator/app/actions/store.py

