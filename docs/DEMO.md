# 电商客服 Agent 演示与复现

演示使用独立合成数据，不是真实交易。业务参考日期为 2026-10-15；主机日期不是退货判定时钟。
代码默认四个保护开关均关闭，以下`--demo`只为当前进程显式开启，不修改。env。

## 启动

首次准备：`uv sync --extra dev --frozen`，复制。env.example 为。env 并填 DeepSeek Key。
已有。env 保留，不覆盖。v2 演示模式通过整篇文档工具取知识，不需要 Embedding Key 或建向量索引。

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
停止按 Ctrl+C。没有`--demo`时遵循。env/Settings，不自动启用保护；发送真实对话会使用付费 API。

若。env 已有管理账号就沿用；否则显式 demo 模式使用`demo-reviewer / demo-test-password`，
这是仅供本机合成场景的公开演示账号。管理员仍须登录，审核原因必填，身份来自已认证请求。

## 五分钟流程

| 时间 | 操作 | 观察与讲解 |
| --- | --- | --- |
| 0:00 至 0:45 | USER001 新会话，问“查询商品 V201 的规格。”、“退货政策的期限是什么？”、“查 S20008 的颜色和状态。” | get_product、get_knowledge_documents、get_order 分别给商品/政策/SKU 事实；此配置是整篇文档，不是向量 RAG |
| 0:45 至 1:45 | 新会话说“我想退 S20017。”；随后明确更正为“S20002 的 A1 黑色耳机退 1 件，无理由，未拆封，符合页面所列二次销售条件。” | 订单补存只补已核实订单，不自动选商品；更正后检查卡片 S20002；刷新页面后选择该历史会话，确认商品/数量/原因仍在 |
| 1:45 至 2:30 | 说“我不是要取消退货草稿，只是问问。” | 即使模型误调取消，后端拒绝写入；最终状态来自读回，草稿仍保留 |
| 2:30 至 3:15 | 检查材料声明，点“核查并生成申请预览”，再点“确认提交申请” | 预览不是申请；按钮才确认创建 pending_review，不退款 |
| 3:15 至 4:15 | 管理员刷新并选择本次 S20002 申请，等待详情加载，填原因并通过或拒绝 | 不选场景自带历史 S20001；审核通过只允许退回，不代表实物核验或退款 |
| 4:15 至 4:40 | 客户点“查询提交结果” | 实际记录、审核原因与草稿/申请/退款状态区分，重启后仍可恢复 |
| 4:40 至 5:00 | 展示下面确定性对照与真实模型报告 | 原模型答 25 件、SQL 为 20/25/29、最终 29 件；区分脚本注错和真实模型结果 |

模型自主选择工具，真实演示的措辞、工具与耗时可能变化。严格复现工程机制请用离线注错
和浏览器证据，不把脚本生成的错误说成现场真实模型自然复现。若本次 S20002 已申请，
新演示改用新隔离目录，不能删除旧申请或绕过剩余数量检查。


## 本轮页面与录制验收

[完整结果](../results/project_handoff_babbfab/browser_v2/summary.json)来自真实 Gradio 页面和 SQLite，
模型是明确注错脚本，零付费 API。新增[恢复截图](../results/project_handoff_babbfab/browser_v2/restored.png)、
[知识工具截图](../results/project_handoff_babbfab/browser_v2/knowledge.png)、
[诊断对照截图](../results/project_handoff_babbfab/trace_diagnostic.png)及
[后台回执](../results/project_handoff_babbfab/backend_receipts_v2.json)。

静音自动化录屏：[客户完整操作](../results/project_handoff_babbfab/browser_v2/video/customer-demo.webm)、
[管理员审核](../results/project_handoff_babbfab/browser_v2/video/admin-demo.webm)。
它们是加速的脚本模型操作记录，不是五分钟真人讲解或新的真实 LLM 评测。
面试录制时按上表节奏讲解，可先放录屏再展示历史真实模型原始结果。

