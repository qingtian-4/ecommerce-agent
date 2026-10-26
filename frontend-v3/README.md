# V3 产品化客服前端

独立、无构建依赖的原生 ES Modules 前端。主视觉来自用户提供的
`docs/V3_customer_service_interactive.html` 和截图；原型只是设计资料，它的假回复、
假提交、状态切换和内存反馈没有作为业务实现使用。用户原型文件不由本轮提交或覆盖。

组件与数据：

- `app.js`：SSE/HTTP 生命周期、单轮互斥、消息/订单/反馈和现有确认接口绑定。
- `render.js`：DOM-only Markdown、订单卡片、商品网格、物流时间线和明确标注的类别示意图。
- `style.css`：固定输入区、桌面双栏、移动端原生对话框抽屉、减少动态效果。
- `acs.shop_frontend`：同一 FastAPI/WebEngine 上的只读投影与 SQLite 反馈，不是新 Agent。

不使用 React/Ant Design 的原因：目前可以直接复用已验证的同源身份、SSE 和确认合同，
不必增加 Node 构建/组件依赖与另一套消息状态。OpenChatShop 仅作为交互与布局参考。
如果后续继续增加坐席工作台，应单独评估 React，而不是导入其整个后端。

## 离线启动

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/v3_shop_demo.py \
  --state-dir /tmp/v3-shop-demo-new --port 8768 --init
```

访问 `http://127.0.0.1:8768/shop`。这是**离线 SDK 脚本验收**，使用真实 LangChain SDK、
LangGraph 和业务 SQLite，HTTP 传输为 MockTransport，不代表真实模型效果，不会收费。
只支持明确的离线场景。旧 `/` 页面仍可访问，默认 Gradio 入口完全不变。
重启时去掉 `--init`，不能覆盖已有 Fixture；账本和反馈不重置。

真实模型模式为显式 `--live`，另需既有有效的 `--env-file`、`--authorization`、
`--price-review`，继续使用原有费用/取证机制。启动服务不会自动发模型请求。
本轮不生成付费许可、不启动新付费实验，也不绕过既有授权期限。

## 交互边界

- 最近3笔订单在聊天中展示，另外两笔可展开操作，全部订单通过只读分页查看。
- 卡片中的详情和物流按钮执行有归属检查的只读 REST，区别于模型 Tool Calling。
- 申请售后表单将用户明确填写的信息转为本轮消息，通过原生 Agent/ToolRegistry 保存草稿和预览，
  不直接写业务 SQLite。用户仍可在普通聊天中插问或更正。
- 全页只有一个当前退货确认卡片，嵌入聊天；右侧/手机抽屉仅显示当前关联订单和任务摘要。
- 确认、拒绝、取消仍使用原 `/confirmation`、`/decision`；应用事件仍由服务端发放。
- 接受按钮不从模型文字推断成功。只有完整权威回执才能显示模拟待审核及申请编号。
- HTTP 回执丢失时保留本地未核实尝试，只读查询，不自动重发 POST。数据库不可读时不显示成功。
- 反馈绑定服务端原生 turn_id 和已核验回答，写入同一个 `web.sqlite` 的 `ui_feedback`；
  没用必须选择原因。显示“已保存”来自服务端确认，刷新和重启后仍保留。
- 转人工仅明确展示“尚未接入”，没有伪造接通、排队、坐席或工单。
- 类别图标是示意图，非实拍；原数据没有图片、库存等字段，不编造这些属性。
- 大型订单表格默认折叠为原文，正常咨询段落继续显示，原始/展示回答存储不改。

## 增量 REST

所有接口继续使用既有 HttpOnly Cookie、合成身份、会话归属和同源边界。
模型不能传入 user_id、数据库路径或 Graph thread_id。

| 方法 | 路径（共同前缀 `/api/sessions/{sid}`） | 合同 |
|---|---|---|
| GET | `/orders?page=1&page_size=3` | 默认3笔，page>=1，page_size<=10，本人订单按日期倒序 |
| GET | `/orders/{oid}` | 本人订单公开字段，不返回姓名或地址 |
| GET | `/orders/{oid}/logistics` | 先核验归属，返回白名单物流字段 |
| GET | `/messages` | 现有已核验原文、稳定turn_id、同轮真实工具卡片、反馈 |
| POST | `/feedback` | CSRF + session归属 + 已核验回答；vote=helpful/unhelpful，reason受枚举约束 |

反馈 request_key 在 SQLite 中有唯一约束。相同键/内容返回已有回执，冲突内容拒绝。
允许修改意见，使用新键追加记录，读取最新意见；不会改变模型原文、业务状态或评分。
原消息、确认、结果查询、SSE 接口不替换。GET 只读展示接口不启动模型或业务提交。

## 验收

```bash
uv run pytest tests/test_shop_frontend.py
uv venv /tmp/shop-browser
uv pip install --python /tmp/shop-browser/bin/python playwright==1.64.1
/tmp/shop-browser/bin/python -m playwright install chromium
/tmp/shop-browser/bin/python scripts/eval_shop_frontend.py \
  --out /tmp/shop-ui-acceptance-new --port 8781
```

浏览器工具独立安装 Playwright，不混入生产依赖。结果及失败记录见
`docs/v3/SHOP_UI_ACCEPTANCE.md`。仅限 Linux/WSL、本机127.0.0.1、单Worker和合成数据。
