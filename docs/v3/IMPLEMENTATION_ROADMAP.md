# V3 实施路线与运行手册

本轮范围结束于隔离只读 PoC、架构与评价设计、离线及小额真实验收。后续阶段是建议，
没有自动执行授权。本轮不合并 main，不替换 Gradio 默认 Loop，不开启生产配置。

## 1. 本轮已落地的运行路径

在实验 worktree 或检出 `feat/v3-langgraph-poc` 后运行：

```bash
uv sync --extra dev --extra v3 --frozen
uv run --extra v3 python scripts/langgraph_poc.py --state-dir /tmp/ecommerce-v3-demo init
uv run --extra v3 python scripts/langgraph_poc.py --state-dir /tmp/ecommerce-v3-demo ask demo "查一下V201现在的售价"
uv run --extra v3 python scripts/langgraph_poc.py --state-dir /tmp/ecommerce-v3-demo ask demo "它有哪些颜色能买？"
uv run --extra v3 python scripts/langgraph_poc.py --state-dir /tmp/ecommerce-v3-demo inspect demo
```

模型密钥沿用 ACS_LLM_API_KEY、ACS_LLM_BASE_URL、ACS_LLM_MODEL 配置；不输出或提交密钥。
独立 worktree 可在命令全局参数中加 `--env-file /已有配置的绝对路径/.env`。
真实 CLI 仅接受本轮核准的 deepseek-flash、non-thinking、最多 1024 输出 Token，
requests.jsonl 的 5 元/40 次预算跨进程累计。inspect 和 init 不调用模型。
init 生成 catalog_v2 的全新合成场景，已有目录直接拒绝，不能拿它覆盖在用数据库。

目录内容与恢复方式：

| 文件 | 作用 | 恢复 |
| --- | --- | --- |
| business.db、docs/、manifest.json | 原有 build_scenario 生成的合成权威数据，reference_date 固定 | 保留整个场景目录；CLI 先验证数据与知识指纹 |
| checkpoints.sqlite 及运行中的 WAL/SHM | 原生 Graph 消息、中间节点、owner/版本/业务路径绑定 | 停 worker 后备份完整目录；重新运行相同路径、相同 user/session |
| checkpoints.events.jsonl | 工具与节点审计、暂态 token、持久化终态引用 | 保留原记录；恢复状态以 native snapshot 为准，不将日志重放为写操作 |
| requests.jsonl | 请求前费用预留、真实 usage、失败记录 | 继续使用原账本，不能删账本后重试规避本轮预算 |
| checkpoints.lock | 本机独占 worker 锁 | 退出或崩溃自动释放 OS 锁；不通过删除锁文件并发运行 |

正常完成后重启，用同一 session_id 发新问题即可恢复历史。执行失败或进程被终止后先 inspect；
next 非空时只用以下命令恢复，不再次提交同一用户文本：

```bash
uv run --extra v3 python scripts/langgraph_poc.py --state-dir /tmp/ecommerce-v3-demo resume demo
```

next 为空时 resume 拒绝，避免重新执行已完成请求。存在 pending 节点时 ask 拒绝，
防止新问题混入未完成轮次。错误工具参数由原生 ToolMessage 回给模型纠正；未开放工具意图
和不完整模型响应在工具节点前停止，保留失败审计。

本轮实际目录为 `/tmp/ecommerce-v3-langgraph-demo`，实验源码为
`/tmp/ecommerce-v3-langgraph-20261023`。/tmp 只保证本机本轮持久化，不是长期存储。
若移到另一业务路径，poc_threads 的绑定会拒绝复用；跨路径/跨版本迁移尚未实现，不能直接改库绕过。
仓库内 [initial_business.sql](../../results/v3_langgraph_poc_20261023/initial_business.sql)
保存烟测初始库，配套原 catalog_v2 和场景生成器可重新生成同一初始业务状态；不包含密钥或真实客户数据。

验证命令：

