# M7 固定模型 + 真实浏览器验收

运行命令：`/tmp/acs-browser-check-20261021/bin/python scripts/eval_web_demo.py --out results/web_demo_offline_20261024_run4 --port 8768`。

这是**固定模型协议测试**，不是DeepSeek效果。浏览器环境使用既有Playwright/Chromium，应用使用本项目.venv Python、FastAPI、原生Graph及M6专用合成数据库。

- 10阶段：商品查询、订单查询、原冻结8阶段。逐阶段草稿/预览/更正/取消/咨询保持revision与实际SQL核对，不只检查HTTP 200。
- 全阶段通过，浏览器pageerror为0；执行前后源码指纹一致，见scorecard.json。
- SSE关闭/离线再连、页面刷新不增加任何业务记录；服务实际退出再启动后保留Cookie/CSRF/session/草稿/预览。
- 原生Interrupt后点击独立确认按钮；申请、正式确认、created审计、幂等回执各1，状态pending_review；重复决定前后全部SQL相同。
- 桌面1280、移动375截图已人工查看；320/375/1280无横向溢出。实际截图为 `preview-desktop.png`、`submitted-desktop.png`、`submitted-mobile.png`。
- 付费模型请求0，费用0。完整服务器日志、浏览器Cookie/CSRF、逐阶段SQL和原生Trace只保存于受限且忽略的.private-evidence，不公开凭证。

HTTP专项17项通过；真实HTTP发起确认后，在COMMIT前/后使用os._exit退出服务的2项通过；24/32请求SDK MockTransport预算2项通过。完整回归及CI另行记录。

历史失败未覆盖：run1为CSP拒绝自动化字符串eval；run2为测试函数拼接错误；run3在SSE连接存在时服务无限等待退出。修复测试函数及设置Uvicorn有限连接关闭等待，不放宽CSP，不更改业务规则。各失败Scorecard及私有日志保留。run4是最终冻结成功证据。
