# M7.1 验收记录

基线d3f5078，独立分支fix/v3-demo-ux-grounding；未合并main或修改PR #4。实际执行顺序是冻结旧版复现、专项红绿、候选真实浏览器、完整回归、冻结源码/CI，再决定真实DeepSeek门禁。

## 已执行的离线证据

- 旧版七场景：`results/m71_ux_baseline_20261025_run2/scorecard.json`，真实Chromium/SQLite；记录失败并保留原图。
- 候选七场景：run1/run2通过。增强十场景run3发现恢复入口被折叠，修复后以新目录run4复验，最终结果以其Scorecard为准。
- 真实SDK正文流：`results/m71_stream_offline_20261025_run1`三阶段完成，包括故意协议拒绝/撤回、刷新时完成、进程重启，0模型网络请求。
- 共享窗口：原EpisodeLedger单文件增加更严的持久窗口；原3元/24、已消费4次、未知/失败请求、原截止时间全部保留。新增2元/18按原保守reserve计算，超限在HTTP前阻止；重启同名窗口不重置。没有第二份费用账本或退款释放。
- 初轮全量遇到本轮事实投影新增count时缺closing导入，修复后1034项通过；补权限和过期反例后1038项通过。只读恢复专项发现原生ToolNode会将旧写请求作为无效工具返回，并未写入；进一步增加恢复前明确拒绝，强断言保留。最终全量/CI计数另以实际报告记录，不以旧计数冒充。

## 真实模型门禁

继续使用10月25日手工界面的持久共享账本：`~/.local/state/ecommerce-agent/v3-demo-20261025-run1/fees.jsonl`。截止时间从最早Episode继承，不新开时钟；原已有4次付费请求不算本轮新成绩。新合成Fixture使用同一账本注入paid_model，并追加固定名m71-20261025的2元/18次窗口，所有限制同时生效。

只有离线合同通过且原deadline仍有效，才执行新的冻结六阶段Development Smoke。新目录、源码SHA、模型参数与官方价格指纹必记。出现未知写入、协议异常、越权或重复写入即停止，不自动重跑。若原期限过期，按本轮要求仅交付离线验收，明确列出付费阶段未执行并需新的独立授权；不能另开账本规避。

## 复现

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run pytest
uv run ruff check .
uv run python scripts/check_secrets.py --all-tracked
# 独立Playwright/Chromium环境：
python scripts/eval_m71_ux.py --out results/m71_NEW --port 8782
python scripts/eval_answer_stream.py --out results/m71_stream_NEW --port 8783
# 免费手工界面：
uv run python scripts/v3_web_demo.py --init --state-dir /tmp/m71-free-NEW --port 8767
```

本地完整τ回归需TAU2_DATA_DIR指向官方dd8757d的data；本机路径恢复错误曾只导致collect失败，未跳过测试或改任务。原依赖未升级，默认Gradio入口不变。公共Git只放脱敏摘要、最终Scorecard、源指纹与精选截图；原始SQL、Cookie、模型输出和诊断原件留在私有目录。

Go/No-Go必须区分本机合成Demo、独立开发期Evaluation和生产使用；本轮不把UX修复或已定义声明纠正当作模型通用业务能力提升。

最终本地完整执行：1044 passed、0 skipped、93.91秒；Ruff通过。新增42项（相对d3f5078的1002项）。十场景Chromium run4全部通过，含真实os._exit(72)后待核验占位、显式恢复且模型调用计数不变；没有删除失败样例。源实现冻结后另复核CI与固定SHA浏览器，再决定真实付费门禁。
