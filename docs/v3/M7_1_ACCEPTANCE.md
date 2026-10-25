# M7.1 验收记录

基线 `d3f5078`，独立分支 `fix/v3-demo-ux-grounding`。原 Worktree 的 `/tmp` 文件曾因环境重建丢失，本轮恢复同一注册目录、按原 uv.lock 恢复依赖；未删除历史目录、合并 main、更新 PR #4 或改默认 Gradio Runtime。主目录三个用户文件的 SHA256 前后一致。真实运行源码与最终修复必须分别查看。

## 已执行的验收

| 层次 | 证据 | 结论及边界 |
|---|---|---|
| 修复前 Chromium | `results/m71_ux_baseline_20261025_run2` | 原版7场景；保留锁定、4xx、Markdown/历史预览失败与桌面/移动图 |
| 修复候选 Chromium | `results/m71_ux_candidate_20261025_run4`、`run5` | 10场景通过，DOM、HTTP次数、375宽度及SQL终态核对；含真实退出72、回执丢失和重启 |
| 五状态补验 | `results/m71_card_states_20261025` | REJECTED、EXPIRED 的真实页面/SQLite，正式三表均0；其余状态在主矩阵覆盖 |
| 原生 SDK 正文流 | `results/m71_stream_offline_20261025_run1`、`results/m71_stream_final_20261025_run1` | 真实SDK + MockTransport，包含提前可见、工具阶段撤回、故意协议拒绝、刷新和重启；各4个模拟HTTP、0付费 |
| 本地完整回归 | `results/m71_delivery_20261025` | 最后全量1048 passed、0 skipped（89.67秒），相对基线新增46项；原M6的39项保留，Ruff及密钥扫描通过 |
| 真实付费源码CI | [40156298578](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40156298578) | `e42eff2`，1044 passed、0 skipped，Ruff/密钥扫描成功；不冒充后续修复CI |
| 真实模型 | `results/m71_deepseek_20261025_run1/REPORT.md` | 6阶段全部完成，13次HTTP、8个工具回执、SQL唯一待审核；语义仍有缺陷，见下 |

最终发送反馈代码的Chromium十场景 `results/m71_ux_final_20261025_run3` 全部通过，浏览器异常0；该Scorecard含实际源码指纹。发送瞬间的HTTP暂挂/单次放行检查另见 `results/m71_send_feedback_20261025`。最终HEAD的CI以GitHub实际结果为准，不能用付费源码CI代替。

## 真实模型结果与费用

六阶段冻结输入依次覆盖商品、订单候选、草稿/运费混合咨询、补信息、插问、更正及预览。随后页面显式确认创建一份 `pending_review` 模拟申请；SQL申请、确认、审计、幂等回执各一条，确认消费一次。Graph、SQL和持久SSE对账，刷新/重启不新增模型HTTP。

新增13请求、88666输入/1779输出Token，保守预留1.497672元，实际时段usage估算0.026790元（峰值估算0.053580元），均非供应商扣款账单。平均首个公开正文5.90秒，平均整轮14.60秒；所有阶段均在最终模型HTTP结束前看到正文。没有假打字。

使用10月25日手工界面的**同一持久账本**，开始前4请求/0.198592元；追加更严2元/18请求窗口，同时保留原3元/24请求和原截止时间。累计17请求/1.696264元预留。原deadline届满后只读核验得到`episode_deadline`，之后新增HTTP为0；没有换账本或时钟。

第6阶段模型原文仍编造旧预览，展示检查依据SQL成功局部纠正。无冒号“预览有效期至03:12”漏检时区；卡片正确，聊天该句没有充分说明时区。保留真实失败，随后补离线红绿及HTTP/Graph测试，相同私有原文重放修复；没有真实重试。第4/5阶段间接“告诉我提交”表述也有权限语义歧义。**不能报告全部自然语言语义通过或模型不再幻觉。**

## 实施边界与失败记录

复用原FastAPI、return-card DTO、FormalReturnPoc/确认桥、ReturnService、SSE和EpisodeLedger。UNKNOWN只读由应用端及恢复执行入口重新校验，不能由模型或旧Checkpoint扩大权限；提交成功只依据完整业务回执。展示缓存只派生文字，不是第二套业务状态。

本轮保留失败：错误TAU数据路径导致收集失败、遗漏closing导入、恢复按钮误放折叠区、历史身份及跨段字段反例、只读恢复断言不足；修复后完整执行，未静默跳过。最后浏览器发送反馈复验又暴露测试器从暂态历史读取目标0轮的等待竞争，保留失败Scorecard及私有DOM，将目标轮数在发送前冻结，并同时核验API/DOM完整轮数，未改业务输入或SQL断言。详细分类在`failures.json`。

原始模型/HTTP/Cookie/完整SQL证据在受限`.private-evidence/`，Git只放合成摘要、指纹、脱敏审计和精选截图。Markdown是安全子集；正文流在最终核验前为可撤回草稿，仍可能短暂包含错误。规则覆盖标为`scoped_rules_only`，未知表达需人工复核。

## 运行与复现

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
# TAU2_DATA_DIR 必须指向官方 dd8757d 的 data，不要指向新版任务。
uv run pytest
uv run ruff check .
uv run python scripts/check_secrets.py --all-tracked
# 另建含 Playwright/Chromium 的浏览器环境，输出目录必须是新的：
/tmp/acs-m71-browser/bin/python scripts/eval_m71_ux.py --out results/m71_NEW --port 8782
/tmp/acs-m71-browser/bin/python scripts/eval_answer_stream.py --out results/m71_stream_NEW --port 8783
# 免费固定模式，本机单 Worker、全新合成 Fixture：
uv run python scripts/v3_web_demo.py --init --state-dir /tmp/m71-free-NEW --port 8767
```

当前保留手工界面为 `http://127.0.0.1:8767` 的免费固定模式。原共享付费时限已到，不能在旧授权下继续真实自由对话；历史私有账本和Fixture均保留。

## 结论

1. 已修复UNKNOWN全局锁、明确4xx误判、慢响应进度、基本Markdown/商品订单卡片、移动布局和隐藏恢复入口；刷新/SSE不重执行。
2. 真实延迟仍有首段约2.43–9.29秒，Markdown不是完整CommonMark；间接权限表述、未知自然语言事实和流式临时错误仍有限制。
3. 模型仍会编造历史；本轮证明的是有限展示纠正，不能证明原文全面正确。
4. 商品和订单查询在固定模式和真实模型浏览器/工具回执中实际完成，未复制占位回答。
5. 真实浏览器草稿、更正、预览、应用确认到唯一模拟申请已完成；异常恢复另有固定故障测试，不声称付费覆盖全部故障。
6. **本机合成Demo验收Go；独立多轮Evaluation有条件Go**：先冻结未见用例，分别评分原文/展示/误纠正/未核实风险，另行授权真实预算。日期修复的真实复核仍待新授权；生产部署、正式Benchmark结论及默认替换No-Go。
