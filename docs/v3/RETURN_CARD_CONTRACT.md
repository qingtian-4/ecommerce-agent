# M7 退货确认卡片 REST/SSE 合同

本轮从已完成的 M7 `27eec1b` 继续，分支 `feat/v3-return-card-contract`。M6 基线 `2d20dbf` 为祖先；不从 M6 重新搭建服务。卡片复用 WebEngine、既有 jobs/events、FormalReturnPoc、FormalConfirmation、ReturnService 和 read_submission_receipt，不新增业务状态表、提交 API 或事务框架。

## 逐项审查

|类别|项目|处理|
|---|---|---|
|已完整实现|FastAPI、同源页面、固定合成身份、Cookie/CSRF、所有权检查|保留既有实现与回归|
|已完整实现|持久 SSE 序号、Last-Event-ID、订阅与后台任务分离|保留协议；补浏览器历史分页恢复|
|已完整实现|M6 原子申请/确认/审计/幂等回执及独立 SQL 查询|直接调用，不复制业务规则|
|已实现但有缺陷|网络异常后旧确认按钮重新启用；刷新只依赖旧游标|未核实 HTTP 标识跨刷新保留，UNKNOWN 禁止自动重发；先补读事件再续订|
|已实现但有缺陷|重复打开同预览；原 key 受到新状态前置检查阻断|同 key 返回原执行；新 key 不允许时409，不能新增等待|
|已实现但有缺陷|历史拒绝挡住新预览，运行中/中断确认被误投影为可接受|限定当前预览的动作，保留历史终态供断线对账；未完成应用操作只读|
|尚未实现|统一五状态 DTO、按钮 allowed_actions、失效/未知体验|本轮补齐，仍使用原确认接口|

## 只读 DTO

`GET /return-card` 绑定当前浏览器会话；`GET /api/sessions/{sid}/return-card` 为同一投影的显式会话路由。两者不打开 Graph、不生成等待、不消费事件、不调模型、不派发任务。业务库/权限无法核实时拒绝访问或返回503，不能建空库或猜测业务失败。

```json
{
  "state": "READY|REJECTED|EXPIRED|UNKNOWN|PENDING_REVIEW",
  "status": "同 state（兼容字段）",
  "allowed_actions": ["open", "accept", "reject", "cancel", "refresh"],
  "reason": "稳定原因码",
  "preview": {"preview_id": "…", "expires_at": "带时区ISO时间", "content": "白名单字段"},
  "confirmation": {"wait_id": "…", "status": "…", "preview_id": "…"},
  "request": {"request_id": "…", "status": "pending_review"},
  "authoritative": true
}
```

所有嵌套字符串复用既有递归清洗。`confirmation.is_current=false` 表示历史终态仍可核对丢失的 HTTP 回复，但它不拥有新预览的动作。只有完整申请、确认、审计及幂等 SQL 回执全部一致时，`request` 与 `authoritative=true` 才表示待审核；Graph、模型回答或 SSE 事件不能替代这些记录。

|状态|SQL/请求事实|allowed_actions|
|---|---|---|
|READY|有效新预览、尚无当前等待|open / refresh|
|READY|有效预览与当前等待绑定一致|accept / reject / cancel / refresh|
|REJECTED|当前确认已拒绝或取消，未创建申请|refresh|
|EXPIRED|没有有效预览、已过期或已失效终态|refresh|
|EXPIRED|旧 waiting 因更正或过期失效|cancel / refresh；关闭旧等待不创建申请|
|UNKNOWN|应用操作运行中、中断、已发出但无完整回执，或数据库暂不可核实|refresh；禁止自动重发 POST|
|PENDING_REVIEW|完整权威 SQL 回执验证通过|refresh|

更正后关闭旧等待，再为新预览打开新等待；不得将旧等待的确认授权移到新预览。未知请求不得自动关闭等待或生成新的确认事件。

## 原接口继续复用

- `POST /api/sessions/{sid}/confirmation` 打开等待；`POST .../decision` 接受/拒绝/取消，正文仍为原 request_key、wait_id、decision。
- request_key 是持久 HTTP 幂等键。原 key/原 payload 返回原 execution_id；不同 payload 拒绝。同一等待不接受新的并行确认，UNKNOWN 的新确认请求返回409。已核实成功的重复接受由 M6 返回原申请，不能再写一份。
- `GET .../receipts/{wait_id}` 保持独立、只读，不打开 Graph。明确的应用恢复接口仍保留原 M7/M6 语义，但 UNKNOWN 卡片不自动调用它。
- Cookie、同源 Origin、CSRF、浏览器会话绑定及业务 owner 校验继续生效。请求体不接受 user_id、数据库路径、thread_id 或任意业务 event_id。

浏览器在派发一次决定前，仅保存 `{wait_id,request_key}` 这项“尚未收到结果的 HTTP 尝试”标识，不存业务成功状态或身份权限。断线/刷新时先只读核对原 SQL 回执；没有已核实终态就保持 UNKNOWN，不能换 key 自动重发。该标识不构成第二套业务状态，也不能越过 M6 权限与事务。

## SSE

沿用原 `{id,execution_id,type,data}` 和事件白名单。每条事件先持久化，订阅仅补读。初次加载分页补读历史，再从最后序号续订；连接重建和 Last-Event-ID 不能执行模型、工具、确认或提交。只显示真实节点/工具进度，不伪造 Token 流，不公开 CoT、原始 SDK 响应、凭证或未脱敏业务数据。

## 启动及边界

```bash
cd /tmp/ecommerce-v3-episode-pilot-20261023
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/v3_web_demo.py --state-dir /tmp/v3-return-card-demo --init
# 浏览器 http://127.0.0.1:8767；同目录重启去掉 --init。
```

默认固定模型，Linux/WSL、127.0.0.1、单 Worker、全新 M6 合成 formal Fixture。原草稿实验的禁止正式写入 Trigger 不变。没有生产身份认证、真实平台售后、审核、退款或换货能力；默认 Gradio/Runtime、main、PR #4 不替换。
