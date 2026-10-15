# 多轮任务评测与外部对照

本轮固定80段本地任务，8类各10段，每段4–12轮；前5段开发、后5段留出，合计
40段开发各运行1次、40段留出各运行3次。任务组合包含商品限制更正、多商品澄清、
7/15天边界、凭证缺失与上传、更正与暂停、旧确认失效、提交重试与响应丢失、
审核和跨角色拒绝，以及知识资料中的恶意命令。

这些是Codex基于合成数据编写的固定用户脚本，不是采集的客户语料、独立人工编写的
任务或独立模型用户，也不是严格盲测。冻结JSON的`human_authored_script`是错误的旧
来源标签，实际来源以`results/multiturn_v1/metadata_erratum.json`为准；保留冻结字节，
没有为了纠正元数据改写问题、判据或已见结果。原始用例、
判据与独立业务快照在运行前冻结；现在已看到结果，后续改进只能将其作为回归集。
不能继续称为新一轮未见留出。任务不只是替换订单号：开发与留出有不同限制、状态、
操作组合和措辞；语料仍由同一作者设计，不能证明真实客户分布上的泛化。

## 执行与判据

真实模型通过与页面相同的`ChatService`、Agent Loop、业务工具和SQLite任务存储执行。
每次任务使用独立数据库副本；不复用前一任务的草稿、审核或历史。上传、确认和审核
是脚本参与方调用真实后台处理器的明确操作，不是让模型输出一行文本冒充按钮。
这里验证服务层处理链路，没有声称80段都驱动浏览器DOM。

确认、审核及上传操作仍记录为会话中的可信事件。模型看不到期望结果和评分提示；
评分角色可以看到版本化政策、完整订单与物流事实、事件顺序、工具结果及逐轮任务状态。
语义评分器与客服模型使用同一供应商、不同提示与调用；它不是独立人工审核。

任务自动通过要求同时满足：

1. 必要的订单/明细/数量/原因与终态正确，正向任务确实产生所需申请。
2. 原业务表和已有申请不被修改；聊天不能创建申请，未经授权、过期或失效的确认不能写入。
3. 新申请身份、数量、审核终态正确；重试与同确认并发仍只有一个申请。
4. 回复没有错误事实、虚假完成承诺或重复追问已知必要信息。

随机申请号按会话和引用关系检查，不与固定UUID比较。明确分开SQL约束与回答质量：
数据库正确仍可能说错期限或声称退款，工具调用正确也不能单独算成功。

失败分类为`runtime`、`permission`、`confirmation`、`quantity`、`state_transition`、
`memory`、`grounding`、`false_completion`、`clarification`、`unsupported`和`judge_error`。
标签允许重叠。未完成应提交任务与发生禁止写入分开检查；`judge_error`是未判定，
主表保守计未通过，不能称为已经确认的Agent业务错误。

三次均成功比例是每段3次全部通过，不是至少通过一次。85%平均成功和75%三次均成功
是原规划目标，未达到时报告真实结果，不改题或放宽判据。耗时来自并行评测环境，
不作生产SLA；成本沿用请求时配置价格，之前调试、校准和外部评测消耗另行区分。

“重启”任务重建服务对象检查持久状态；真实容器停止、删除重建及恢复另有容器证据。
“响应丢失”明确丢弃事务提交后的返回值再重试，不能夸大为任意崩溃点都可恢复。

## 评分器也需要验证

初始20个成对样例中误放过1个错误政策回答，保留于`calibration_first`。
引用原文的校准随后通过，却在完整对话发现事实缺失和时间顺序误判：只给一条目标
明细不等于完整订单，上传之后的状态不能用于批评上传之前的回复；真实确认之后
可以说已创建申请，仍不能说已退款。

因此保留所有原始模型回答、原SQL检查和原评分，补完整订单、物流、政策与有顺序的
工具/可信操作证据，再对同一批原始回答评分。完整开发对话及成对变异也纳入校准，
而不是只用短句判断长对话的评分可靠性。评分配置、消耗和源码hash单独保存。
这是测量修正，不是客服Agent的成功率提升，也没有新增被测Agent调用。

最终本地语义评分启用现有模型的推理模式，34个校准样例一致33个、误接受0个、
误拒1个；仍可能误判，不能当作人工真值。该模式下供应商不保证温度参数生效，
不宣称评分确定性。隐藏推理正文不展示或保存，只记录用量与推理字符统计。
最终报告保守记录未判定，并保留所有原文供复核。

