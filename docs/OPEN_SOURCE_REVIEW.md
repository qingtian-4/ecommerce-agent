# 开源客服项目对照与采用范围

审查日期：2026-10-15。以下结论来自固定提交的实际源码；没有运行这些项目，也不将
README 的功能清单当成稳定性证明。外部实现用于验证方向，不据此宣称本项目达到生产级。

| 项目与固定版本 | 实际读到的机制 | 对本项目的取舍 |
| --- | --- | --- |
| [aojiaodeyudie/ecommerce_agent_cs](https://github.com/aojiaodeyudie/ecommerce_agent_cs/tree/0bf76f7877553bfcb935995d7c31ff6697f00545) | [`chat_service.py`](https://github.com/aojiaodeyudie/ecommerce_agent_cs/blob/0bf76f7877553bfcb935995d7c31ff6697f00545/ecommerce/chat_service.py) 统一会话入口、历史和槽位恢复；[`ConsumerChat.tsx`](https://github.com/aojiaodeyudie/ecommerce_agent_cs/blob/0bf76f7877553bfcb935995d7c31ff6697f00545/frontend/src/pages/ConsumerChat.tsx) 将消息滚动区与输入区分开，带逐条反馈；`TicketDesk.tsx` 有工单回复/处理入口 | 采用共享对话服务和固定输入区。工单创建不等于实时人工接入，不沿用其“已转接”兜底文案。未在固定版本根目录找到 LICENSE，只研究结构，不复制代码或样式 |
| [openai/openai-cs-agents-demo](https://github.com/openai/openai-cs-agents-demo/tree/bd7bfca0f5abf50529370814c3e7c88542011925) | [`page.tsx`](https://github.com/openai/openai-cs-agents-demo/blob/bd7bfca0f5abf50529370814c3e7c88542011925/ui/app/page.tsx) 分离聊天和 Agent 观察面板；[`conversation-context.tsx`](https://github.com/openai/openai-cs-agents-demo/blob/bd7bfca0f5abf50529370814c3e7c88542011925/ui/components/conversation-context.tsx) 展示业务上下文；航空工具中存在内存状态与演示返回值 | 采用可见业务上下文、按需展开工程观察。Agent 间 handoff 与真人接管分开；不为模仿示例换 SDK，不把内存工具当真实退款后端。MIT |
| [Chatwoot](https://github.com/chatwoot/chatwoot/tree/b8f9b13d5a0d5e55167cac4762c9b9b9064de5e1) | [`ConversationView.vue`](https://github.com/chatwoot/chatwoot/blob/b8f9b13d5a0d5e55167cac4762c9b9b9064de5e1/app/javascript/dashboard/routes/dashboard/conversation/ConversationView.vue) 将会话列表、消息区和业务侧栏拆开；`min-w-0` 和条件侧栏控制拥挤 | 借鉴工作台的信息结构，继续使用 Gradio。它是完整客服平台，不能据界面推断我们的 Agent 质量。仓库有分区许可，企业版部分不能按普通开源代码处理 |
| [Azure-Samples/contoso-chat](https://github.com/Azure-Samples/contoso-chat/tree/028921fe3209db3216075d34ced6c109de132533) | [`chat_request.py`](https://github.com/Azure-Samples/contoso-chat/blob/028921fe3209db3216075d34ced6c109de132533/src/api/contoso_chat/chat_request.py) 读取客户与商品数据；[`product.py`](https://github.com/Azure-Samples/contoso-chat/blob/028921fe3209db3216075d34ced6c109de132533/src/api/contoso_chat/product/product.py) 生成商品查询并调用文本/向量搜索 | 参考事实与检索分离。该版本 `chat_history` 参数没有传入模型输入，参数存在不代表记忆有效。仓库已归档，作为历史参考，不引入其 Azure 部署栈。MIT |

## 缺口与实施优先级

1. **可复现的数据覆盖**：原 4 商品不足以检验佩戴形式、颜色可用性和硬约束推荐。
   新增独立合成 v2 场景，不替换旧失败所用事实；价格、日期、SKU、知识版本可核对。
2. **运行边界**：UI 与评测共用轮次服务；保留完整历史，限制模型上下文、请求数、工具数
   与整轮耗时。持续流、超时、排队、重复点击和重启必须有失败证据，而不只看正常路径。
3. **可理解的工作台**：左侧历史，中间消息及固定输入，右侧本人订单与售后办理。
   工程观察单独展开，避免用户把工具 JSON 当作办理结果。原始凭证继续使用原文件预览。
4. **结果闭环仍需补充**：逐条反馈、失败分类、人工工单与接管协议、价保办理尚未交付。
   先做可查询的工单状态与真实操作入口，再允许客服说“已转接/已办理”。
5. **评测与部署仍需补充**：80 段本地多轮任务、固定 τ retail 子集及重复运行是下一交付；
   现有开发用例不能继续称未见留出。多实例、真实客户认证、渠道接入和支付退款均未实现。

暂不添加多 Agent、Graph 框架、Redis 或更多检索算法。现有小库已有 Dense/BM25/RRF/
reranker 对照；新增组件应先对应一种实际失败，再用相同冻结问题验证收益与消耗。
外部项目的功能数量、星标与界面成熟度都不代替本项目的事实正确率和状态变化检查。
