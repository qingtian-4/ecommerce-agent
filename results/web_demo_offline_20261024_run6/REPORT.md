# M7 最终修复源码离线验收

运行源码 `891700927505579452752100dc61f16c0b7d151a`，包含Formal能力投影与历史回执保持修复。固定模型，不调用DeepSeek。真实Chromium完成商品/订单咨询2阶段和冻结本地8阶段，共10阶段通过，0 pageerror；源码指纹前后相同。

完成多轮草稿、商品/政策插问、更正、否定与明确取消、最新预览、服务进程重启、浏览器刷新、SSE断开补读、显式应用确认、重复点击。SQL申请/确认/created审计/幂等回执各1，审核0；重复点击不改SQL。查看 [Scorecard](scorecard.json)、[SQL终态](sql_final.json)、[桌面](submitted-desktop.png)、[移动](submitted-mobile.png)。截图人工查看，320/375/1280无横向溢出。固定模型只能证明协议与状态，不证明模型质量。

[完整CI 40069624994](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40069624994)：**970 passed、0 skipped、0 failed/error**，其中Web24、M6原子提交39；Ruff与密钥扫描通过。已下载JUnit核对实际数量及ZIP SHA256，摘要见 [ci_acceptance.json](ci_acceptance.json)。大日志/JUnit由CI Artifact管理，不把大量中间文件加入Git。Artifact14天后过期，保留摘要、源码指纹与复现命令。

新增两项回归分别先红后绿：Formal能力事实/描述/预览回执一致；恢复保持历史SQL回执的notice，不被当前版本覆盖。没有开放模型提交能力或改变ReturnService规则。真实模型仍只有15498f2上的run1，没有为本修复重新付费；其虚构历史预览等语义失败仍保留。

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/v3_web_demo.py --state-dir /tmp/v3-web-fresh --init
# 重启沿用同目录，去掉--init；默认127.0.0.1:8767，固定模型。
python scripts/eval_web_demo.py --out /tmp/v3-web-browser-fresh --port 8768
```

浏览器验收需独立安装Playwright/Chromium；应用仍使用仓库.venv。原始浏览器状态、逐阶段SQL/Checkpoint/服务Trace均留在受限 `.private-evidence/`，不提交凭证。

Go：本机合成业务Demo与后续独立本地评测。No-Go：真实平台售后、生产认证、正式模型质量结论。完成后不自动进入Benchmark或部署。