```bash
uv run --extra v3 pytest tests/test_langgraph_poc.py
uv run --extra v3 pytest
uv run ruff check .
uv run python scripts/check_secrets.py --all-tracked
```

真实请求只按已冻结 [smoke_plan](../../results/v3_langgraph_poc_20261023/smoke_plan.json)
运行；其十轮烟测通过与否在 REPORT 中逐项列出，不作为官方 Benchmark 或未知测试集。

## 2. 下一轮最小范围：官方 HalfDuplex Adapter 离线合同

建议维护者下一轮只授权一个独立交付：在固定 τ `dd8757d` 环境接入候选 Graph Adapter，
先不搬退货、不新增任务账本实现、不启动全量收费评测。

改动集中在 `scripts/tau_langgraph_agent.py`、Adapter 合同测试与现有评测工厂注册；
官方 Domain/policy/tool/Task/reward 完全不改。用相同脚本轨迹核对旧 Loop 与 Graph 的工具意图、
官方执行次数、工具结果 ID、结束状态及 Reward，并测试错误参数、未知写回执和进程退出。
每条脚本至少包含真正执行的官方环境路径，不能仅校验 schema 或跳过可选依赖后报通过。

Go 条件：无本地工具越过官方执行器；同条件最终状态/评分一致；异常停止有轨迹；脚本测试不冒充
真实模型能力。通过后才申请下一笔小额 paired pilot 预算，预注册相同模型/模拟器/评价器条件。
当前未实现该 Adapter，所以本轮结论是“接口机制可复用，不能直接运行候选版”。

## 3. 其后只读 Task/Fact 状态实验

由维护者在 Adapter 合同后另行授权。在新增未见的多诉求、对象更正、临时插问任务上验证，
单模型提出带原话引用的 TaskProposal，确定性 reducer 校验主体和来源，Graph 保存任务状态。
保存已答/待澄清/暂停/失败的各任务，FactRef 绑定商品/订单与版本。拒绝来源不匹配的提案，
不靠新的特殊表达替换旧规则。开发题与验证题分开冻结，漏项、错绑、错误覆盖、调用成本分别计量。

Go 条件是旧 Loop 与候选在同条件真实任务上有可复核收益；若只是状态存储更方便而质量不变，
如实只报告工程收益。未证明质量改善前不删除旧有限保护或改默认配置。

## 4. 完整退货链路迁移的模块范围

必须同时涉及 ChatService 的身份/轮次协调、SessionStore 的草稿及会话边界、ReturnService 的
预览/确认/提交/审核、ToolRegistry 的受限工具提供、Gradio 确认事件、业务回执、Graph 状态和
本地域评测。实现、故障修复、测试、文档和评测证据组成同一完整可回退交付，不能先改 UI 再补事务。

保留 ReturnService 的业务事务与幂等。新增确认桥需处理原确认失效、对象更正、材料或政策变化、
过期、重复点击、提交后 Checkpoint 失败、回执丢失、恢复时先核对真实申请。
业务写事务提交后，Graph 只登记回执引用；跨库落盘失败不能触发第二次真实写。
旧 SessionStore 只读历史导入、schema v5/v6 兼容与备份恢复须单独列出迁移验收，不能降迁移号。

测试须证明：未确认和旧确认零申请；一次有效确认恰好一条待审申请；跨 session/user 拒绝；
并发/重启/未知结果不重复写；管理员独立审核；中间 SSE 丢失不改变实际执行；混合咨询不被
退货回执覆盖。只有这些通过，才讨论一项受控实验开关。换货依旧另行定义领域事实和状态机，
不复制第二套 Runtime。

## 5. 回退与停止

本轮回退为停止 PoC 进程、切回原分支和原启动命令，原业务数据与默认 Loop 未改。
本轮没有把旧用户会话升级为 Graph，会话不需要回迁。保留实验目录和失败证据供复核。
完成交付后停止，不继续 FastAPI 部署、React、完整退货、换货、PostgreSQL/Redis 或收费大评测。
