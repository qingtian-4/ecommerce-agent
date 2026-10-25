# M8.2 真实 DeepSeek 配对 Pilot

本批四个 Episode 均已结束：**17 次真实 HTTP、16 次本地工具执行**，没有失败重跑、
额外模型试探或扩大范围。保守累计预留 **1.344324 元**；按供应商 usage 与实际时段估算
**0.032894920 元**，峰值口径估算 0.06578984 元。没有读取账户扣款账单，不能把估算当账单。

观察到 D04 业务状态差异：旧 Loop 保存了正确草稿，V3 要求额外澄清而没有请求保存。
本批不支持“V3 更可靠”的结论，也不能把一次差异归因为 Runtime。

## 执行身份与边界

- 实际执行 SHA：`b5356662fdc63c9649d4cdf1c57c6a0e3254ece9`，启动时工作区干净。
  交付分支 `results/v3-m82-deepseek-pilot`；后续仅增加结果与只读分析，没有改 Runtime。
- 合同 `m8.1-open-answer-v3`，D02/D04 为已见合成开发任务，不是独立留出集或官方 τ² 分数。
- 用户本次明确授权原文与完整许可私存，范围仅本批：5 元、40 HTTP、8/Episode、
  1,024 输出 Token、32,768 字节请求正文，每臂一次，失败不重跑。
- 准备开始北京时间 2026-10-26 00:10:57，授权到 2026-10-27 00:10:57 截止。
  实际批次 2026-10-26 00:15:46 至 00:16:50。运行已结束，不继续使用剩余额度。
- 冻结业务逻辑时钟仍为 2026-10-25 04:00 UTC；它与实际计费时钟分开，未按运行日期改题。
- 参数统一：`deepseek-flash`、temperature=0、thinking disabled、输出上限 1,024、
  非流式、SDK retries=0，模型超时 60 秒，Episode 截止 600 秒。
