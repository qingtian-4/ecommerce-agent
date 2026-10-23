# 响应合同修复：离线验收

基线 `93db5cf`，分支 `fix/v3-response-contract-parity`。完整离线门禁结果将在本轮最终验收完成后写入 `gates-summary.json`；没有通过门禁前不发送付费请求。本地完整首跑为 838 passed、1 failed，暴露新包装层对旧 Bridge 接口的错误假设；修复后 Bridge 与全部响应合同 30 passed。原 Bridge 用例未修改，详见 `local-validation.json`。付费前还必须取得此修复源码的完整 CI 绿灯。

## 改动与证据

- `src/acs/model_response_contract.py` 统一两个官方 Adapter 的完整响应规则；`scripts/tau_model_response.py` 接入旧 Loop，`scripts/tau_langgraph_agent.py` 接入原生 Graph 模型边界。合法 mixed content 留在模型历史，对用户只发工具意图。
- `src/acs/episode_transport.py` 在 SDK、Guard 和 usage 错误之前持久化原始 HTTP/部分流；`src/acs/response_evidence.py` 保存私有原始证据。公开摘要不含正文、参数值、供应商 metadata，调用 ID 使用稳定 SHA256 映射。
- `scripts/tau_episode.py` 修复恢复后首次派发未经过工具预算的问题，正常与恢复路径共用 `_record_output`。旧未知写回执保护保留，Graph checkpoint 不证明业务写成功。
- `tests/test_response_contract.py` 通过实际 OpenAI/LangChain SDK MockTransport 及原版 Retail Orchestrator 验证配对；测试禁止 socket 网络。专项覆盖公开隐私、取证顺序、畸形流、真实 Graph 恢复预算和 Smoke 共用账本。

本轮先在未修改 Adapter 的 `93db5cf` 上执行新增 mixed 回归，得到 1 failed，再实施修复。红灯日志为本地 `.ci-artifacts/response-contract/red.log`。17 组固定输出矩阵摘要见 `matrix-summary.json`，它不是模型能力成绩。矩阵只执行读取或错误参数，因此 Reward 为 0 是预期；完整任务 33/34 的既有成功写入轨迹继续由原专项回归验证。

审查过程、发现的问题及已修复状态见 `review_summary.json`。早期失败日志和被后续修复取代的中断测试仍保存在本地，不冒充最终绿灯。完整日志和 JUnit 使用 CI Artifact 或本地忽略目录，不重复提交大体积逐例轨迹。

## 重现

使用已有 Python 3.13 环境和冻结依赖，官方 checkout 必须干净且位于 `dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd`，安装的 tau2 为 1.0.1。官方源码及 Retail 数据指纹见 `upstream.json`。

```bash
export TAU2_DATA_DIR=/tmp/acs-external-tau-20261022/data
export LITELLM_LOCAL_MODEL_COST_MAP=True
.venv/bin/python -m pytest --junitxml=.ci-artifacts/pytest.xml
.venv/bin/ruff check .
.venv/bin/python scripts/check_secrets.py --all-tracked
```

全仓测试必须非空且没有 skip；可选依赖缺失不算验收通过。Linux/WSL 是本轮平台目标，保留 POSIX 文件锁，不宣称 Windows 原生兼容。

## 真实验证的门槛

最终源码单独提交后，使用 `scripts/eval_tau_development_pilot.py --freeze` 冻结新的源码 SHA、上游指纹、任务顺序与预算，复制本轮已核验的价格摘要及对应源码的成功门禁证据。`--run` 要求显式的本地 `.env` 路径，不复制密钥。先两臂只读真实 Smoke，再按 33/旧、33/V3、34/旧、34/V3 执行一次，所有角色与 Smoke 共用累计 5 元、48 次 HTTP 上限。失败、预算耗尽或未知写立即停止，不自动重试。

新实验位于单独目录，历史 `results/tau_development_pilot_20261023/` 不覆盖。原始 SDK/HTTP、Trace、checkpoint 和完整账本只保存到新目录的 `.private-evidence/`，权限 0700/0600；公开 Git 只提交结构化脱敏结果。拒绝分类、取证边界与历史不可追溯项见 `docs/v3/RESPONSE_PROTOCOL_ROOT_CAUSE.md`。

## 最终门禁

单行 Bridge 兼容性修复后，推送源码 `3c4ab1c` 并运行完整 [CI 39967136857](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39967136857)：839 passed，0 failed/error/skipped，Ruff 和全量密钥扫描成功。原始 JUnit 已下载并验证 Artifact SHA256，汇总见 `gates-summary.json`；初次本地 838/1 的失败记录仍保留，没有被后续绿灯覆盖。

通过完整门禁后真实执行新 Pilot：两臂 Smoke 与 Task 33 配对通过，Task 34 旧 Loop 通过，Task 34 V3 因累计 48 次上限未启动。实际新记录见 [新实验](../tau_response_contract_pilot_20261023_r1/REPORT.md)，不是旧真实成绩的复制。
