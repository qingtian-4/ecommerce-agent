# M7 退货确认卡片合同

本合同复用 `src/acs/web_service.py` 的 FastAPI、事件表、`FormalReturnPoc`、`FormalConfirmation` 和 `read_submission_receipt()`。它只投影状态，不新增业务事务、Graph 状态或提交接口。

## 审查结论

|范围|审查结果|
|---|---|
|已有 REST、CSRF、合成身份、会话所有权|已实现并保留；本轮没有复制接口|
|事件持久化、事件 ID、Last-Event-ID 补读|已实现；浏览器重连仅读事件|
|M6 原子确认、幂等、SQL 权威回执|已实现；卡片只读 `read_submission_receipt()`|
|统一五状态 DTO|本轮补齐 `GET /return-card` 与 `/api/sessions/{sid}/return-card`|
|按钮按服务端允许动作控制|本轮补齐；前端不自行推断可执行动作|
|重复打开同一预览|本轮补齐；已有等待返回409，终态预览返回409，不创建新等待|
|刷新/SSE断线恢复|本轮补齐卡片重投影；未知结果只允许刷新读取|

## DTO

```json
{
  "state": "READY|REJECTED|EXPIRED|UNKNOWN|PENDING_REVIEW",
  "status": "同 state",
  "allowed_actions": ["open", "accept", "reject", "cancel", "refresh"],
  "reason": "稳定原因码",
  "preview": {"preview_id": "…", "expires_at": "…", "content": "白名单字段"},
  "confirmation": {"wait_id": "…", "status": "…", "preview_id": "…"},
  "request": {"request_id": "…", "status": "pending_review"},
  "authoritative": true
}
```

`request` 只有完整 SQL 回执通过 `read_submission_receipt()` 时才出现；Graph checkpoint、最终聊天文本和 SSE 计划事件都不能单独生成 `PENDING_REVIEW`。

|状态|进入条件|允许动作|页面含义|
|---|---|---|---|
|`READY`|有效预览；无等待时可打开，已有等待时可接受/拒绝/取消|`open` 或 `accept/reject/cancel` 加 `refresh`|等待用户在应用按钮决定|
|`REJECTED`|确认已拒绝或取消|`refresh`|没有创建申请|
|`EXPIRED`|预览过期、更正/政策导致失效或不存在有效预览|`refresh`|重新生成预览后再打开|
|`UNKNOWN`|已发出应用事件但没有可核实 SQL 回执，或任务结果未知|`refresh`|禁止自动重发 POST；先读权威状态|
|`PENDING_REVIEW`|申请、确认、审计、幂等回执均通过 SQL 对账|`refresh`|隔离模拟申请已待审核，审核与退款仍禁止|

## HTTP / SSE

- `GET /return-card` 使用当前浏览器会话；`GET /api/sessions/{sid}/return-card` 是显式会话版本。两者只读。
- 打开等待仍是既有 `POST /api/sessions/{sid}/confirmation`；接受/拒绝/取消仍是既有 `POST .../decision`。所有 POST 继续要求 Cookie、同源 Origin、CSRF 和会话所有权。
- `POST confirmation` 在卡片不允许 `open` 时直接返回 `409`，避免异步创建重复等待。
- `GET .../events` 先读持久化事件表，再通过 SSE 发送；`Last-Event-ID` 或 `after` 只补读，断开不创建、撤销或重放业务操作。
- `submission_verified` 只在 SQL 回执完成后公开；`turn_failed` 的 `unknown` 只提示查询，不等于业务一定失败。

## 运行

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/v3_web_demo.py --state-dir /tmp/v3-return-card-demo --init
# 浏览器访问 http://127.0.0.1:8767；重启同一目录时去掉 --init。
```

默认使用固定合成模型和 M6 formal SQLite Fixture；不会连接生产数据库、执行审核、退款或真实平台办理。
