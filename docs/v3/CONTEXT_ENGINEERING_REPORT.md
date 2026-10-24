# Context Engineering 与确认门禁

基线74ba808（真实运行1e7f86a），复用原V3 Worktree，独立分支feat/v3-context-confirmation-poc。默认Runtime、历史实验与主目录用户文件保留；没有新增框架或依赖。

## 根因与最小修复

`LocalState.messages` 继承 AgentState 的 `Annotated[list, add_messages]`。旧 before_model.refresh 每次返回新SystemMessage且没有稳定消息ID，因此reducer追加而不是覆盖。实际15个请求的事实消息数1→15；最后一次46条消息。旧事实包含完整草稿、订单、政策与预览，并与工具回执内的current_task重叠。

新代码：before_model只核验应用context并更新expected_revision；wrap_model_call在每个请求前重新核验有效Turn，从SQLite读取最新Gateway.state，用ModelRequest.override临时加入一份事实SystemMessage。临时消息不返回Graph，不写入Checkpoint。恢复旧Checkpoint时只在模型投影中排除本应用前缀的旧System事实，原件仍在历史中；Human/AI/ToolMessage均原样保留。

没有裁剪工具结果、模型输出、用户更正/否定/授权，没有摘要模型、长期记忆或新RAG。历史合法订单/政策回执仍可见，但只有请求时核验的事实表示当前状态；写权限、revision与有效Turn始终由工具网关和SQLite验证，模型上下文不是权限凭证。

源码：src/acs/local_return_poc.py；私有证据数字审计：scripts/audit_local_context.py；测试：tests/test_local_context.py。红测试已在旧代码实际失败（Checkpoint累积3条业务System），同一断言在修复后通过，日志/tmp/v3-context-red.log、/tmp/v3-context-green.log。

## 证据与估算边界

results/context_engineering_20261024/baseline_context_audit.json按原HTTP SHA逐条验证15份私有请求，公开仅分类字节、数量、usage和成本。各部分Token是按紧凑UTF-8字节占比分摊供应商实际prompt usage的估计，不是DeepSeek分词器精确测量。实际usage总计179348输入、1529输出。

历史事实快照220353字节，占全部wire34.86%；其中除每次最后一份以外的旧快照占29.89%。订单、政策回执及重复current_task仍是显著成本，本轮先消除已确认的追加问题，不构建复杂Context Manager。相同旧模型决定的离线投影预留2.774388→2.018088元，不能当作新的真实模型结果。

## 分阶段门禁

先运行旧/新固定8阶段、原32项业务/恢复专项、完整CI，再以新的目录/源码SHA/3元32请求账本执行相同冻结输入。用户输入和业务初态保持，不因结果调整。第8阶段必须有完整可信最终答复和新预览回执，人工核对未虚称申请/退款，才允许进入Interrupt实现。

真实与最终验收证据产生后追加；本文件此处不将尚未执行的确认流程描述为完成。

## 离线对照实际结果

74ba808原实现通过git show载入临时模块，候选使用当前源码，在两个全新fixture执行相同8阶段输入和15个固定模型决定。两侧10次工具名称、规范参数、revision前后值、逐阶段业务状态、最终答复完全一致，正式四表均0；事实消息旧1→15，新每次1。原生Tool Call/ToolMessage配对完整。证据results/context_engineering_20261024/offline_parity.json；这只是协议/语义合同，不是模型质量评测。
