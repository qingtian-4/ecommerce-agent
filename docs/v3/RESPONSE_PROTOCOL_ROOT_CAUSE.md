# V3 响应合同：根因、修复与证据边界

基线 `93db5cf`，修复分支 `fix/v3-response-contract-parity`。历史任务 33 的旧 Loop Task/DB Reward 为 1，V3 为 0；第四次模型调用被 `IncompleteModelResponse('Partial, malformed or mixed model response')` 拒绝。原报告、Scorecard 和失败 checkpoint 保留不动。

## 可确认与不可确认

可确认的代码根因是**两个官方 Adapter 的模型响应接受规则不同**。旧 Loop 在 `wants_tools` 时保留文本并继续执行工具，V3 `model_boundary` 把任意“文本＋工具调用”视为不完整响应。原独立离线诊断以及本轮新增回归均能触发跨臂差异。本轮修复前执行 `test_mixed_response_preserves_semantics_and_official_parity`，在真实官方环境中失败，证明原断言未被旧测试覆盖。

可确认的历史运行事实仅为 V3 第四个模型响应触发该复合守卫。历史响应正文没有保存，不能确定触发的是 finish_reason、invalid_tool_calls 还是 mixed_content，不能声称已找到旧真实失败的精确谓词，也不能从另一臂的成功倒推同一个响应会被它接受。

## 最小共享合同

新 `src/acs/model_response_contract.py` 是两个官方 Adapter 的共享完整响应判断。`scripts/tau_model_response.py:ContractLLM` 在实际旧 Loop 的模型边界调用它；`scripts/tau_langgraph_agent.py:model_boundary` 在原生 Graph 模型节点调用它。未重写模型工具循环，未修改默认本地客服 Loop、业务权限或官方 Task/Policy/评分器。

| 响应 | 共同规则 |
| --- | --- |
| 非空纯文本，finish=stop | 最终回复 |
| 一次或批量合法调用 | 整批检查通过后按原顺序逐个交官方执行 |
| 文本＋合法调用 | 接受并原样留在模型上下文；对官方用户只投影空文本工具意图，不能把中间文本当完成声明 |
| finish=stop 且含调用 | 作为完整工具批次处理，不丢调用 |
| finish=tool_calls 但无调用 | `missing_tool_calls`，整批停止 |
| length、content_filter、其它异常 finish | `invalid_finish_reason`，不执行任何调用 |
| HTTP JSON/SSE 缺 finish | 两臂都 `missing_finish_reason`；流缺终止帧则 `incomplete_stream` |
| 参数 JSON 语法错误或不是对象 | `invalid_tool_calls` |
| 参数对象缺业务字段或业务拒绝 | 仍由官方执行器判断；原始错误回执进入历史 |
| 空白 ID、批内重复、历史已用 ID、未知工具 | 分别停止，不执行可疑批次 |
| SDK 响应包络错误 | `sdk_response_parse_error`；取证分类为 wire/SDK 结构解析错误，而不是业务拒绝 |
| 不完整流、网络中断、未知写回执 | 明确停止；已发出的写操作不能自动重放 |

原有脚本模型 `AIMessage` 没有 finish metadata 时仍按“已构造完成的 fixture 对象”兼容，它不代表真实 HTTP 可以省略完成标记。真实 SDK 两条路径都先检查完整 wire 响应；不能用 fixture 宽松条件推断真实模型验收。

ToolCall 参数值、消息正文和官方回执不因记账或取证被重写。旧 SDK 使用 SSE、Graph 使用非流式 HTTP；工具参数 JSON 的空格可能不同，比较语义而非伪称逐字节一致。没有删除全部 Guard。

## 拒绝前取证

`src/acs/episode_transport.py` 在实际 HTTP 收到响应之后、SDK 解析及 Guard 抛错之前，先写不可覆盖、fsync 的原始响应文件，然后写结构判定；即使缺 usage 导致费用账本停止，原始输出和拒绝结构仍已保存。流在传输异常时保留已接收部分及原始异常类型。账本 request_id 关联模型、角色、usage、缓存、延迟、保守预留及费用。

`src/acs/response_evidence.py` 只接收生成的十六进制请求 ID，拒绝符号链接和公开权限目录；目录 0700、文件 0600，不保存请求头、Authorization、输入消息或 API key，已知凭据若被输出回显也会替换。Graph 在模型调用前保存 thread/checkpoint 关联，Callback 在 Guard 前保存解析后的 SDK/原生消息结果。实际 wire 在 SDK 之前拒绝时不会虚构“SDK 已经成功解析”。

