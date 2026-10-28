# 修复验证证据

2026-10-29，基线0f830f6。任务数据版本不变；模型请求协议新增shared-context-v1。

- `baseline.json`：原用户实验摘要，不改原文件或成绩。
- `red.log`：新增控制在修复前失败。
- `trace-replay/report.json`：原模型决策重放，SX04合成末轮续答有显式标记，费用0。
- `rehearsal-final/scorecard.json`：四段冻结脚本，证明协议与SQL执行，不是模型成功率。
- `pytest-final.log`：完整回归；`ruff.log`：静态检查。
- `browser-final/report.json`：真实Chromium16项检查，模型为免费脚本。

修订范围与边界见[说明](../../docs/SHOP_FAILURE_REPAIR.md)。真实实验另存独立目录，
在实施验收和源码提交后执行，保留全部新回答及原始费用。执行前不能宣称真实模型改善。

实施验收：1322项Pytest，0失败、0跳过；Ruff通过；Chromium16/16通过。