本轮开发自动严格通过33/40（82.5%），留出107/120（89.2%），留出三次均通过
30/40（75%）。全部160次中有15次语义评分未判定，已保守计未通过，不能直接写为
已确认的客服失败。正向应创建申请28/28、负向不创建132/132；业务表与已有申请
未发生禁止变化。确定性全部通过157/160，仍有1次已选订单未保存和2次运行未正常完成。
后者与回答政策、日期解释以及裁判未判定分别记录，不混成一个“Agent成功”指标。
同一批原始回答重评分115→140次自动通过，只是评分输入与模式修正，**不是能力提升**。

## 复现

```bash
# 首次生成新套件；已有目录拒绝覆盖
uv run python scripts/build_task_suite.py --out data/eval/my_suite
# 已提交的v1用例只重新生成被忽略的独立业务快照，并核对冻结hash
uv run python scripts/build_task_suite.py --materialize
uv run python scripts/calibrate_task_judge.py --cases data/eval/task_judge_pairs_v3.json \
  --out results/new_task_calibration
uv run python scripts/eval_multiturn.py --out results/new_tasks \
  --calibration results/new_task_calibration/summary.json
```

`--split development/holdout`可分开执行；`--ids`和`--trials`只用于明确标注的开发诊断。
正式协议使用默认的开发1次/留出3次。输出目录独占创建，不覆盖失败尝试。

## 外部原版对照

固定[sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench/tree/dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd)，
版本1.0.1；零结果筛选规则为retail/base按任务数字ID排序取前5个，即0–4。
两个方案各独立3次，种子20261015–20261017，共30次。客服、用户模拟器和官方NL裁判
均明确配置为现有DeepSeek模型，温度0；保留原始环境、工具、政策、断言和评分实现。
这不是官方排行榜配置，不能与排行榜模型数字直接比较。

项目方案通过`tau_acs_agent.py`在实际`CustomerServiceAgent.chat_stream`的工具调用处
暂停，将工具请求交给官方orchestrator执行，再将真实ToolMessage回送原Loop继续。
没有让适配器自己执行工具，也没有只给官方Agent换模型。官方LLMAgent使用同一模型
作为对照。适配器不接收评分答案；任务初始化历史与官方问候正常保留。

外部政策/工具的固定输入更大，适配器明确使用64,000字符输入限额，其他ACS轮次预算
沿用配置；本地应用仍为24,000。官方对照没有相同的ACS限额，不能将差异单归因于
推理算法。这里验证通用工具循环，不验证本地SQL权限、确认入口、RAG或页面。

上游文本安装仍导入部分语音模块；适配环境补齐导入依赖，没有修改上游源码或锁文件。
初次接入中，适配器拒绝官方初始化问候，且额外开启协议强制检查使官方方案提前停止。
该失败批次保留于`results/tau_retail/original`，属于接入错误，不能当作有效能力基线。
修复后恢复官方默认协议配置，冻结第二份清单，完整运行两个方案全部任务及重复次数。

有效结果见`results/tau_retail/current`：项目14/15、三次均成功4/5；官方对照15/15、
三次均成功5/5。项目在任务4第1次将10个可用变体说成11个，尽管数据库终态正确，
原NL断言仍使整项失败。没有修改评分或重跑选好成绩。上游未识别该模型价格，
其cost=0不能当作免费，外部成本统计不完整。

```bash
git clone https://github.com/sierra-research/tau2-bench /tmp/acs-tau
git -C /tmp/acs-tau checkout dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd
uv sync --frozen --no-dev --project /tmp/acs-tau
uv pip install --python /tmp/acs-tau/.venv/bin/python \
  pydantic-settings pillow faiss-cpu jieba rank-bm25 websockets scipy soundfile
PYTHONPATH=src:scripts /tmp/acs-tau/.venv/bin/python scripts/eval_tau_retail.py \
  --upstream /tmp/acs-tau --manifest data/eval/tau_retail_subset_v2.json \
  --out results/new_tau_comparison
```

凭据仅在当前进程环境注入，结果配置不保存密钥。上游Agent、环境与裁判使用同一
固定版本；NL裁判模型是已记录的配置选择，没有替换原评分提示或reward_basis。
本轮未额外适配ECom-Bench，也不将本地改编任务称为官方原版成绩。
