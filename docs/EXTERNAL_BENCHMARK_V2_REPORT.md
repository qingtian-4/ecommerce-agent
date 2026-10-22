# 电商客服 Agent 外部基准验收

2026-10-22。独立分支 `feat/external-benchmark-v2`，V1=`f34981f`，
V2=`f6c3387`。本轮只修改评测桥接、独立运行入口、测试和交付文档，不修改产品 Runtime、
ChatService、业务事务、依赖锁或默认开关。不合并 PR #4、不改变仓库可见性。

## 选择依据与源码边界

| 维度 | τ²-bench Retail | ECom-Bench |
| --- | --- | --- |
| 固定源码 | [dd8757d](https://github.com/sierra-research/tau2-bench/tree/dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd)，沿用历史 pin | [bc5018d](https://github.com/XiaoduoAILab/ECom-Bench/tree/bc5018daaf45eea12330941bce68cebd293cfa85)，本轮只静态读取 |
| 公开任务 | Retail base=114；train=74、test=40，官方英文任务 | 公开 `envs/story/tasks.py` 有 53 个 Task |
| Loop 复用 | 已有 HalfDuplexAgent 桥接；本轮修复并复用真实 Loop | 需新建异步 MCP/状态桥接，目前未做 |
| V2 可测 | 实际 Router、请求前有限 Recovery、预算和停止条件 | 理论可接 Runtime；不能因中文任务就假定 Router 正确覆盖 |
| 不可测 | ChatService、RAG、本地 SQL、页面确认、P0/P1、颜色/聚合、崩溃恢复 | 同样不能自然覆盖本地业务协议，需要另行设计适配 |
| 官方业务和评分 | 保留官方数据、政策、工具、用户模拟器、ALL 评分和 strict replay | MCP/JSON 状态；action×search×output，time 单列；字符串命中不等于事实正确 |
| 额外业务 | 修改地址/换货等由官方环境执行，不代表本店新增这些能力 | 安装、发票、电子卡等大量产品 Scope 外能力，不能为得分补齐 |
| 参考 Agent | 官方 LLMAgent 可同环境、同模型对照 | 官方示例可参考，但对接工作量及缺失能力更多 |
| 成本与价值 | 复用既有协议，三题三臂 Pilot 可小预算验证 | 另做完整 Adapter 成本更高，无证据支持本轮同时建设 |

`routing.SCOPES` 白名单使用 `get_order/update_return_task` 等本地工具名，
与 ECom 的 `manage_order/manage_return` 不同；直接输入中文任务也可能选出空工具集，
需要另行做协议与范围验证，不能假定切换框架就能覆盖 V2。

选择 **τ Retail**。上游当前 HEAD 为 `05736c2`，与历史 pin 的 Retail 任务和 split 相同，
但工具/环境/LLM/评分源码有变化。本轮不升级，差异留档于
[upstream_comparison](../results/external_benchmark_v2_20261022/upstream_comparison.json)。
ECom 的实际接口、评分源码及工作量详见[静态兼容性分析](ECOM_BENCH_COMPATIBILITY.md)；
该旧文价格和调用数是当时假设，不是本轮实付或新的报价。

## 实际适配与必要修复

[`tau_acs_agent.py`](../scripts/tau_acs_agent.py)暂停真实 ACS Loop，向官方 Orchestrator 发出
工具请求，收到官方 ToolMessage 后再恢复生成器；不伪造业务回执。
新增 [`eval_tau_v2.py`](../scripts/eval_tau_v2.py) 独立入口：

- Official 使用官方 LLMAgent；V1 从实际 `f34981f` 源码独立命名空间加载，逐文件核对 Git；
  V2 使用 `f6c3387` 的实际 Loop，只开启 Router 和 Runtime Recovery。
- 统一官方 system prompt、政策、工具、模拟用户和裁判模型，temperature=0、thinking=false、
  输出上限 768、请求超时 45 秒、Orchestrator 100 步/10 错误/240 秒。
- ACS 每用户轮 8 步、16 工具、12 请求、120 秒，输入字符上限 64,000；官方 Agent 没有相同
  内部截断。ACS 流式、官方非流式，不能当作严格等预算或严格延迟因果实验。
- 所有真实请求（Agent/用户/裁判）经过同一逐请求费用预留；SDK 和 LiteLLM 隐式重试关闭。
  官方 generate、用户行为、消息解析、环境执行、评分逻辑保持原实现，只替换 provider-call
  seam 做统一计费。失败 usage 不明按完整预留扣账并停止，不靠缓存折扣保证预算。

真实红测试复现三项 Adapter 缺陷：Registry 缺工具名称导致 Router 不能运行；Loop 先公布
调用意图、后执行范围检查，桥接可能提前把被拒绝工具送到官方环境；字符串错误回执与
Runtime 的字典处理不兼容。修复限于桥接：暴露真实工具名、出口先核对实际 route、错误字符串
归一化。**未修改权限或为基准放宽业务规则**。旧入口和历史结果保留。

[红测试](../results/external_benchmark_v2_20261022/bridge_real_red.log)为 3 failed、1 passed，
[桥接绿测试](../results/external_benchmark_v2_20261022/bridge_final_green.log)为 4 passed。
首次环境缺 `websockets` 和一次出口检查实现失误也保留日志，不冒充有效通过。

## 冻结与无网络预演

[冻结协议](../results/external_benchmark_v2_20261022/protocol_v1.json)记录完整任务、源文件哈希、
上游工具/政策/评分哈希、模型端点、依赖版本、预算、种子 20261022、一次 trial 和失败规则。
模型是供应商可变别名 `deepseek-flash`，温度 0 不保证确定性；种子仅作用于官方环境。

预先固定 test split 的 17（待处理订单地址修改）、51（纠正错误订单后退货）、68（身份信息
更正及付款金额查询），按题轮换执行臂顺序。三题均在既有完整历史运行范围内，**不是未见留出集**。
冻结后不改题、不改评分、不挑选重试；启动记录先落盘，已存在输出目录拒绝复跑。

[无网络预演](../results/external_benchmark_v2_20261022/preflight_v1/summary.json)：
三题三臂 9 段官方 DB/评分回放全部通过；漏掉最终写入的负例 reward=0；一次请求前人工
503 注错，V1 reward=0、V2 reward=1，实际经过 `retry_scheduled`。
预演使用官方 gold action 驱动脚本模型，仅验证线缆、真实环境写入和评分读取；
**不作为 Agent 质量成绩，在线 Agent 不读取 gold action 作答**。

隔离外部环境安装 pinned τ1.0.1，依赖版本完整留在
[external-requirements](../results/external_benchmark_v2_20261022/external-requirements.txt)。
LiteLLM/OpenAI 等与历史环境有版本差异，故本轮与历史分数分开报告。项目 `uv.lock` 未改。
三臂初始数据库隔离，DB 哈希及官方评分细项均保留。Socket 被禁止时预演仍通过。

## 真实 Pilot 与费用

用户单独授权 **总计不超过人民币 5 元**。本轮三题×三臂×一次，不追加 trial。
结果及请求明细见下文“已完成 Pilot 结果”及独立 `pilot_v1/analysis.json`。

预算保护价为输入 4 元/百万、输出 16 元/百万，忽略缓存优惠；按序列化 UTF-8 请求字节+
4096 framing 预留输入和最大输出，合计最多 180 次请求。超过预算则停止、保留未完成。
该保护价高于查询时[DeepSeek 官方峰时未缓存价格](https://api-docs.deepseek.com/quick_start/pricing/)
按保守 10 元/USD 换算；配置价格估算、保守记账和供应商实付三者不能混用。

## 复现方式

普通项目环境保持 `uv sync --extra dev --frozen`，可选外部测试缺 τ 时跳过。
外部评测需单独 Python 3.13 环境，安装项目锁定基础依赖、上游固定提交及
`websockets==16.0`，本轮安装日志保留。不要把临时绝对路径当作可移植项目路径。

```bash
# 新建独立目录/环境；从仓库根目录操作
# tau-src 必须 git checkout dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd
git clone https://github.com/sierra-research/tau2-bench.git /tmp/tau-src
git -C /tmp/tau-src checkout dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd
uv venv /tmp/tau-env --python 3.13
uv export --frozen --extra dev --no-emit-project --no-hashes \
  --format requirements-txt -o /tmp/acs-base.txt
uv pip install --python /tmp/tau-env/bin/python -r /tmp/acs-base.txt \
  -e /tmp/tau-src 'litellm==1.82.6' 'websockets==16.0'
# 激活该隔离环境后，下文 python 指 /tmp/tau-env/bin/python
source /tmp/tau-env/bin/activate
mkdir -p /tmp/acs-v1
# 实际 V1，不是只关闭 V2 开关冒充版本
git archive f34981fa1324310a9d0f0de4430fe1980ba664e9 src | tar -x -C /tmp/acs-v1
export TAU2_DATA_DIR=/绝对路径/tau-src/data
# 下文 python 指单独外部环境中的解释器，不是项目 .venv
python scripts/eval_tau_v2.py --upstream /绝对路径/tau-src \
  --v1-root /tmp/acs-v1 --protocol /新目录/protocol.json --freeze
python scripts/eval_tau_v2.py --upstream /绝对路径/tau-src \
  --v1-root /tmp/acs-v1 --protocol /新目录/protocol.json --out /新目录/preflight
python -m pytest tests/test_tau_bridge.py tests/test_external_tau_protocol.py
```

`--live --preflight /新目录/preflight --out /新目录/live` 才会付费；本轮授权只用于现有 Pilot，
复制命令不代表授权新的实验。`analyze_tau_v2.py /输出目录` 只读取记录并生成分析，不调用模型。

## PR #4 审阅导航与交付边界

PR #4 仍为 `main ← feat/runtime-v2@f6c3387`，Draft、MERGEABLE，已通过 CI；
独立评测分支建立在它之上，建议顺序是用户审阅并决定 PR #4，再审阅评测分支。未经授权不合并。

| 模块 | 源码/证据 | 关注点 |
| --- | --- | --- |
| Runtime | [service_agent](../src/acs/service_agent.py)、[runtime](../src/acs/runtime.py) | 工具配对、整轮预算、停止条件 |
| Router | [routing](../src/acs/routing.py) | 轻量路径与允许工具；英文不做专用规则 |
| Recovery | [recovery](../src/acs/recovery.py)、service_agent | 请求前有限重试；不盲目重放业务写入 |
| SQLite v6 | [sessions](../src/acs/sessions.py)、[premerge](../results/v2_premerge_20261022/REPORT.md) | 新表迁移、启动核对、备份回滚；不是中间节点续跑 |
| ChatService/UI | [chat_service](../src/acs/chat_service.py)、[app](../app.py) | 默认关闭、客户进度不暴露隐藏 CoT；本次外部未覆盖 |
| 测试 | [test_v2_premerge](../tests/test_v2_premerge.py)、[桥接测试](../tests/test_tau_bridge.py) | 生产安全回归与独立适配有效性分别验收 |
| Benchmark | [本轮记录](../results/external_benchmark_v2_20261022/)、[历史τ](../results/iteration_v2_recovery/tau_base/) | 两轮不可合并计算分数 |

本轮停止于 Pilot 和正式实验预算建议，不运行 40 题正式组或 684 段重跑。

## 已完成 Pilot 结果

[原始轨迹与官方评分](../results/external_benchmark_v2_20261022/pilot_v1/) ·
[逐例分析](../results/external_benchmark_v2_20261022/pilot_v1/analysis.json) ·
[全请求账本](../results/external_benchmark_v2_20261022/pilot_v1/requests.json)。
九段全部执行并计分，无接口失败、超时、未判定或选择性重试。

| 指标（三题合计，每臂一次） | Official | ACS V1 | ACS V2 |
| --- | ---: | ---: | ---: |
| 官方 reward=1 / DB 正确 | 3/3 | 3/3 | 3/3 |
| pass^1（该固定子集） | 100% | 100% | 100% |
| pass^3 | 未测 | 未测 | 未测 |
| 真实模型请求，含用户/裁判 | 46 | 44 | 42 |
| 输入 Token | 171,336 | 167,626 | 155,322 |
| 输出 Token | 3,137 | 3,203 | 2,988 |
| 实际工具调用 / 工具错误 | 16 / 1 | 16 / 1 | 16 / 1 |
| Route / fallback / 重试 | 0 / 0 / 0 | 0 / 0 / 0 | 13 / 13 / 0 |
| 完整 episode 延迟 p50（秒） | 16.60 | 18.44 | 14.59 |
| 保守费用记账（元） | 0.735536 | 0.721752 | 0.669096 |
| 配置价估算（元） | 0.065005 | 0.064604 | 0.062184 |

共 **132 请求、503,612 Token**，其中 Agent=78、模拟用户=51、裁判=3。
保守账本 **2.126384 元**；按供应商实际 usage、缓存及项目配置价格估算 **0.191793 元**，
不是供应商账单。无缺失 usage、无未定价调用；账本含所有请求且停止追加。
官方对象中的 cost=0 源于其对该模型的本地价格映射未知，**不代表免费**，费用以独立账本统计。
三臂初始数据库 hash 相同，每段各自隔离；地址写入及退货写入每段各一次，查询题无业务写入。

三臂共同的工具错误均来自 task68 按用户初始错误 ZIP 查询不到身份，之后正确更正并完成。
这属于任务刻意设置的业务情境，不能计作 API 故障。task68 的付款金额回答由官方 NL 裁判
各一次检查；task17/51 的主验收为 DB 比对，action_checks 中部分读工具不匹配不影响官方
DB/NL reward，不能私自改用“所有工具与 gold 完全相同”评分。

**V1/V2 逐例质量无差异；本轮没有一般任务质量增益证据。** V2 少 2 请求、少约 7.34% 输入
Token，但所有 route 均 fallback 且工具集相同，不能归因为 Router 缩小了 schema。
模拟用户、采样和多轮表达不同，无独立析因；耗时也不能据三题推断稳定提速。
没有真实请求失败，所以线上未验证 Recovery；无网络预演的一次 503 只能证明对应受控恢复。

### 保留的测量局限与问题

1. **外部协议诊断误报**：V2 有 5 次 `invalid_result`。源码
   [`recovery.classify_failure`](../src/acs/recovery.py)把本地 `{orders,count}` 或字典约定
   用于官方的合法订单 ID 列表/字符串，产生诊断噪声；本轮未阻断执行、未自动重试或改变分数。
   不修改 Runtime/官方数据以抹除结果。若目标是准确测量外部工具错误率，下一步应先设计
   Adapter 结果类别映射并做红绿回放，再决定是否扩大实验。
2. **外部写入恢复边界**：`recovery.WRITES` 是本地工具名，官方修改/退货名称不在其中。
   官方工具由 Orchestrator 执行，无法将本地写回执核对或崩溃恢复成果外推到这些操作。
   本轮没有注入官方写后响应丢失，也没有重放官方写操作，不声称证明外部写入恢复安全。
3. **覆盖不足**：仅三题、每臂一次且全部成功，无广泛混合/长轮任务统计，没有 pass^3。
   单模型别名、流式差异、上下文和内部预算差异仍在。最终成功不证明每句自由文本都准确。
4. **轨迹范围**：ACS Trace 绑定在完成的 assistant 消息；若中途退出，只保留 partial trajectory、
   活跃 Loop history 和请求账本，不能假定尚未输出的 Trace 事件不存在。
5. **环境兼容**：Tau 的顶层导入带语音依赖，即使本轮只运行文本也需额外 websockets；
   保留隔离环境，不扩大应用依赖。安装镜像失败、环境失败及修复尝试均留档。

## 与历史证据分开解释

| 实验 | 结果 | 适用边界 |
| --- | --- | --- |
| 历史完整 Retail base，114×3×2=684 段 | Official pass^1/pass^3=87.4%/75.4%，ACS=86.5%/78.9% | 原模型/预算/依赖协议；不是本轮 V2；不重评分 |
| V1 本地 DeepSeek，11 已见题每臂一次 | 6/11→11/11 | 本地事实保护，不能与官方 benchmark 相加 |
| V2 本地 DeepSeek，8 自然+2 受控故障 | 自然8/8→8/8，受控503为0/2→2/2 | 真实 ChatService，故障由人工注入；不能代表故障发生概率 |
| 本轮官方三题 Pilot，3×1×3=9 段 | Official/V1/V2 均3/3 | 真模型、官方数据/工具/评分；已见固定子集，非完整新成绩 |
| 本轮脚本预演，9自然+3负例/故障 | 自然全通、漏写失败、V2请求前重试有效 | 脚本动作/固定裁判，只验证工程接线 |

**结论 A**：历史完整同模型对照支持“自研 Loop 在这一 Retail 协议下接近官方 LLMAgent”，
不支持“优于主流框架”或 SOTA。本轮三题只补充实际 V1/V2 接线与无明显执行回归证据，
不能提升对通用竞争力的置信度。与 LangGraph 等通用编排框架相比，本项目的辨识度是
可追溯失败、事务/授权、逐帧保护、独立成本统计和诚实的评测边界，不是架构名称或排行榜。
系统是有限本地客服业务加可复用 Runtime，不是完整通用电商执行平台。

**结论 B**：保留有离线/本地证据的有限 Router、请求前 Recovery 与业务保护，但仍默认关闭。
Router 在外部英文任务没有收窄覆盖；Recovery 的证据主要是受控 503；ChatService 业务机制
本轮不可测。应优先处理外部结果分类/副作用标注与新故障留出任务，前提是确有扩评需求，
不要为官方订单修改、换货等工具增加本地产品能力，不继续无边界建设框架。

## 正式评测建议与停止决定

正式方案仅供审阅：固定同一上游 test=40、三臂、各三次，共 **360 段**，
预期约 **5,280 请求、20,144,480 Token**。按本次配置价估算线性外推约 **7.67 元**，
按不含缓存优惠的保守记账外推 **85.06 元**，再加 25% 假设余量约 **106.32 元**。
三题不代表全部复杂度，25% 不是硬上界；正式必须重新授权独立硬限额、冻结所有任务与版本。
预算用完则保留所有尝试并报告未完成，不能选择性补成功。

**本轮不运行正式组**：诊断误报尚未适配，且 Pilot 无自然任务增益，追加大规模费用的价值有限。
Go：独立评测代码、接线预演、固定 Pilot 和项目讲解材料交付。
No-Go：宣布 V2 一般质量提升、默认启用、无条件生产部署或未经授权扩评。
推荐先按既有 V1/V2 Demo 准备实习面试；PR #4 是否合并由维护者单独决定。

## 展示与维护状态

本轮读回 GitHub 的现有 visibility=PUBLIC，未执行可见性变更；不把已有公开状态说成本轮发布。
新增官方任务/政策快照附 `results/external_benchmark_v2_20261022/TAU_LICENSE.txt`，
来源为 Sierra Research 的公开合成基准，不是本店真实客户数据。
新增结果通过密钥扫描并核对当前 Key 未出现在证据中，但自动扫描不能代替人工隐私复核。
项目根许可证仍未确定，适合展示源码、运行和面试讲解，不能声称已经完成全部开源许可。
[公开准备事项](PUBLICATION_READINESS.md)继续有效。

Runtime/业务默认配置不变，既有五分钟[Demo](DEMO.md)沿用；本轮不新增页面，
不把脚本接线预演伪装为在线演示。推荐停止功能扩展，维护已验证的有限路径，准备技术面试。

## 最终工程验证

本轮真正clean工作目录（f6c3387加本轮五个代码/测试文件）完整回归
**813 passed、1 skipped、4 warnings，624.73秒**，
[原日志](../results/external_benchmark_v2_20261022/pytest_clean_worktree.log)。
跳过的是普通环境未安装的可选τ模块；该模块在独立外部环境实际运行通过，不以跳过代替验证。
Ruff、密钥扫描和git diff --check通过；1097份历史结果及三份用户WIP哈希均一致，
产品源码、默认配置和uv.lock无变化，付费期间源码与冻结协议一致。
本轮源码提交/远程CI结果在最终交付回复提供；不能用PR4旧CI代替评测分支的新CI。
