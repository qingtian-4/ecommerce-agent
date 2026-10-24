# M7 Web 服务与事件协议

基线 `2d20dbf`，独立分支 `feat/v3-web-service-demo`，复用原 V3 Worktree。不改变 app.py、默认 Loop、官方 τ Adapter 或 M6 业务规则。

## 生命周期

FastAPI lifespan 持有 `web-worker.lock`，仅一个进程；ThreadPoolExecutor(max_workers=1) 持有运行任务。每项任务进入 FormalReturnPoc 上下文，持有既有 worker.lock、同步 Graph、SqliteSaver 和 run_lock，结束后关闭 Graph/模型 HTTP 客户端。SSE 是独立异步订阅，断开不取消任务。服务优雅退出等待当前任务；硬退出由持久任务标记与 M6 SQL/Graph恢复协议处理。

| 状态 | 权威位置 | 作用 |
|---|---|---|
| 浏览器凭证/CSRF/会话映射 | web.sqlite browsers | Cookie 只存随机凭证，库中存凭证 hash；合成身份服务端固定 |
| 执行ID/HTTP幂等键/任务状态 | web.sqlite jobs | 同会话同request_key同payload只返回原执行；不同payload拒绝 |
| 可见事件序号 | web.sqlite events | AUTOINCREMENT、按session过滤，只能补读，不能触发业务 |
| 用户消息/草稿/预览/申请/确认/审计/幂等回执 | business.sqlite | SessionStore/ReturnService/M6提交桥负责；Graph不是成功证明 |
| 原生对话与等待位置 | graph.sqlite | create_agent / StateGraph / SqliteSaver，不自建模型工具循环 |
| 原始执行与请求取证 | 受限本地 .private-evidence、trace.jsonl | 不通过Web静态路由或公开事件发布 |

服务启动把遗留 queued/running 标为 interrupted 并写可见事件，不自动重放模型或确认。对话恢复显式调用现有 stream(None)；确认恢复显式调用现有 submission('resume')，先查权威 SQL。提交成功只在完整 SQL 回执核对后发布 submission_verified。终态事件与 jobs终态在同一个web事务写入，避免UI看到完成后仍被running锁住。

## REST 合同

默认地址 `http://127.0.0.1:8767`，拒绝其他Host、跨站请求及非同源Origin；不开放CORS。

| 方法 / URL | 内容与授权 |
|---|---|
| GET / | 获取同源页面，建立随机浏览器安全凭证；不建立业务会话 |
| GET /api/bootstrap | 已有Cookie→CSRF及当前浏览器session；不接受身份字段 |
| POST /api/sessions | Cookie+Origin+CSRF；为该浏览器创建/返回一个隔离业务session |
| GET /api/sessions/{sid} | Cookie和所有权；最新草稿/有效预览/最终聊天/订单/权威申请/确认状态/任务状态 |
| POST .../{sid}/messages | text+request_key；202 execution_id，后台同步Graph |
| POST .../{sid}/resume | request_key；显式恢复未完成对话 |
| POST .../{sid}/confirmation | request_key；打开最新预览的原生Interrupt |
| POST .../{sid}/decision | request_key+wait_id+accept/reject/cancel；wait必须属于浏览器会话 |
| POST .../{sid}/confirmation/{wait_id}/resume | 显式核对并恢复已保存的waiting/issued确认 |
| GET .../{sid}/receipts/{wait_id} | 不打开Graph，核对完整业务SQL回执；业务库不可用返回503，不能猜失败 |
| GET .../{sid}/events | SSE；after/Last-Event-ID补读；once=true用于有限批次读取 |

JSON契约Pydantic strict、extra=forbid；user_id、db_path、thread_id、业务event_id均无公开输入。request_key是HTTP任务幂等键，不能充当确认event_id。所有业务副作用POST必须通过Cookie、Origin、CSRF及session所有权；初次页面只bootstrap安全凭证。状态码：401未建凭证、403身份/CSRF/Host拒绝、409忙或幂等键冲突、422格式错误、503业务状态不可核实；错误不回显原始输入或SQLite异常。

## SSE 合同

每条数据 `{id,execution_id,type,data}`，SSE id与持久序号相同。字段严格白名单，不直接序列化 Graph 更新、模型结构或工具JSON。重连只读 events，不执行模型、工具或提交。

| type | 意义 |
|---|---|
| turn_started | 后台对话确已启动 |
| tool_requested | Graph模型节点已产生工具请求，尚不是业务成功 |
| tool_completed | 原生ToolMessage已返回，带success/error状态 |
| business_state_updated | 应查询当前SQL状态，不把历史快照当最新事实 |
| confirmation_required | 应用门确有持久等待 |
| submission_pending | 正在核对/消费应用决定，尚未声称成功 |
| submission_verified | 完整申请+确认+审计+幂等SQL回执已验证 |
| turn_completed | 任务终态与事件已原子持久化 |
| turn_failed | 执行未完成或结果未知，要求权威查询，不能理解为申请一定失败 |

提供节点/工具进度，不提供伪造Token流，不公开CoT。应用错误细节保留本地，公开事件仅固定原因。聊天文本经过同一敏感字段清洗，服务端合成姓名、电话及配置密钥不公开。页面用textContent渲染，不把模型文本作为HTML。

展示授权绑定：GET多次业务读取可能跨越另一标签页的更新；等待的preview_id与响应中显示的预览不同或无有效预览时，等待呈refresh_required，页面还独立比较ID后才启用接受。用户可显式核对恢复等待，不能把屏幕上另一份预览当授权。

离线固定模型的游标从最后原生HumanMessage之后的AI历史计算，不存在进程内游标权威；Tool Call ID以原生持久HumanMessage.id作前缀，重复同文本新Turn和原Turn恢复不会冲突。此逻辑只属于固定脚本演示，不改变真实模型接口或默认Runtime。


## 基线与依赖核验

远端M6分支仍为2d20dbf，main仍为f34981f；V3仅复用原Worktree，不新增或删除目录。主目录三个用户原文件hash与开始审计一致，保留其原未提交状态。环境为Python3.13.12、FastAPI0.142.2、Uvicorn0.54.0、LangChain1.4.7、LangGraph1.2.16、SQLite Checkpointer3.1.2、Pydantic2.13.5；本轮仅显式声明现有Web依赖并更新锁元数据，没有整体升级。源码、保护文件hash见results/web_demo_deepseek_20261024_run1/repository_audit.json。

Formal模型能力投影在真实冒烟后作最小修复：预览工具描述、新预览回执、请求级当前事实明确模型无提交工具，应用确认可创建隔离模拟pending_review。旧草稿Fixture的提示、权限与正式写入禁止规则保持。历史预览幂等回执必须从SQL原样恢复，不能被当前版本的新文案覆盖；最新预览有效性核验仍保留。没有复制ReturnService的业务规则。
