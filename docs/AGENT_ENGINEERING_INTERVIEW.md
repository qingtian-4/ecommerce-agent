# 电商客服 Agent 面试技术说明

日期：2026-10-23，审计基点f987474；每个回答以当前源码为准，不将计划说成实现。
数字来自[Scorecard](EVALUATION_SCORECARD.md)，版本/开关见[架构审计](CODE_ARCHITECTURE_AUDIT.md)。

## 两分钟讲解

我做的是可观测的电商客服Agent：模型负责选择查询和草稿工具，ChatService负责会话与运行边界，
SQLite和ReturnService负责事实、权限、确认、事务与幂等。模型可以整理退货草稿，正式申请要页面确认，
管理员在独立入口审核；没有实际退款或实时人工接管。

Runtime是自定义Function Calling循环。模型文本、真正的ToolCall、执行结果和真实业务状态分开处理。
上下文按完整用户轮裁剪，数据库持久化会话和业务State；这不等于通用长期记忆。
RAG已有BM25、向量和混合检索，演示常用整篇版本化文档，不能把整篇文档说成向量检索。

我最关注失败如何定位：订单读到了却没存草稿、跨订单SKU串用、无真实写回执却说完成。
受限保护可以确定性呈现业务回执和事实，但也可能吞掉咨询或延迟首答，所以必须同条件对照。
流式是SDK接收模型块、Python生成器发更新、Gradio处理页面SSE；自研部分是流程和保护边界。

评价分开做：历史τ684段已完成；最新正式三臂119/120评分，剩一段预算停止，同39题V1/V2结果相同。
独立8题真实V2/V2.1为4/8对3/8，没有证明新版本更强。项目价值是可复现、可解释和诚实测量，
不是宣称生产可靠或靠增加框架提高成功率。

## 五分钟讲解结构

| 时间 | 内容 | 可现场打开的证据 |
| --- | --- | --- |
| 0–1分钟 | 订单查询→草稿→预览→页面确认→审核→查询；强调模型提交权限边界 | ReturnService.confirm_and_submit、Demo |
| 1–2分钟 | Loop/ChatService/Registry/SQLite职责；短期Context与持久State | service_agent._run_turn、chat_service.run_turn、sessions.update_return_task |
| 2–3分钟 | 查得到不等于办成；回执核验与混合咨询为何分开 | task39、write_evidence.guarded_answer、consultation_reply |
| 3–4分钟 | 恢复先核对业务，再谈继续；重试不重放未知写；SDK/Gradio流式分工 | recovery.reconcile_update、SessionStore.recover_interrupted、LLMClient.chat_stream |
| 4–5分钟 | 分组Scorecard，真实负结果、成本/首答代价、下一步最小缺口 | EVALUATION_SCORECARD、D01/D02、RELIABILITY_GAP_PRIORITIES |

## 18个追问与源码回答

### 1. 为什么采用自定义Agent Loop？

