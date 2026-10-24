# M7 最终基础源码离线浏览器验收

源码 `15498f29158de93c382c49aee63657a6719158f7`，固定模型，非DeepSeek成绩。商品/订单2阶段与冻结本地8阶段共10阶段通过，浏览器0 pageerror；源码指纹运行前后相同。完成真实浏览器输入、插问、更正、取消、预览、服务进程重启、刷新、SSE断开重连、显式确认和重复点击。

正式申请、确认、审计、幂等回执各1，审核0；重复确认SQL不变。查看 [Scorecard](scorecard.json)、[SQL终态](sql_final.json)、[桌面](submitted-desktop.png)、[移动](submitted-mobile.png)。人工查看截图，320/375/1280宽度无横向溢出。

[完整CI](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40068447544)：968 passed、0 skipped、0 failed/error；M6原子提交39项、Web22项，Ruff与全追踪文件密钥扫描通过。JUnit摘要见 [ci_acceptance.json](ci_acceptance.json)。付费门禁配置在HTTP前写入 [paid_gate.json](paid_gate.json)。

真实模型另存 `../web_demo_deepseek_20261024_run1/`，不能混用结果。后续能力投影修复不改本运行的指纹或成绩，另以离线回归和新CI验证。