| 操作后 | 预期工具/入口 | 可验证状态 |
| --- | --- | --- |
| 查询商品、政策、订单 | get_product / get_knowledge_documents / get_order | 工具回执有商品与政策来源；S20008 为 V201-2 白色；业务表不变 |
| 我想退 S20017 | get_order；模型可能调用 update_return_task，遗漏时 P0 补存 | 只有本人订单的草稿，未指定 item，不产生新申请 |
| 明确更正为 S20002 耳机 1 件无理由未拆封且符合条件 | get_order / update_return_task | 草稿改为 S20002、明细 3、数量 1；旧字段按依赖失效，页面恢复后相同 |
| 不是要取消草稿 | 可能不调工具；脚本强制误调 cancel_return_task | 后端拒绝，草稿和 revision 不因误取消改变，不虚报清空 |
| 预览 | 页面核查并生成申请预览 | ready=true 才可确认；预览不新增 return_requests |
| 明确确认 | 页面确认提交申请 | 本次 S20002 产生一条 pending_review，草稿 submitted；不退款 |
| 管理员审核并查询 | 认证管理入口 / 客户查询按钮 | 同一申请一条 approved 审核，原因可见；种子历史 S20001 仍是另一条记录 |
| 新会话问总件数 | list_orders；开启组 order_aggregation/output_gate | 原答 25 件只在诊断区；SQL20 单 25 明细 29 件，正文 29 件；聊天帧不泄漏 25 件 |

“显示开发观察”可查看工具、权威计数、原答和最终答案。脚本 usage 是占位值，页面上的示例
Token/费用不能当作本次真实用量；真实历史 A/B 另见报告。普通真实模型未必选择同一工具，
不要承诺现场自然复现某句错误。页面操作后等待卡片/完成状态再继续，刷新后显式选回历史。

## 保护范围

| 开关 | 显式 demo 值 | 有效范围与退出条件 |
| --- | --- | --- |
| ACS_RETURN_STATE_CHECK_ENABLED | true | “我想退+本人订单”且同轮成功核查；只补不存在的订单草稿；“请帮我退”等仍可能不命中 |
| ACS_ORDER_CANDIDATE_RENDER_ENABLED | true | 首轮明确全部明细或受限耳机候选、单次完整 list_orders、无既有草稿 |
| ACS_ORDER_CONSTRAINT_ENABLED | true | 白/黑/蓝色明确限制、更正、取消；“谢谢/好的/收到”保留；陌生条件暂停，不是通用记忆 |
| ACS_ORDER_AGGREGATION_ENABLED | true | 首轮明确订单/明细/件数、显式状态或订单子集；金额、型号数、历史隐含范围不接管 |

受保护请求在核验前不展示模型结论；无完整回执时给未核实提示。进度/工具轨迹仍可见。
模型原答只留在明确诊断轨迹。普通问答继续流式，规则不等于通用事实审查。
取消草稿权限与政策/日期安全修复始终生效，不依赖实验开关。

## 离线证据与自动化

- [完整阶段报告](../results/final_delivery_c9afdbf/REPORT.md)：红绿、完整回归、在线独立统计、成本与 Go/No-Go。
- [浏览器真实操作](../results/final_delivery_c9afdbf/browser_styled/summary.json)：原版 Gradio 组件、真实 ChatService/SQLite、脚本模型，无付费调用。
- [逐帧故障注入](../results/final_delivery_c9afdbf/browser_faults/summary.json)：无工具、断流、预算超时、刷新恢复。
- [冻结任务](../results/final_delivery_c9afdbf/live_cases.json)与预演/真实结果目录分开，已见开发/回归，不是留出集。

```bash
uv run pytest
uv run ruff check .
uv run python scripts/check_secrets.py --all-tracked
```

CI 使用锁定依赖、无模型密钥运行上述检查；可选 tau2 缺失时预期跳过。
浏览器验证另需 Playwright/Chromium，可在临时虚拟环境安装，不加入应用依赖。
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

终端 4 执行真实页面操作及 DOM 观察，输出新目录：

```bash
/tmp/acs-browser-check-new/bin/python scripts/check_delivery_ui.py --out /tmp/acs-ui-new-evidence --video
```

故障验收另起客户服务，加`--faults --candidate`、全新`--scenario`与`--port 7895`；
浏览器命令加`--faults --url http://127.0.0.1:7895 --out /tmp/acs-ui-new-faults`。
以上模型均为故意注错脚本，不消耗 API；仅浏览器访问本机网络。
在线脚本的`--live`确实付费，未来实验必须另定任务/总预算，不自动延续本轮剩余额度。
