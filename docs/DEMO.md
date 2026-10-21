# 电商客服 Agent 演示与复现

演示使用独立合成数据，不是真实交易。业务参考日期为2026-10-15；主机日期不是退货判定时钟。
代码默认四个保护开关均关闭，以下`--demo`只为当前进程显式开启，不修改.env。

## 启动

首次准备：`uv sync --extra dev --frozen`，复制.env.example为.env并填DeepSeek Key。
已有.env保留，不覆盖。v2演示模式通过整篇文档工具取知识，不需要Embedding Key或建向量索引。

终端一（先运行，会在目录不存在时创建数据）：

```bash
uv run python scripts/workbench.py --demo
```

终端二（客户入口启动后再运行，复用同一数据库）：

```bash
uv run python scripts/workbench.py --demo --admin
```

客户入口：http://127.0.0.1:7860；管理员：http://127.0.0.1:7861。
默认数据目录`~/.local/share/ecommerce-agent/workbench-v2`，重启保留会话和申请，不重新播种。
可以用`--scenario /绝对路径`指定全新隔离目录，或`--port`避开已占端口；两个入口须使用同一目录。
停止按Ctrl+C。没有`--demo`时遵循.env/Settings，不自动启用保护；发送真实对话会使用付费API。

若.env已有管理账号就沿用；否则显式demo模式使用`demo-reviewer / demo-test-password`，
这是仅供本机合成场景的公开演示账号。管理员仍须登录，审核原因必填，身份来自已认证请求。

## 五分钟流程

| 时间 | 操作 | 观察与讲解 |
| --- | --- | --- |
| 0:00–0:45 | USER001新会话，问“S20008买的耳机是什么颜色？”；再问退货政策 | get_order的所购SKU与文档政策分别给事实；原答/工具/知识依据可展开观察 |
| 0:45–1:45 | 新会话说“我想退S20017。”；随后明确更正为“S20002的A1黑色耳机退1件，无理由，未拆封，符合页面所列二次销售条件。” | 订单补存只补已核实订单，不自动选商品；更正后旧字段失效，检查草稿卡片而非只看口头回复 |
| 1:45–2:30 | 说“我不是要取消退货草稿，只是问问。” | 即使模型误调取消，后端拒绝写入；最终状态来自读回，草稿仍保留 |
| 2:30–3:15 | 检查材料声明，点“核查并生成申请预览”，再点“确认提交申请” | 预览不是申请；按钮才确认创建pending_review，不退款 |
| 3:15–4:15 | 管理员刷新并选择本次S20002申请，等待详情加载，填原因并通过或拒绝 | 不选场景自带历史S20001；审核通过只允许退回，不代表实物核验或退款 |
| 4:15–4:40 | 客户点“查询提交结果” | 实际记录、审核原因与草稿/申请/退款状态区分，重启后仍可恢复 |
| 4:40–5:00 | 展示下面确定性对照与真实模型报告 | 原模型答25件、SQL为20/25/29、最终29件；区分脚本注错和真实模型结果 |

模型自主选择工具，真实演示的措辞、工具与耗时可能变化。严格复现工程机制请用离线注错
和浏览器证据，不把脚本生成的错误说成现场真实模型自然复现。若本次S20002已申请，
新演示改用新隔离目录，不能删除旧申请或绕过剩余数量检查。

## 保护范围

| 开关 | 显式demo值 | 有效范围与退出条件 |
| --- | --- | --- |
| ACS_RETURN_STATE_CHECK_ENABLED | true | “我想退+本人订单”且同轮成功核查；只补不存在的订单草稿；“请帮我退”等仍可能不命中 |
| ACS_ORDER_CANDIDATE_RENDER_ENABLED | true | 首轮明确全部明细或受限耳机候选、单次完整list_orders、无既有草稿 |
| ACS_ORDER_CONSTRAINT_ENABLED | true | 白/黑/蓝色明确限制、更正、取消；“谢谢/好的/收到”保留；陌生条件暂停，不是通用记忆 |
| ACS_ORDER_AGGREGATION_ENABLED | true | 首轮明确订单/明细/件数、显式状态或订单子集；金额、型号数、历史隐含范围不接管 |

