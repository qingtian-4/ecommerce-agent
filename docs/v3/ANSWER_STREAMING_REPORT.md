# DeepSeek 自由对话与正文流

本轮针对用户截图中的两个独立事实修复：页面原默认使用固定脚本，查询结果共用占位回复；原 SSE 只传工具进度，没有正文流。固定查询结果展示已在 566de1c 完成。本分支增加显式 `--deepseek` 模式下的真实正文流，默认脚本、Gradio、官方 τ HalfDuplex 和 M6 提交事务保持原行为。

## 实现与边界

- `scripts/v3_web_demo.py --deepseek` 使用原 paid_model 与 EpisodeLedger，开启 ChatOpenAI streaming 和同步 HTTP 传输的显式流式选项。HTTP 前持久预留，3 元、24 次尝试与原 Episode 截止时间不可通过重启重置。失败、未知 usage 不免费，不自动重试。
- `episode_transport.py` 转发完整 SSE 帧，保留原始字节；终止 `[DONE]` 必须等完整响应 Guard、取证、usage 和费用回执写入成功后才放行。因此 SDK/Graph 不能从不完整流执行工具。旧 Adapter 和异步入口默认继续完整缓冲。
- `local_return_poc.py` 仅增加可选 RunnableConfig callback。原生 create_agent、updates、max_concurrency=1、ToolRegistry、业务权限、回执和 Checkpoint 不改变。没有重新编写模型/工具循环。
- `AnswerStreamCallback` 只观察 AIMessageChunk.content。reasoning_content、工具参数和原始响应不公开；正文通过跨片段保留窗口及既有脱敏。遇到 tool call 或错误就撤回临时文字；最终回答仍须经过响应合同和 SessionStore 保存。
- 新事件 `answer_delta` 为累计的脱敏文本前缀，`status=provisional`；`answer_discarded` 撤回指定 message_id；`answer_completed` 只在业务 Turn 完成后发布。三者共用既有持久事件序号和会话权限；execution_id+message_id 定位，不新增 REST 写接口。
- 页面只使用 textContent，生成中明确标记，SQL 卡片继续是申请结果唯一权威。刷新先完整补读历史，再重新读取快照，解决“快照 running、补读已 completed”造成空答案和发送按钮不解锁的竞争。SSE 重连与 GET 不执行模型/工具。

短于脱敏窗口（至少64字符）的答复可能整段显示。文本与工具调用混合时，中间文字可撤回，不能把它作为操作完成证明。这里是正文增量而非保证每个 token 都独立显示，不使用定时打字假流式。

## 离线证据

新18项测试使用真实 SDK、httpx.MockTransport、原生 Graph、FastAPI TestClient 与合成 SQLite，0 skipped。覆盖首片段先于上游结束、逐字节拆分 DONE、截断/无 usage、超时/断流/响应头后关闭、重启后预算、mixed content、批量工具 ID、未知工具整批不执行、敏感字段所有切分点、失败撤回和重复 SSE GET 不再调用模型。

初始测试旧实现4项传输失败、2项 Web 失败，原因是没有 live 选项。原生 Graph messages 模式在 max_concurrency=1 下出现等待任务抢占唯一执行槽；保留私有栈证据，改用官方 LangChain callback，未放宽工具并发。

浏览器 `results/answer_stream_offline_20261024_run7/scorecard.json`：三阶段、4次合成 SDK HTTP、0次付费、0页面错误。普通回答、mixed content→真实商品工具→最终回答均完成；第三阶段故意 length 被拒绝并撤回，正式申请/确认/审计均0。刷新完成竞争先红（run4超时），补读后只读 refresh 修复后通过；服务进程重启不增加模型调用。

早期失败均保留本地原目录：run1启动等待过早且统计未建表失败；run2离线模拟器把末尾事实 SystemMessage 当工具尾消息，触发重复ID守卫；run3动态草稿观察时机失败；run4真实刷新竞争红测；run5 Playwright第二参数覆盖计数闭包；run6初始HTML按钮状态被当作已恢复。修复测试器，不改变业务规则或放宽响应 Guard。最终验收要求 SSE 建连后再检查控件，失败退出非0。

## 复现

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run pytest tests/test_episode_streaming.py tests/test_web_answer_stream.py
# 浏览器独立环境需安装 playwright 与 Chromium：
python scripts/eval_answer_stream.py --out results/answer_stream_offline_NEW --port 8771
# 原免费演示仍可独立运行：
uv run python scripts/v3_web_demo.py --state-dir /tmp/v3-free-NEW --init --port 8767
```

真实模型必须先保存并核实当前官方中英文价格快照及 price_review.json，显式使用 --deepseek、--env-file；不能把免费脚本按钮当作真实自由对话效果。真实冒烟将在本分支源码冻结和离线门禁通过后使用全新目录；真实结果另交付，不复用旧结果。

## 安全限制

仅 Linux/WSL、127.0.0.1、单 Worker、合成身份和隔离 SQLite；不代表生产认证、生产提交或退款能力。原始请求/响应、Cookie、数据库、异常栈仅存700私有目录（文件600），公共事件摘要只保留文字长度/哈希和时间。价格按官方可核实单价估算，与供应商账单分别陈述。禁止默认开启付费、任意新建账本绕过本轮总限额或自动重跑失败任务。

本地完整回归：1002 passed、0 skipped、114.23秒；18项新增专项另行通过。完整运行使用新tmpfs隔离Fixture（真实SQLite、真实子进程退出；不模拟fsync），此前ext4运行因jbd2等待停止，原日志与临时库保留；不宣称断电持久性。Ruff、JavaScript语法通过。安全/架构审查及四角对抗检查已完成，发现的刷新竞争与测试器缺陷均已修复。

## 真实验收结果

实现源码736ba3f的CI：1002 passed、0 skipped，Ruff/密钥扫描成功。固定源码浏览器run8也通过三阶段、协议拒绝撤回及刷新完成竞争、服务重启；测试器输出见新目录Scorecard。

[真实DeepSeek报告](../../results/answer_stream_deepseek_20261024_run1/REPORT.md)：一次冻结六阶段完成，13次HTTP，88448输入/1928输出Token，8次工具，应用按钮创建唯一pending_review。6阶段首个公开正文均先于最终模型HTTP完成，平均5.93秒（服务端turn_started起）。保守预留1.496484元，usage/时段估算0.0284224元，0未知请求。原有正式业务规则未改。

仍发现模型无依据的“旧耳机预览已失效”表述和提交能力/时区文字不严谨，不宣称整体语义成功率。真正Go的是本机合成环境真实对话与正文流，不是生产或正式Benchmark。当前8767已切显式DeepSeek模式，复用本轮同一账本、同一3元/24总限额与原Episode截止时间，不能因为服务重启或新浏览器而重置。
