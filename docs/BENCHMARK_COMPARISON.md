# Ecommerce-Agent-Bench 扩展与对照协议

本轮在`feat/v3-local-agent-benchmark` / `0a5d204c54cbaa753eca9bb25a859f5f2acbd13c`
上开发独立分支`feat/v3-benchmark-comparison`。原Demo、默认Runtime、历史数据和价格账本不修改。

## 数据、Gold与隔离

推荐数据：`data/eval/commerce_benchmark_v2_1`。40个合成商品、240订单、8用户、
30篇知识文档、120检索问题、60多轮任务；逻辑时钟2026-11-06 04:00 UTC，seed20261026。
商品按5个系列、每系列8个角色参数化，订单按8用户×5系列×6状态场景生成。
价格、订单金额、外键、商品明细、有效日期及原正式Fixture安全Trigger均有自动校验。
售后仍只经既有应用桥创建模拟pending_review；没有生产退款、审核或换货。

任务为12个已见业务场景族×5实例，不是60个独立人工场景。4个族20实例为development，
8个族40实例为validation；共同能力50实例，V3确认及恢复增量10实例。
Gold复用已验收M8状态/事实合同，并独立核验新实体与价格映射。
已有场景来源、固定脚本及Gold都可见，不能称为严格独立未见测试或人工盲审。

检索为80商品问题、20政策问题、12比较/多事实问题、8无直接答案问题。
按文档组划分32开发问题、88验证问题（其中80有证据、8无直接答案）。
两个集合的文档组无交叉；目录内商品使用相同角色结构和语法，仍存在模板迁移，
并且大多数问题包含精确型号/商品名。开发和验证分开报告，不能据高召回声称开放表达泛化。
全部输入运行前冻结并有逐文件哈希；Gold由原文证据片段映射到原生chunk v5。
多事实用多个必需证据组，组内是等价块。Gold不是根据检索排名生成。

原v2保留在`data/eval/commerce_benchmark_v2`：扩展时工具参数明细ID变了、聊天文本中的ID
漏映射，属于题面/Gold不一致。新增测试在v2确定失败；v2.1只修正用户文本中的实体映射，
另目录另manifest，不将旧结果改写为新版本。两版可字节重建。原v2结果不作有效模型能力指标。
修改不是提高Agent分数；明确取消失败保留，未改输入来迁就现有授权正则。

## 参考、复用与方法边界