受保护请求在核验前不展示模型结论；无完整回执时给未核实提示。进度/工具轨迹仍可见。
模型原答只留在明确诊断轨迹。普通问答继续流式，规则不等于通用事实审查。
取消草稿权限与政策/日期安全修复始终生效，不依赖实验开关。

## 离线证据与自动化

- [完整阶段报告](../results/final_delivery_c9afdbf/REPORT.md)：红绿、完整回归、在线独立统计、成本与Go/No-Go。
- [浏览器真实操作](../results/final_delivery_c9afdbf/browser_styled/summary.json)：原版Gradio组件、真实ChatService/SQLite、脚本模型，无付费调用。
- [逐帧故障注入](../results/final_delivery_c9afdbf/browser_faults/summary.json)：无工具、断流、预算超时、刷新恢复。
- [冻结任务](../results/final_delivery_c9afdbf/live_cases.json)与预演/真实结果目录分开，已见开发/回归，不是留出集。

```bash
uv run pytest
uv run ruff check .
uv run python scripts/check_secrets.py --all-tracked
```

CI使用锁定依赖、无模型密钥运行上述检查；可选tau2缺失时预期跳过。
浏览器验证另需Playwright/Chromium，可在临时虚拟环境安装，不加入应用依赖。
`check_delivery_ui.py --serve`只启动明确标注的脚本模型验收入口，不作为真实模型成功率。

## 重新运行离线实验

以下输出目录须尚不存在，原始冻结证据不覆盖；全程不加`--live`：

```bash
uv run python scripts/eval_delivery_live.py --cases results/final_delivery_c9afdbf/live_cases.json --out /tmp/acs-delivery-preflight-new
uv run python scripts/replay_delivery_trace.py --source results/final_delivery_c9afdbf/live/raw.jsonl --cases results/final_delivery_c9afdbf/live_cases.json --out /tmp/acs-delivery-replay-new
uv run python scripts/calibrate_delivery_judge.py --out /tmp/acs-delivery-judge-new
```

浏览器验收另建临时环境，不改变应用锁：

```bash
uv venv /tmp/acs-browser-check-new
uv pip install --python /tmp/acs-browser-check-new/bin/python playwright==1.64.0
/tmp/acs-browser-check-new/bin/python -m playwright install chromium
```

在三个终端依次启动脚本模型服务，先等客户入口可访问再启动同库管理员，避免首次播种竞争。
使用全新隔离目录；同一目录已有申请时不要重复运行并删除旧数据：

```bash
# 终端1：候选客户
uv run python scripts/check_delivery_ui.py --serve --scenario /tmp/acs-ui-new-candidate --candidate --port 7890
# 终端2：候选管理员，与终端1同库
uv run python scripts/check_delivery_ui.py --serve --scenario /tmp/acs-ui-new-candidate --admin --port 7891
# 终端3：关闭两个新保护的基线
uv run python scripts/check_delivery_ui.py --serve --scenario /tmp/acs-ui-new-baseline --port 7892
```

终端4执行真实页面操作及DOM观察，输出新目录：

```bash
/tmp/acs-browser-check-new/bin/python scripts/check_delivery_ui.py --out /tmp/acs-ui-new-evidence
```

故障验收另起客户服务，加`--faults --candidate`、全新`--scenario`与`--port 7895`；
浏览器命令加`--faults --url http://127.0.0.1:7895 --out /tmp/acs-ui-new-faults`。
以上模型均为故意注错脚本，不消耗API；仅浏览器访问本机网络。
在线脚本的`--live`确实付费，未来实验必须另定任务/总预算，不自动延续本轮剩余额度。