原始 HTTP/SDK 响应、异常正文、完整会话、Graph checkpoint 和完整账本仅留在本地 `.private-evidence/`。它被 Git 忽略，不能上传为普通 CI Artifact。公开投影由 `scripts/tau_pilot_artifacts.py` 按白名单生成，只包含结构、允许的工具名、稳定 SHA256 ID 映射、Token、费用与校验摘要；不含模型文本、参数值、回执正文、任意 metadata 或异常字符串。异常 finish 原值在私有文件保留，公开显示 `<unexpected>`。

这不是对真实业务写成功的证明。官方 Environment 的真实回执仍是业务事实，Graph Checkpoint 不等于 Retail DB。没有实现跨 Graph/官方 DB/Orchestrator 的原子恢复。

## 恢复预算和回执

本轮审查还定位到 `EpisodeAgent.resume()` 的首次派发路径没有复用外层工具预算检查：模型 checkpoint 已提交、尚未 dispatch 时退出，恢复可能越过工具额度。现在正常生成和恢复都调用 `_record_output`，在执行前检查剩余期限、终止原因和工具意图。测试同时覆盖内存反例和真实 Graph checkpoint 恢复窗口；未授权第二次调用不会交到官方执行器。

重复回执、错误 Tool Call ID、未知写操作结果、迟到真实回执仍按原 receipt_guard/账本保护。两组对照均显式启用 receipt_guard，不扩大模型权限，不把业务拒绝与未知结果混为一类。

## 验证与同类路径检查

专项测试 `tests/test_response_contract.py` 通过真实 SDK MockTransport 和原始 Retail Orchestrator 逐例对照 DB、官方 Reward、工具执行与 ID、规范停止原因；不改原任务或 Reward。既有 mixed 拒绝测试改为合法响应的更强语义断言，坏 JSON、重复 ID、空 ID 仍保留拒绝断言，不通过删除负例获取绿灯。

检查范围包括旧 Adapter、V3 boundary、实际 HTTP、原生 Callback、Episode 正常生成与恢复、既有本地 PoC Guard、默认 `LLMClient` 和 `CustomerServiceAgent`。修改仅接入官方 Adapter；稳定的默认本地客服 Runtime 不因本轮合同变化获得新业务能力。

完整测试数量、矩阵摘要、红绿证据和新真实 Pilot 结果见本轮结果目录。真实运行须使用新运行 ID 和已提交源码 SHA，先通过所有离线门禁，再运行共用全局账本的只读 Smoke 和开发期任务 33/34。历史失败不可原位重跑。脚本矩阵只证明协议，不证明模型成功率。

## 已知限制

验收限定 Linux/WSL，`fcntl` 不支持 Windows 原生；SQLite 锁和 Graph 连接按既有生命周期测试，不宣称跨进程业务原子性。Graph Adapter 协议版本升为 `tau-langgraph-halfduplex-2`，旧版本 checkpoint 的签名不兼容，不直接续跑历史失败实验；本轮测试支持同版本、同配置恢复。冻结请求为 n=1；非流式当前取第一个 choice，流式拒绝非零 choice index，意外多 choice 的统一规则尚未扩展。预算限制已禁止本轮主动请求 n>1。

## 本轮真实验证结果

修复源码 `3c4ab1c` 的完整 CI 为 839 passed、0 skipped，Ruff/密钥扫描成功。新运行 `tau_response_contract_pilot_20261023_r1` 的两臂真实 Smoke 通过，Task 33 旧 Loop 与 V3 官方 Task/DB Reward 均为 1，最终 DB 哈希完全相同，且两臂各有一个 mixed 响应被接受。Task 34 旧 Loop 为 1；累计达到 48 次 HTTP 上限后，Task 34 V3 未启动，不得记为成功或 Reward=0。总费用按时段/usage 估算 0.038934880 元，保守预留 4.228444 元，无未知回执。

这关闭了本轮可复现的共享响应合同和取证阻塞，但不能还原历史第四次响应的精确拒绝谓词。详情及真实私有证据索引见 [新 Pilot 报告](../../results/tau_response_contract_pilot_20261023_r1/REPORT.md)。