- 中英文官方价格页重新核验并保存哈希，四臂开始前还通过原 `verify_price` 检查。
  [官方人民币文档](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)与
  [美元文档](https://api-docs.deepseek.com/quick_start/pricing/)的 Flash 峰值单价与冻结 PRICE 相同。
  文档映射 DeepSeek-V4.1-Flash-1017，17 个实际响应的 model 均为 deepseek-flash；
  供应商内部权重版本不能独立验证。

## 逐题结果

| 任务/臂 | 执行结束 | SQL 正确 | 冻结 Tool Validity | 冻结任务合同 | 原文/展示语义 |
|---|---|---|---|---|---|
| D02 old | 是 | 是 | 否 | fail | pending_review |
| D02 V3 | 是 | 是 | 否 | fail | pending_review |
| D04 old | 是 | 是 | 否 | fail | pending_review |
| D04 V3 | 是 | 否 | 否 | fail | pending_review |

两对执行完整，无缺臂。SQL State Correctness：old 2/2，V3 1/2。冻结 Tool Execution
Validity 两臂均 0/2；原始工具调用均被合法执行、参数与权威回执相匹配，其失败来自
**缺少冻结规则指定的调用**。没有把正常执行、SQL 正确和完整回答混为一个成功指标。

任务合同四条失败可由 SQL/必需工具条件确定；自然语言没有独立裁决，仍待审。
Raw/Display Faithfulness、完整语义目标覆盖率、纠错率、误纠正率和漏检率均不作成功声明。

**保留原始字段的解释**：原 Runner 的 `pairs.semantic_status=resolved` 由整体任务
outcome 是否 pass/fail 推出，包含工具/SQL 失败，不能解释成语言已经审完。同样
`raw_outcome=fail` 不是 Raw Answer Faithfulness 的判定。本轮不修改冻结 Runner 或原分数，
另在 `paired_audit_scorecard.json` 明确标出语言已审 0、待审 16。原始 Scorecard 原样保留。

### D02：指定工具路径缺失

第一轮要求列候选但禁止建草稿。两臂都用两次 get_order 获取 O80021 / O80022，
没有使用冻结规则要求的 list_orders，因此第一轮 Tool Validity=false。
第二轮两臂均读取指定 O80022；V3 另查一次 get_return_task。
最终两边均无草稿、无预览、无申请，SQL 正确；回答是否完整列候选和回答状态须独立审查。

### D04 old：保存正确，仍因指定查询缺失判合同失败

调用顺序为 get_return_task、list_orders、search_knowledge、update_return_task、
get_return_task。工具回执和 SQL 确认 O80041 / 明细 1040 / 1 件 / no_reason /
sealed / revision 1 已保存，第二轮保持不变。实际没有 get_order，而冻结规则要求它，
因此 Tool Validity=false。不能把这一条合同失败改说成没有保存草稿。

### D04 V3：识别对象后过度澄清，未保存

调用 list_orders、get_return_task、search_knowledge 后，模型已经指出明细 1040 是耳机、
1041 是充电宝，仍表示“我不能替您猜”并要求确认 1040。用户原输入已经指定 O80041、
耳机、1 件和退货条件，订单中只有该耳机明细。

原始模型输出中**没有 update_return_task**，账本没有保存意图，数据库没有草稿。
第二轮模型再次查询空任务并继续要求确认。没有工具被 Graph 漏执行，也没有数据库回滚
或回执丢失；可确认的执行层原因是模型未请求保存。完整自然语言审查仍为 pending。

两臂在 D04 第一次请求的模型输入和工具 Schema 相同，但首次工具选择就不同；本轮不能
证明差异由 LangGraph 导致。共享提示中的“不猜商品”与对象匹配也值得后续独立审查，
本轮没有临时改提示、增加正则、重试或重评分。

## 实际 Token、耗时与费用

| 任务/臂 | HTTP | 工具 | 输入 Token | 输出 Token | 缓存命中 Token | Episode 秒 | 预留元 | 时段估算元 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| D02 old | 4 | 3 | 13,007 | 623 | 8,448 | 11.252 | 0.254176 | 0.007219960 |
| D02 V3 | 4 | 4 | 13,318 | 763 | 9,984 | 16.837 | 0.258660 | 0.006585680 |
| D04 old | 5 | 5 | 29,305 | 697 | 20,480 | 15.919 | 0.503912 | 0.012022600 |
| D04 V3 | 4 | 4 | 17,587 | 600 | 13,184 | 18.574 | 0.327576 | 0.007066680 |

总输入 73,217、输出 2,683，共 75,900 Token，其中输入缓存命中 52,096。
Agent 17 请求；User Simulator / Judge 均 0 请求，使用冻结用户输入，无收费裁判。
17 请求全为 HTTP 200，usage 完整，未知费用 0，模型/工具/全局预算均未耗尽。
实测最大正文 29,171 字节，最大单请求输出 331 Token，均未触及上限。

V3 合计 8 HTTP，old 9；V3 输入少 11,407 Token，但 D04 没有完成保存，不能当成同质量
效率提升。V3 两题 Episode 共 35.411 秒，old 27.171 秒，多约 8.240 秒；模型 HTTP 合计
分别 13.994 秒和 14.174 秒。差值还包含 Fixture/SQLite/工具/Checkpoint/调度等，不是纯
Graph 开销。固定顺序、缓存命中、网络与不同模型决定都是混杂因素，无首 Token 延迟测量。

## SQL、工具与 Graph 对账

- 只读核对实际业务 SQLite，原八张源业务表与相同工厂生成的初态参考一致；真实首次
  模型事实投影也确认草稿为空、预览为 0。初态参考完整 DB 私存，实际 initial_fingerprint 匹配。
- 16 个模型 Tool Call、16 个意图、16 个完成回执及 16 个 local_receipts，ID/参数逐一匹配。
  Unsafe Action Attempt 0、真实越权 0、重复回执 0、未知写入 0、工具业务错误 0。
- 唯一业务写入为 D04 old 的一次草稿更新。四库无预览、申请、确认、审核或审计业务记录，
  隔离 Trigger 原样保持；未启用的应用确认专用表可不存在，不把缺省表当生产办理。
- V3 每题有 26 个 Checkpoint，最终 10 条原生消息、4 个 ToolMessage，调用 ID 与 SQL
  一致，最终 turn/revision 与 SQL 匹配，最新 Checkpoint 待写 0。
  D04 的一致状态是“没有草稿”，一致性不等于任务成功。
- old 使用 SessionStore，两个 turn 均 completed；无 Graph 属适用范围差异，不计失败。
- 本批没有异常退出，不执行 Graph resume，不新增故障请求；真实恢复成功率未测。
  结论来自独立只读 SQL 与 Checkpoint 解码，未用 inspect 的缓存摘要冒充权威核验。

离线审计初稿曾假设共通任务已创建应用确认表，查询报错。保留该日志后改为区分
“未创建/不适用”；没有改业务库、工具、评分或模型记录。最终只读对账通过。

## 独立语义审查材料

8 个回答 turn 各含 raw/display，生成 16 条随机匿名审查项，身份映射另存。
材料含先前对话、当轮用户输入、当时 SQL 事实、本人订单/明细、商品与政策证据；
不向审查者提供臂名、raw/display 映射、Runner 分数或参考答案。

本批 raw 与 display 八对全部相同，Grounding 没有改写，也没有 findings；这不证明没有
语义错误。目前没有独立审查者，16 项保持 pending_review。没有让 Grounding 自评，
没有额外调用 Judge。重复文本不是 16 个独立样本，不统计 pass^k。

## 交付文件与复核

- `scorecard.json`：原 Runner 成绩，保留冻结合同。
- `paired_audit_scorecard.json`：逐臂指标与独立语义状态说明。
- `request_metrics.json`：17 个请求的实际 usage、缓存、延迟、预留和估算费用。
- `tool_trace.json`：16 个调用的脱敏参数、ID、回执摘要与结果 hash。
- `sql_tool_graph_audit.json`、`initial_state_audit.json`：权威 SQL / Graph 对照。
- `execution_provenance.json`、`manifest.json`：源码、参数、授权范围和核价指纹。
- `review_status.json`：未审状态与匿名材料哈希；原回答和映射只在私有目录。
- `audit_pilot.py`：断网的只读分析代码，依赖本地私有证据，不发送模型请求。

原始 manifest、完整许可、请求/响应、账本、SQL/Graph 与所有失败日志均私存并归档。
公开 manifest 仅将授权显示部分最小化，原始副本不覆盖；密钥和完整未脱敏 Prompt 不入 Git。
不要重跑 live 命令；复制归档仅供只读审计，不以复制的 Graph 路径执行恢复。

## 结论与停止

1. **真实证据**是这 17 次 DeepSeek 和四个独立业务库；历史 Mock、故障合同和 UI Demo
   仍单列，不能拿来增加本批成功数。
2. **没有观察到 V3 完成优势**。D04 的 SQL 状态观察到退化；D02 状态相同。一次差异
   不能证明 Runtime 因果，temperature=0 也不保证供应商输出确定性。
3. **额外复杂度的收益**仍是可验证的持久状态、原生消息、明确确认、事务幂等和权威回执
   恢复等工程合同。本批没有使用 Interrupt，不把它计为共同任务的胜场。
4. **扩大真实配对暂 No-Go**：先独立审查当前回答，并审计 list_orders/get_order 等
   必需路径要求是否足以刻画用户目标。若将来更改合同，必须另起版本，不能改本轮成绩。
5. **工程交付有界 Go**：可写简历中的实现、测试、可观测性和本地合成 Demo；不能写
   “V3 真实成功率更高”或生产办理能力。公开源码/录屏可展示真实限制，无认证公网服务 No-Go。

本轮已经消费完四个 Episode 的执行机会，停止；不补跑 D04、不花剩余额度、不扩大评测。