参考[τ-Knowledge原论文v1](https://arxiv.org/html/2603.04370v1)：
把文档召回、动作、数据库任务成功与知识使用分开，Golden Retriever直接提供Gold文档用于
隔离“没找着”和“找着但没用好”。本项目文档Recall是检索到的完整chunk所关联Gold文档覆盖；
没有实现terminal/grep，也不借用其部分文档ROUGE指标。
本轮Rerank是本地Cross-Encoder，不是论文附录的LLM点式过滤，不能称为复现官方实验。
原τ² Adapter依旧锁定1.0.1/dd8757d，不引入τ-Knowledge新版领域或混合官方分数。
此前ECom-Bench研究、Persona和多意图参考见COMMERCE_BENCHMARK.md，仍不复制其客户/任务数据。

复用SCHEMA/SessionStore/ReturnService/ToolRegistry、M8 ModelTap与双Runtime执行器、
M8.1开放回答评分和配对停止规则、BM25分词/RRF、FAISS及既有HTTP/费用/私有取证。
新增代码只有数据映射、离线实验适配、本地ONNX推理及本轮Pilot入口；没有新Agent框架或业务事务。
评测适配使用进程内时钟/Fixture投影，必须单进程顺序执行，不能嵌入Demo服务线程。

## 真检索实验

`commerce_retrieval_comparison.py`实际运行：

- BM25：现有LexicalIndex。
- Dense：BGE-small-zh-v1.5中文向量，CLS池化、L2归一化、FAISS内积；查询添加官方检索指令。
- Hybrid：相同Dense/BM25各取10，现有RRF k=60，融合截取10。
- Rerank：相同Hybrid候选10，由BGE-reranker-base交叉编码并排序。
- Oracle：从Gold每个证据组取一个真实块，只有此诊断条件访问Gold；无答案时为空。

四种实际检索都使用同一语料、切块、候选10和最终上下文3，不访问答案标签。
Oracle不是可部署检索器，指标不参与模型选优。报告Recall@1/3/5/10、MRR@10、完整证据率、
文档Recall@3和p50/p95。无答案的非空上下文单列，不当成模型幻觉或自动拒答失败。
向量索引构建和模型加载单独计时；单问题Dense编码、BM25、RRF及重排耗时分别保留。
共享子步骤计时只用于同题条件比较，不能将各变体耗时相加当作实际独立请求总耗时。
本地CPU量化推理的延迟不能代表云模型或在线并发SLA。

固定模型是公开BGE权重的Xenova ONNX INT8转换，不是随机向量或Mock：

|用途|仓库|固定revision|
|---|---|---|
|Dense|Xenova/bge-small-zh-v1.5|75c43b069aac4d136ba6bc1122f995fedcfd2781|
|Rerank|Xenova/bge-reranker-base|280bcc27a84e0b898c251e06fddb25171bd9b101|

原始[中文Embedding模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)和
[Reranker模型卡](https://huggingface.co/BAAI/bge-reranker-base)为MIT。
报告包含本次实际ONNX/tokenizer哈希；下载预备阶段可以联网，实验推理本身禁止联网。
安装在独立临时环境，未修改默认Python依赖或Demo索引；大体积权重和索引不入Git。
首个推理预备曾因CLS视图非连续触发FAISS异常，保留失败日志，显式连续float32后复验；
失败过程不算有效召回实验，也没有伪造缺失结果。

Oracle Evidence对最终回答/业务动作的后续实验，应冻结检索条件并使用相同任务、模型参数、
工具权限、上下文/输出上限；只将search工具返回值换为Gold块作为特权诊断。
若Oracle任务仍失败，才能据真实轨迹研究知识应用；本轮只有证据上界，未给出Oracle模型成功率。

## Agent公平对照与语义审查

共同能力：同一初始SQLite、用户输入、模型投影Prompt/Schema、16次Episode模型决定、
24工具、600秒Episode、相同SQL与回答评分。旧Loop的内层保护保留但在这些冻结轨迹未提前触发。
M8的ModelTap将身份事实从SQL动态投影，两臂规范化消息、工具及DB终态都实际比较。
每对独立数据库；不同模型自然决策允许不同安全路径，不要求参考调用路径相同。
工具副作用和权威回执优先于Graph镜像；安全拒绝仍需检查工具选择是否适当。

离线模型是原生BaseChatModel固定决定，仅作为业务执行控制。不能把45/50当作模型任务成功率。
50共同配对与10个V3应用增量分开，不能把旧Loop没有的Interrupt算作胜场。
原文与Grounding展示分别评价。精确已标注控制可程序裁决，真实开放表达未核验时
是pending_review；SQL正确不能替代语义完整性或事实性。独立盲审未完成前，不发布真实语义成功率。
工具调用、模型决定/HTTP、Token、延迟和成本各自记录；固定决定无Token用量，填null。

首个磁盘版大批离线运行因本机fsync延迟主动中断，保留partial日志，不混入完整分母。
完整离线对照在独立tmpfs Fixture执行，然后将证据复制回持久目录；两臂使用同一存储。
因此本地延迟只描述该实验环境，不能外推性能或生产可靠性。

## 动态用户模拟

`BoundedUser`是可运行的受约束状态机：只读取公开客服回答，先要求列候选、看到目标订单后选择，
收到业务状态后结束；错误/缺失回复触发追问，最多5轮。目标订单是用户自身意图，
不读取Gold、SQL、ToolMessage、臂名或评分。单元反例覆盖没列目标、追问及到达耐心上限。
`--mode simulator`让该用户实际驱动V3和3次真实业务工具，Agent决定仍固定，因此不称为
LLM Persona质量或真实Agent成功率。固定多轮60题仍单列，不悄悄替换成动态任务。

后续LLM用户可以在这个边界替换文字生成，但应保存用户目标、已披露槽位、最大轮数和允许动作，
用同一策略/seed配对，禁止得到Gold/SQL/Runtime身份；模拟器HTTP和Judge计入总账。
模拟器因代理回答而分叉是有效交互差异，不能强制给两臂同一错误追问再称自然用户实验。
本轮小Pilot沿用固定输入，先消除动态用户造成的额外混杂。

## 真实DeepSeek Pilot门禁

选EC02-1（多候选/物流）、EC03-1（商品咨询+部分写入），各old→V3一次，共4Episode。
这是开发连通性，选中的验证族在此之后也成为已见数据，不包装为未见测试。
参数：deepseek-flash、temperature0、非思考、最大输出1024；每Episode8次HTTP、24工具、
600秒；全局40HTTP/5元，先到先停，不重跑。请求正文32768字节。
保守每请求输入上界2×32768+4096 Token；按峰值未命中2元/百万输入和8元/百万输出，
32次计划上界4.718592元；未知/缺usage不免费，保留原EpisodeLedger非退款预留机制。
保守上界不是预言真实Token；当前实时官方价格、模型别名及中英文快照必须重新核实。

本轮用户明确允许必要付费API，执行只限上述4Episode、自设5元/40HTTP，授权24小时，
绑定本轮源码SHA、数据哈希、绝对输出路径、唯一run_id，不能复用旧手动聊天授权。
源码/价格/身份不匹配、未知业务写入、不可核实费用或越权全局停止。
普通安全模型失败继续配对另一臂。SDK MockTransport预演先执行同样序列和真实SQLite，
不把合成usage当真实费用；预演通过和完整离线检查完成后才启用付费。
已失败Episode不重跑，不清空账本；用户原有无关修改保留，付费前只要求相关源码已提交且哈希匹配。

## 复现

```bash
uv run python scripts/eval_commerce_comparison.py --mode verify
uv run python scripts/eval_commerce_comparison.py --mode build --out /tmp/commerce-v2-rebuild
uv run python scripts/eval_commerce_comparison.py --mode agents --out /tmp/commerce-v2-agents-new
uv run python scripts/eval_commerce_comparison.py --mode simulator --out /tmp/commerce-v2-user-new
uv run python scripts/eval_commerce_pilot.py --mode rehearsal --out /tmp/commerce-v2-sdk-new
```

检索可选环境：

```bash
uv venv /tmp/commerce-model-env-new --python .venv/bin/python --system-site-packages
uv pip install --python /tmp/commerce-model-env-new/bin/python -r requirements/benchmark-local-models.txt
# 让隔离环境复用已锁定的项目Runtime，不升级或复制其依赖。
.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))' > /tmp/commerce-model-env-new/lib/python3.13/site-packages/project-runtime.pth
uv run python scripts/prepare_benchmark_models.py --out /tmp/commerce-model-weights-new
/tmp/commerce-model-env-new/bin/python scripts/eval_commerce_comparison.py --mode retrieval --model-root /tmp/commerce-model-weights-new --out /tmp/commerce-v2-rag-new
```

Python小版本目录应与实际环境一致（本次3.13）。每个输出目录必须新建，拒绝覆盖。
公开结果仅合成统计与必要证据，原始SQL/Graph/Prompt/usage放私有目录且不提交。
本阶段完成后停止，不自动优化取消规则、切换默认检索器、扩大付费或正式Benchmark。
