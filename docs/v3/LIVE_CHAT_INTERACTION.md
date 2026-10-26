# 自由对话、输入键盘与对话内 SSE

## 已确认的原因

2026-10-26 的 8767 服务由 `scripts/v3_web_demo.py` 启动，缺少 `--deepseek`，
实际为免费固定脚本。页面截图不能证明真实模型已接入。原 JS 未处理 textarea 的
keydown，所以 Enter 插入换行；工具事件只进入独立开发者 Trace。

`晴天` 是原 HTML 的硬编码标题，无业务含义，本轮改为“电商客服”。品牌商品来自
`src/acs/db_seed.py` 的手写合成种子，未接入 Anker 或其他商城。没有自动替换历史商品，
避免破坏冻结评测。当前基础数据：3 用户、4 商品、25 订单、26 订单行、19 物流记录、
37 历史价格；USER001 有 10 个订单；`data/docs` 有 8 份知识 Markdown。
样例品牌名和规格不能作为真实商品信息使用。页面新增“关于演示数据”说明。

## 修改和边界

- 复用原 WebEngine、REST、SSE、模型传输及 M6 正式提交事务；默认 Gradio 不变。
- Enter 提交一次，Shift+Enter 换行；组合输入及 keyCode 229 不触发发送；加载中禁止提交。
- 每轮对话增加工具进度折叠块，区分请求、真实回执、完成和错误。重新播放事件仍去重。
  没有隐藏推理展示，技术 Trace 仍可展开查看。
- 正文沿用实际 SDK 流式片段和持久化 answer_delta；工具阶段草稿及被拒绝输出会撤回。
  生成中标明待核验；最终业务成功仍只能依据 SQLite 权威回执。
- `src/acs/interactive_demo.py` 与 `scripts/v3_interactive_demo.py` 提供受信任的本机交互配置。
  用户本轮明确回复“不限制api调用”，因此独立交互账本不设累计请求/金额上限。
  继承已有费用预留、usage、锁、请求取证与错误停止；原 Benchmark 有限额构造器不变。
  许可和账本持久绑定同一目录与期限，不得将历史付费实验账本改成不限额。
- 模型固定 deepseek-flash、temperature=0、非思考、最多1024输出Token；单次超时60秒，
  原 Graph 单轮保护、权限、确认及未知业务结果保护保持。不是无限模型上下文或生产认证。
- 只监听127.0.0.1、单Worker、合成数据库。正式申请仍须单独卡片确认，只创建模拟待审核记录。

## 价格与私有状态

2026-10-26 再次核验[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：
模型名称及峰值输入2元/百万、缓存0.04元/百万、输出8元/百万与既有账本一致。
账本同时保留峰值保守估计和时段usage估计，均不等于供应商扣款账单。
每次实际请求仍核对官方价格快照，不一致时停止。

本次运行目录：`/home/qingtian/.local/state/ecommerce-agent/v3-interactive-20261026`。
`business.sqlite` 是权威业务库，`web.sqlite` 是浏览器会话/作业/事件；Graph Checkpoint
只是执行镜像。`fees.jsonl` 保存实际HTTP、Token、延迟、费用和错误。
私有许可及价格快照在同级 `interactive-grants-20261026`，原始请求/响应只在运行目录的
`.private-evidence`。不提交密钥、Cookie、许可或完整原始Prompt。
原 `m71-free-review-20261025` 及所有历史结果保留。

本次应用许可有效24小时，截止2026-10-27北京时间10:59:38；不自动延长或重置账本。

## 启动

已有环境与本次私有许可下，从V3 Worktree执行：

```bash
.venv/bin/python scripts/v3_interactive_demo.py \
  --state-dir /home/qingtian/.local/state/ecommerce-agent/v3-interactive-20261026 \
  --env-file '/home/qingtian/projects/E-commerce Agent/.env' \
  --authorization /home/qingtian/.local/state/ecommerce-agent/interactive-grants-20261026/authorization.json \
  --price-review /home/qingtian/.local/state/ecommerce-agent/interactive-grants-20261026/price-review.json \
  --port 8767
```

首次全新目录需加 `--init`，已创建的目录禁止再次初始化。
浏览器访问 `http://127.0.0.1:8767`，确认右上角为“DeepSeek · 实时自由对话”。
真实模式隐藏固定步骤按钮，可以直接自由提问。刷新保留会话，不自动重发POST。

## 离线验收

完整 Pytest：1130 passed、0 skipped，150.06秒，18个既有警告；受影响专项54 passed。
Ruff通过。浏览器工具单独安装Playwright 1.64.1，不修改项目依赖及冻结清单。

```bash
/tmp/ecommerce-ui-browser-20261026/bin/python scripts/eval_chat_interaction.py \
  --out /tmp/chat-interaction-new-run --port 8773
```

真实 Chromium + SDK MockTransport + 合成 SQLite，12项全部通过；0付费模型请求。
旧 `d5d2198` JS 在同一合同中4项失败：Enter、对话内工具进度、重放工具记录、对话内错误。
刷新没有增加HTTP；3个聊天请求、4次离线SDK请求、1次只读商品工具、正式申请0。
早期验收脚本曾因初始化就绪/CSP字符串轮询/错误观察刷新方式/端口冲突失败，未当作业务
成绩；最终日志与截图在 `results/live_chat_interaction_20261026`。

截至本提交，真实 API 验证待执行，不能将离线脚本输出宣称为 DeepSeek 效果。
公开真实冒烟摘要另行绑定实际源码SHA；不重新执行M8.2、不扩展Benchmark。
