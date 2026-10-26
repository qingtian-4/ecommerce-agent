# V3 产品化客服界面验收

本轮从 `fix/v3-chat-presentation` 的 `b1e7f5576b805af21df5b9554f8422eff806605f` 开始，实际完整基线以
`results/shop_ui_20261026/source_manifest.json` 为准。在正常项目目录的独立分支
`feat/v3-shop-frontend` 实施，没有重新创建开发 Worktree。

## 实现与复用边界

主视觉使用用户原型的浅灰背景、白色工作区、橙色操作按钮、深色用户消息，以及固定输入区。
桌面左侧聊天、右侧当前关联订单/售后摘要，900px 及以下使用单列聊天和订单抽屉。
不把全部订单堆入右栏，也不自动猜选退货对象。消费者界面不展示隐藏推理。

| 交付 | 实际源码 | 业务依据 |
|---|---|---|
| 新页面与原生组件 | `frontend-v3/` | 独立 `/shop`，旧 `/` 和 Gradio 保留 |
| 最近3笔、分页、详情与物流 | `acs.shop_frontend` | 原 BusinessDB 本人归属检查、公开字段白名单 |
| 商品网格 | `frontend-v3/render.js` | 本轮实际工具回执，不信任模型自报结构 |
| 退货表单 | `frontend-v3/app.js` | 明确填写转为用户消息，经现有 Graph/ToolRegistry 执行 |
| 聊天内唯一确认卡片 | 同上 | 原 return-card DTO、confirmation/decision/receipt API |
| 回答反馈 | `acs.shop_frontend` | 原 WebEngine 同一 web.sqlite 的 ui_feedback，turn_id/CSRF/归属/唯一键 |
| 正文/工具进度 | 现有 SSE + 新页面 | 真实 answer_delta、工具与完成事件，非定时器模拟正文 |
| 离线运行入口 | `scripts/v3_shop_demo.py` | 正式提交专用合成 Fixture，真实 SDK MockTransport |

OpenChatShop 的源码、React/Ant Design 取舍与许可证见
[参考审查](SHOP_UI_REFERENCE_REVIEW.md)。本轮没有复制其 Agent、数据层或自动补发队列。

## 真实浏览器与 SQL 验收

执行 `scripts/eval_shop_frontend.py`，Chromium 启动真实 FastAPI 服务、原生 LangGraph、
全新合成 SQLite。模型供应商传输由 MockTransport 替代，**不属于真实 DeepSeek 质量实验**。
最终逐项结果、请求数、截图与几何尺寸在 `results/shop_ui_20261026/`。
最终 Chromium 47/47 项通过，28 次 SDK MockTransport 请求，新增付费请求0。
完整 Pytest 1144 passed、0 skipped（18项既有警告），Ruff 和密钥扫描另由 CI 复验。
本机执行时源码尚未提交，精确文件 SHA256 绑定在 source_manifest.json；CI 绑定最终提交 SHA。

验收包括：最近3笔/全部分页/物流与 SQL 一致、商品网格与商品表一致、普通咨询段落保留、
大型订单表格折叠、每个完成回答有反馈、反馈原因持久化、人工服务未接入提示；
320/390/900/901/1380px 无横向溢出、固定输入、移动抽屉及操作；
更正使旧确认失效、新预览、明确4xx不误报未知、接受时双击只发一次、HTTP回执丢失只读核对；
刷新、SSE断线/重连、服务重启不重放；拒绝/取消/过期不新增申请；跨会话和CSRF拒绝；
协议错误可见、失败流式草稿撤回，执行中旧 HTTP 快照不提前解锁发送。

正式成功不是根据聊天文案判断。完整业务 SQLite 中申请、确认、审计、幂等回执各1条，
申请为 `pending_review`，外键关联与消费次数可对照 `sql_reconciliation.json`。
Graph 镜像故意损坏后，只读业务回执和服务重启仍能展示同一申请。
这不等于损坏的 Graph 可继续推理或自动恢复写操作。

五种状态均通过真实 HTTP 和 SQLite 路径展示；UNKNOWN 由提交已发生但 HTTP 回执被丢弃、
业务查询临时不可用的故障注入产生。没有使用原型的假状态切换。明确4xx由HTTP故障注入验证；
实际权限拒绝另由不同浏览器和CSRF测试验证。数据库不可读时不显示成功、不重发提交。

## 复现与运行

见 [frontend-v3/README.md](../../frontend-v3/README.md)。离线页面：

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/v3_shop_demo.py --state-dir /tmp/v3-shop-demo-new --port 8768 --init
# 浏览器打开 http://127.0.0.1:8768/shop
# 重启去掉 --init，保留同一目录的业务数据、会话、Graph、事件和账本。
```

浏览器验收工具独立安装，不新增生产依赖；CI 同样执行完整 Chromium 矩阵，
只上传公开 JSON/截图，私有原始 SDK 请求、Graph 和 SQLite 不进入 Git/CI Artifact。

## 已修复缺陷及未覆盖风险

1. 新页面曾只使用 HTTP 快照判断执行锁，旧快照可能先于 SSE 终态到达。
   固定同一个旧快照、让供应商保持请求进行中，修复前浏览器断言失败，
   修复后保留 SSE 执行中锁，直到权威终态。红/绿结果一并保留。
2. 提交回执丢失后的数据库查询错误覆盖了原错误类别，核验成功后仍有旧警告。
   区分状态读取错误，仅在三份读取均成功且完整业务回执核实后清除该警告。
3. 移动订单抽屉内打开详情先关闭抽屉，避免叠加两个模态窗口。
4. 真正停止测试服务使 SSE 断开时，后台读取可能合理进入 UNKNOWN；
   原页面连接恢复后没有重新读取权威状态。增加连接打开时的只读对账，
   不重发模型/工具/确认请求。保留修复前浏览器失败记录，修复后验证申请编号不变。

类别示意图不是商品实拍。原始商品表没有图片字段，没有编造图片地址或库存。
人工坐席尚未接入，不能转接。最新当前售后卡片与历史原生消息一起恢复，未增加第二套业务状态。
大型表格只是默认折叠，原始/展示文本在服务端不修改；正常知识和商品咨询不靠全文替换处理。

本轮新付费请求为0、实际新增费用0，不报告新的模型效果提升。既有授权的手动真实模型入口
仍受原许可期限、现有费用账本与单轮保护限制，本轮不创建新许可、不延长授权。
仅允许 Linux/WSL、本机127.0.0.1、单Worker和合成数据，不能开放公网或办理真实退款。
基线曾出现的确认恢复CI偶发失败记录继续保留，不能将本轮前端竞态修复当成该业务问题已解决。

结论：通过验收的产品化前端可用于本机隔离 Demo；生产及人工坐席接入 No-Go。
