# 下一阶段：本地退货链路的最小迁移计划

这是一份后续实施计划，本轮没有迁移本地退货、替换默认 Runtime 或启用业务写开关。

**当前 No-Go：暂缓开始本地退货迁移。** 真实 Pilot 的候选版本在任务 33 因协议守卫停止；先补足被拒绝模型输出的安全留存，并对齐新旧响应接受条件。离线配对验收通过后，再另行冻结真实 Development Pilot，不覆盖本次失败或把它重跑成成功。以下退货计划在该阻塞关闭后执行。

## 范围

下一轮只做隔离数据库中的草稿、预览与显式确认接续。商品、订单、物流、RAG 和 `ToolRegistry` 的权限规则继续复用。先比较相同固定决策的状态与回执，再做另行授权的少量真实模型请求；不能依据官方 Retail 的 DB 分数证明本地确认安全。

| 现有模块 | 最小改动 | 保留的权威职责 |
| --- | --- | --- |
| `src/acs/chat_service.py:ChatService.run_turn` | 增加候选 Runtime 的显式实验入口，映射 turn ID、取消和事件 | 身份由应用注入，默认 Loop 不变 |
| `src/acs/langgraph_poc.py` | 从只读实验扩展草稿和预览的原生消息路径 | 工具通过 Registry，不给模型 SQLite |
| `src/acs/sessions.py:SessionStore` | 核对 conversation turn 与 Graph thread 的映射；从 DB 重读草稿 revision | 用户归属、任务状态、revision 与恢复记录 |
| `src/acs/returns.py:ReturnService.preview` | Graph 保存 preview ID、revision、policy hash、有效期引用 | 预览内容来自业务事实；不是已提交申请 |
| `src/acs/returns.py:ReturnService.confirm_and_submit` | 显式确认边界接续；以 preview ID 对账 | 仍只由应用确认触发，不能成为模型 Tool；幂等、过期、归属和内容一致性校验保留 |
| `src/acs/cs_tools.py` | 仅开放草稿和预览的既有受限 Tool | 不扩大模型提交或审核权限 |
| `src/acs/return_state_check.py` | 审计既有精确句式后置规则的作用，先做两版本行为记录 | 不在这轮复制新的句式补丁 |
| UI 与 Trace | 展示请求、确认待办、真实提交回执及未知结果 | SSE/Gradio 事件不冒充 DB 成功 |

## 确认与真实写入的验收门槛

1. 初始阶段只在独立业务 DB 副本生成草稿和预览，Graph Checkpoint 使用另一文件。核对双方旧数据不受影响。
2. Interrupt 只能保存待确认的引用。应用提交 `session_id/user_id/preview_id`，ReturnService 在同一事务里重新验证归属、revision、政策、有效期及业务事实。
3. 用户插问后可继续待办；用户更正订单、SKU、数量、原因，或草稿取消、政策变化、预览过期时，旧确认必须失效并重新生成预览。
4. 分别在确认前、DB commit 前、commit 后但 Graph 更新前退出。以 preview ID 查询真实申请，已成功则复用回执，未知则阻止自动重试，不把 checkpoint 当提交成功证明。
5. 并发重复确认只能生成一条申请，跨用户预览不可提交。失败写入后的 Graph、业务 revision 和 UI 状态必须可解释。
6. 固定输入下比较旧链路与候选链路的任务状态、确认事件、真实写次数和最终 DB；再运行现有退货、会话恢复、权限和确认测试。

Go 条件是以上离线链路通过且所有未知写窗口关闭。真实 Pilot 的小样本只提供模型连通性与运行成本证据，不取代该验收。完整审核、退款、换货、生产 FastAPI 或前端重写不在下一轮最小范围内。