当前业务需要把每次模型请求、ToolCall、预算、真实写回执和可见输出放在一个可以逐步检查的循环里。
[CustomerServiceAgent._run_turn](../src/acs/service_agent.py#L243)显式执行“模型→工具→回填→下一次决策”。
它使失败回放和局部保护直接对应代码，但也要求自己维护消息配对、异常、重试和停止条件。
没有证据证明自定义Loop天然优于LangGraph；保留现有实现是为了最小改动和清楚的实验变量。

### 2. Loop、ChatService和业务服务有什么区别？

Loop管模型/工具决策；[ChatService.run_turn](../src/acs/chat_service.py#L55)管用户会话、上下文、
运行预算和最终持久化；[ReturnService](../src/acs/returns.py#L44)管预览、正式提交、审核和业务事务。
UI只消费TurnUpdate并处理明确按钮确认。外部[ACSAgent](../scripts/tau_acs_agent.py#L227)接官方环境，
不经过本店ChatService；所以官方τ结果不能证明本地P0、页面确认或恢复卡片全部正确。

### 3. 项目的Memory到底是什么？

[SessionStore.load](../src/acs/sessions.py#L182)恢复SQLite会话；ChatService重建当次模型上下文；
return_tasks是业务State，支持部分字段。当前没有跨会话语义长期记忆、偏好学习或自动摘要记忆。
[颜色限制](../src/acs/order_reliability.py#L15)从完整归属用户原话中有限投影，先于模型裁剪；
不能因为它能跨轮保持白色就宣称通用Memory。D01没存草稿时工具数据已经存在，根因是决策遗漏。

### 4. return_tasks.sources是不是事实账本？

不是。[update_return_task](../src/acs/sessions.py#L379)保存字段的原话、turn_id和source类型，
描述模型抽取的用户声明。它可用于更正和核对本轮变化，但不证明符合退货期限、非人为损坏或材料真实。
资格仍由SQL事实、政策、材料和预览/提交时复核确定。sources没有被扩展成通用Fact Ledger。

### 5. Function Calling如何落地并防越权？

[LLMClient.chat_stream](../src/acs/llm.py#L195)按index拼接碎片ToolCall，完整后Loop交给
[ToolRegistry.run](../src/acs/cs_tools.py#L346)解析JSON、核对注册工具/参数，应用提供current_user_id，
不能由模型传入的user_id替代。[Database.get_order](../src/acs/database.py#L133)再次按归属检查。
工具schema帮助模型选择，不是安全边界；模型输出DSML文字不是真执行，真正副作用必须有回执。

### 6. RAG做了哪些，哪些只是依赖集成？

[KnowledgeBase.chunk_text/build/load](../src/acs/rag.py)维护分块、元数据、embedding及索引指纹；
[retrieve](../src/acs/rag.py#L353)有BM25、Dense和RRF，rerank/select_evidence做可选重排和证据选择。
自定义的是组织和评测接入，向量模型、FAISS和重排服务是集成组件。
知识模式documents调用[get_knowledge_documents](../src/acs/cs_tools.py#L560)取整篇原文；
full_context直接放完整文档；两者不应包装成向量RAG效果。

### 7. 检索会增加多少模型调用？

不能笼统说0。[KnowledgeBase._verify_evidence](../src/acs/rag.py#L417)在启用标定/对应分数路径时
会调用小JSON判断，dense查询要embedding，可选reranker也可能是API；文档模式不需要embedding查询。
所有链路要区分Agent、embedding、重排、证据核验和评测裁判。若没有记录某类usage，成本标不完整。
这是已有RAG机制，不是新增的通用Reviewer Agent。

### 8. Router是不是另一个Agent？

不是。[route_request](../src/acs/routing.py#L17)是有限规则，识别本地业务词，对混合请求取能力并集，
不确定回退可用工具，不新增模型。V2.1修复了范围交集为空却高置信继续的问题；
回退完整“可用工具”仍须Registry/业务权限检查，不是提权。
英文τ正常任务大多fallback，原V2两次空范围还导致task39失败，不能宣传已在外部验证工具裁剪收益。

### 9. 上下文为什么按字符裁剪？

[input_chars/bounded_context/model_context](../src/acs/context.py)提供无需额外模型的可复现上限：
保留必要system/草稿和当前完整工具轮，旧历史按完整用户轮丢弃连续前缀，避免孤立tool消息。
当前必需内容本身超限就停止，不硬截JSON。字符不等于Token，不能用减少1000字符直接宣称省1000Token。
旧约束仍可能被模型忽略；保留Context和约束执行是两个问题。

### 10. SSE与流式输出哪些是自己写的？

SDK负责解析模型服务流；自写[LLMClient.chat_stream/_async_chat](../src/acs/llm.py#L195)
拼接文本/工具参数、记usage，[iterate_request](../src/acs/async_transport.py#L19)桥接异步I/O和同步生成器。
[on_send](../app.py#L574)把TurnUpdate yield给Gradio，[事件绑定](../app.py#L1445)设置0.1秒更新节奏。
浏览器的队列事件流/StreamingResponse由Gradio routes.py提供，不是自研SSE协议或独立FastAPI业务API。
桥接Queue也不是持久任务队列，没有通用分布式背压/重连协议。

### 11. 为什么保护后首答变慢？

[Loop receipt_guard](../src/acs/service_agent.py#L258)先缓冲模型文字，最终核验后才显示；
ChatService对部分候选/颜色/数量/取消也做门控。它可避免先显示错误再纠正，但没有撤回已显示文字的能力。
真实8题可见首答p50为1101→3917ms；这比只报API首块或整段均值更能反映体验代价。
输出事实保护有限，默认仍关闭，不能宣称所有文本都有证明。

### 12. 重试和幂等为什么分开？

重试是再次发送请求；幂等是重复执行仍对应同一业务结果。
SDK [max_retries=0](../src/acs/llm.py#L97)，Runtime只在有限可重试错误且尚无文本片段、预算允许时重试模型。
[ReturnService.confirm_and_submit](../src/acs/returns.py#L250)按preview_id在事务中返回已有申请，
防止重复确认创建新单。即使重试前暂未向用户显示文字，只要Provider已产生文字，当前实现也保守禁止自动重试。

### 13. 429/503、部分流和写后响应丢失怎么处理？

[classify_failure/retry_delay](../src/acs/recovery.py#L44)区分限流、临时服务、超时、业务拒绝、权限等；
Loop只在before-content且预算足够时有限重试，不统一重试所有工具。
已写但响应丢失由[reconcile_update](../src/acs/recovery.py#L21)按字段、revision和当前turn来源比对；
未知时state_uncertain并停止，不因为失败回执就再写一次。部分流有中断提示/受保护输出缓冲，不能假装完整。

### 14. 崩溃恢复是不是自动从中间节点继续？

不是。[SessionStore.recover_interrupted](../src/acs/sessions.py#L323)在单服务启动时处理旧running，
标interrupted；开启Recovery时读取草稿/申请/审核实际状态，保留旧预览需再核查，不自动重放业务。
没有持久化任意LLM执行程序计数器，没有“每个节点exactly-once”。
外部评测的[tau_resume/RequestJournal](../scripts/tau_resume.py)是另一套实验恢复：校验父链、旧结果与预算，
从新环境重开未完成Episode，不是客服会话自动续写或继续原工具。

### 15. 如何限制死循环？

[RunBudget](../src/acs/runtime.py)管整轮时间和API请求，Loop另限steps/tools/retrieval。
Recovery开启时，[_run_turn的signature](../src/acs/service_agent.py#L453)检测连续相同工具+参数+稳定结果，
忽略queried_at，达到no_progress_limit停止。它不覆盖任意A→B→A循环，交替循环仍靠总预算退出。
不能说已经做了通用规划或进度证明，也不应在无证据时用更大步数掩盖循环。

### 16. 如何保证“完成”是真的？

[write_evidence.verify/guarded_answer](../src/acs/write_evidence.py#L30)将实际工具回执与对象/字段绑定，
最新作用域回执生成摘要；无证据的模型完成声明不放行。外部验证还需[OfficialRegistry](../scripts/tau_acs_agent.py#L29)
按官方schema和字段检查，不能套本店回执结构。
[consultation_reply](../src/acs/consultation.py#L15)另列可信政策/商品资料，避免摘要吞掉咨询；
片段即使真实也不标完整。仍没有任意自然语言“完成”的通用语义检测，不能给生产保证。

### 17. Evaluation怎样避免自我感动？

先冻结任务、版本、快照、开关和预算；完整保留失败和原答；状态检查与语义质量分开。
历史684段用官方reward；正式119/120不把缺失当成功或缩分母；11题与8题是真实已见任务单次配对。
24条假模型案例只验证已冻结契约，14条通过含安全拒绝，不是用户整题成功。
[Scorecard](EVALUATION_SCORECARD.md)保留未知费用、裁判usage和不同延迟单位，不给一个混合总分。

### 18. 能证明V2.1更好吗，下一步怎么做？

不能。正式原V2同39题与V1reward相同，独立本地8题V2/V2.1为4/8对3/8，少Token还可能来自漏调用。
八题首个请求payload均相同，单次随机输出差异不能证明稳定因果。
优先[复用P0补存](RELIABILITY_GAP_PRIORITIES.md)：把明确订单草稿请求和已核验本人订单绑定，
仅补order_id，不选商品/不提交；先冻结混合请求与否定、更正、冲突等回归，不先加框架或付费重复。

## 面试表述底线

可以说自己实现了Loop、状态/回执约束、应用接入和可复核评价；不能把SDK、Gradio、FAISS或官方τ说成自研。
可以讲一次性小样本改善，也要讲负结果和测量修正；不把“恢复状态”说成“自动恢复任意任务”，
不把“Trace可观察”说成暴露隐藏CoT，不把局部保护说成生产级可靠。
