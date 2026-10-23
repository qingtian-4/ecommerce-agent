# 旧模块到 V3 的迁移映射

路径相对本实验 worktree。标注“独立分支”的文件在 main 基线不存在，链接固定到被审查版本。
“已实现”仅指独立 PoC，不表示默认 UI 或本店完整业务已经改用 Graph。

| 旧模块与职责 | V3 节点/状态/服务/接口 | 本轮实装与后续处理 |
| --- | --- | --- |
| service_agent.CustomerServiceAgent._run_turn | create_agent 原生 model/tools 条件循环；native AgentState.messages | 只读已实现；旧类不改、不移除 |
| llm.LLMClient、async_transport | ChatOpenAI 消息、工具绑定与原生 stream；callback usage | PoC 已实现；旧完整 turn wall deadline、上下文裁剪需另做迁移，不假定 socket timeout 等价 |
| cs_tools.ToolSpec.schema | StructuredTool + Pydantic ProductArgs/OrderArgs/EmptyArgs | 六个只读工具已实现；schema 描述仍来自现有 Registry |
| cs_tools.ToolRegistry.run | ToolNode 中的受限执行适配；应用注入 current_user_id | 已复用；SQL 归属权限仍由 BusinessDB 强制 |
| database.BusinessDB | 权威只读业务服务 | 不改；绝不把连接对象交给模型或序列化进 Graph |
| rag.KnowledgeBase、retrieval、knowledge_context | 知识工具服务；Graph 只保存引用和来源版本 | 保留实现与索引；本轮未接 RAG 工具到 PoC，不新增 embedding 格式 |
| chat_service.ChatService.run_turn | 后续应用协调服务：身份、输入幂等、每会话串行、事件投影与 Graph 调用 | PoC 是单 worker CLI，没有重写现有 ChatService |
| sessions.SessionStore 会话消息 | 原生 Checkpointer 保存执行状态；旧会话只读导入边界 | PoC 新会话已实现；未双写旧 conversation_turns，不支持静默导入 |
| SessionStore.begin_turn/finish_turn | 服务入口受理/终态索引；Graph completion 与业务 receipt 分别记录 | CLI 先落 checkpoint、再发 run_completed；HTTP 接口未实现 |
| SessionStore.return_tasks/revision/sources | 业务草稿继续权威；Graph tasks/entity_bindings 只存引用/提案 | 保留现有业务实现；Graph 任务账本为设计 |
| returns.ReturnService.preview | 确定性 validate/preview 节点，生成已绑定 revision 与政策的预览 | 未迁移；不能让模型持有“资格已通过”的永久布尔值 |
| ReturnService.confirm_and_submit | 独立用户确认接口 → Interrupt resume 校验 → 事务提交服务 | 原服务保留；Graph 桥未实现。preview_id、revision、TTL、policy_hash、归属与剩余数量复核不删除 |
| ReturnService.review / evidence | 认证管理员接口与材料服务 | 保留；模型没有审核、退款和材料核验权 |
| [routing.route_request](https://github.com/qingtian-4/ecommerce-agent/blob/f6c3387c81e71022b96d553bd080f5751198b6f9/src/acs/routing.py)（独立分支） | 原生工具集合 + 后续确定性能力/授权路由；任务提案可含多个任务 | 本轮只读固定名单，无独立意图路由模型；不照搬词法匹配 |
| [recovery.classify_failure/reconcile_update](https://github.com/qingtian-4/ecommerce-agent/blob/c5130610a32eef12c4305519fdbf2c2c448189dc/src/acs/recovery.py)（独立分支） | 原生 checkpoint 恢复节点；业务异常分类、未知写回执核对保留服务职责 | 只读崩溃恢复已实现；不自动重放写操作 |
| [write_evidence](https://github.com/qingtian-4/ecommerce-agent/blob/c5130610a32eef12c4305519fdbf2c2c448189dc/src/acs/write_evidence.py)（独立分支） | 后续业务回执 schema、字段/主体绑定；写后结果投影 | 契约可借用，未接线，不声称 native checkpoint 替代回执核对 |
| [consultation](https://github.com/qingtian-4/ecommerce-agent/blob/c12bb626d50e454acc387de04ed6c1e0e73190cc/src/acs/consultation.py)（独立分支） | 后续逐 task 输出与来源映射，业务回执不覆盖其他已回答任务 | 未迁移；本轮没有“混合咨询完整性已解决”的结论 |
| return_state_check / order_candidates / order_reliability | 后续通用 Task/Fact 契约与确定性业务投影；旧保护继续服务旧入口 | PoC 不复刻特殊表达修补，不移除旧保护 |
| runtime.RunBudget、progress、obs.Trace | 原生调用限额 + 本轮 PocBudget/AuditEvent；后续统一事件 schema/全链路费用 | PoC 请求费用已受限；未覆盖旧 turn deadline、HTTP 排队或付费用户模拟器 |
| app.on_send / TurnUpdate / AgentResult | 后续 Gradio 消费统一事件并投影为原组件；FastAPI 输出同一事件 | 原 Demo 不变；本轮 CLI JSONL 不是已部署 SSE |
| [tau_acs_agent.ACSAgent/OfficialRegistry](https://github.com/qingtian-4/ecommerce-agent/blob/f780f171dfaad6cfa75e7eb3a547232e3a5fe30a/scripts/tau_acs_agent.py)（独立分支） | LangGraph HalfDuplex Adapter；工具意图交还官方执行器，ToolMessage 按 id 注入 | 官方协议和返回校验可以复用；chat_stream/agent.messages/trace transport 必须重写，见 BENCHMARK_PROTOCOL |

消息转换的最低要求是 native HumanMessage/AIMessage/ToolMessage 与旧字典的 role、content、
tool_call_id、tool_calls.arguments 保持一一对应，畸形工具请求不能当普通最终答案。
Registry 的 error 映射为 ToolException，再由 StructuredTool 生成 status=error；业务未找到结果
仍是合法工具响应，不与执行故障混为一类。本轮错误参数、DB 异常及未授权工具均有反例测试。

旧 UI 的“草稿”“申请已提交”“审核通过”有不同权威来源。后续界面恢复从业务接口读取草稿和申请，
从 Graph 读取待执行/待确认状态，从事件日志重建可见进度，不能仅靠模型历史推导这些状态。
