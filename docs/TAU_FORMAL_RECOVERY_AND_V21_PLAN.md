# 电商客服 Agent 正式评测恢复与 V2.1 验收

日期：2026-10-22；共同基线 `462569b`。本轮**零付费请求**。
原正式实验仍为102段完成、1段中断、17段未开始；下面的18段脚本预演不计入真实成绩。

交付分开：

- A：[fix/tau-formal-recovery](https://github.com/qingtian-4/ecommerce-agent/tree/fix/tau-formal-recovery)，仅评测续跑/持久化/监督；生产Runtime和Adapter保持原冻结字节。
- B：[fix/runtime-v21-evidence](https://github.com/qingtian-4/ecommerce-agent/tree/fix/runtime-v21-evidence)，独立V2.1路由及写回执修复；不混入A的成绩，不默认启用新机制。

## 退出调查

结论 **UNKNOWN**。对应时段内核journal未找到记录；dmesg没有关联OOM，当前内存/磁盘充足，
WSL的旧告警及历史启动日志不能证明本次退出原因。原进程没有留下退出码或traceback，
不能根据PTY/session消失断言API故障，也不能从Router缺陷推断进程退出。
[系统证据](https://github.com/qingtian-4/ecommerce-agent/blob/fix/tau-formal-recovery/results/tau_recovery_v21_20261022/exit_investigation.json)。

旧脚本不能续跑的具体位置：`execute` 要求全量freeze相等、`out.mkdir(exist_ok=False)`拒绝旧目录、
每次创建空TaggedLedger并遍历40×3，没有completed过滤或祖先账本；`Ledger.save`整文件写入，
缺心跳/退出码。旧requests约44.9MB，多次全量覆写造成写压力，但没有证据它导致退出。
Provider List重复输出，旧总日志仅268KB，不足以认定日志失控。

## A：只读审计和安全续跑

[scripts/tau_resume.py](https://github.com/qingtian-4/ecommerce-agent/blob/fix/tau-formal-recovery/scripts/tau_resume.py)
先验证原protocol/manifest/INTERRUPTION、102个已评分文件SHA、任务/组/文件名、reward一致性、
请求序号/归属/usage/预留/费用；多文件重复、文件损坏、账本缺失或篡改均拒绝。
未结算#1668不假设免费：新账本副本按0.163316元全额保留，原文件保持不动。

| 项目 | 值 |
| --- | --- |
| 原已结算保守费用 | 32.619652 CNY |
| #1668未知usage全额预留 | 0.163316 CNY |
| 继承费用 / 总上限 | **32.782968 / 40 CNY** |
| 剩余总额 | **7.217032 CNY** |
| 新请求起始序号 | **1669**，全角色共享，不重置5000请求保护 |
| 原完整结果 | 102，全部跳过，失败结果也不重跑 |
| 待执行 | 18，task97/V1为新尝试2，其余首次尝试 |

默认入口只生成计划；`--dry-run`明确使用脚本决策和断网预演；`--live`需要人工审批文件及
精确匹配的恢复protocol SHA、成功的离线预演。**当前没有 approved=true 文件。**
恢复协议绑定父证据哈希、新执行脚本哈希、原协议、18段清单及预算。
原未知usage即停止的政策没有被静默取消：只有后续明确审批，才允许在全额计费后重开隔离Episode。

每段仍新建官方环境。task97旧轨迹不补写成功、不接着重放旧工具；新尝试独立记录parent和累计编号。
补充再次中断时继续保留全部费用与尝试，必须新审计/新审批，不能挑选成功或自动重试到通过。
已评分但尚未登记完成的原子结果文件经校验后视为完成，不重复执行。

互斥flock按原协议绑定，在原目录外持久保存cohort最新补充路径：
既阻止同时运行，也阻止换一个新输出目录、重复使用原始32.78元余额。
以后必须从最新补充目录继承完整父链；不能回到原始目录重新启动。

`RequestJournal`先持久化单请求，再写小余额缓存；原子replace前fsync文件，之后fsync目录。
缓存可以落后于连续、校验通过的请求日志；缓存超前或缺请求拒绝。未知usage全额计入。
完整requests导出只在正常收尾一次执行；SIGKILL时以单请求记录重建，不依赖finally。

## 运行监督与故障测试

[scripts/tau_run_state.py](https://github.com/qingtian-4/ecommerce-agent/blob/fix/tau-formal-recovery/scripts/tau_run_state.py)
记录PID、boot/start token、2秒心跳、Task/Arm/请求序号、完成数、已计费、在途预留和余额。
SIGINT/SIGTERM走安全停止，SIGKILL没有exit文件时保留“非正常/原因未知”；PID复用不误认为旧进程仍活着。
[scripts/tau_supervise.py](https://github.com/qingtian-4/ecommerce-agent/blob/fix/tau-formal-recovery/scripts/tau_supervise.py)
将控制台独立落盘，记录子进程真正returncode并转发终止信号。依赖原子账本恢复，而不是假定退出处理必执行。

实际tmux监督下的18段断网预演已通过：18/18接线、0新请求、预算32.782968不变、子进程退出码0。
检查原Runtime/Adapter和官方工具/评分哈希，拒绝在V2.1源码下补原始成绩。
[全部恢复证据](https://github.com/qingtian-4/ecommerce-agent/tree/fix/tau-formal-recovery/results/tau_recovery_v21_20261022)。

正式测试含：SIGKILL、SIGTERM、请求落盘/缓存未写时SIGKILL、结果写完未登记时SIGKILL、
进程间重复启动、不同输出目录顺序复用预算、文件损坏/缺失、重复结果、余额不足、再次中断父链。
审查发现的缓存落后误拒绝、恢复编号固定2及顺序复用旧预算问题均已补测修复。
正常运行锁退出释放；不删除锁inode，不用修改业务事务或权限获得恢复能力。

## 待人工批准的18段

顺序保留原来按任务index轮换组的协议：

| 顺序 | Task | Arm | 原状态 |
| --- | --- | --- | --- |
| 1 | 97 | ACS V1 | started_unsettled，重开隔离环境，attempt2 |
| 2 | 97 | ACS V2 原始 | not_started |
| 3 | 97 | Official | not_started |
| 4 | 100 | ACS V2 原始 | not_started |
| 5 | 100 | Official | not_started |
| 6 | 100 | ACS V1 | not_started |
| 7 | 101 | Official | not_started |
| 8 | 101 | ACS V1 | not_started |
| 9 | 101 | ACS V2 原始 | not_started |
| 10 | 102 | ACS V1 | not_started |
| 11 | 102 | ACS V2 原始 | not_started |
| 12 | 102 | Official | not_started |
| 13 | 108 | ACS V2 原始 | not_started |
| 14 | 108 | Official | not_started |
| 15 | 108 | ACS V1 | not_started |
| 16 | 111 | Official | not_started |
| 17 | 111 | ACS V1 | not_started |
| 18 | 111 | ACS V2 原始 | not_started |

按102段完整结果均值估计新增约292请求、保守约5.72元；加25%约7.15元，接近剩余7.217032元。
**这不是上界，尾部任务更复杂，不能保证18段都能跑完。** 不增加预算、不挪用其他授权；每请求预留，
费用/usage/协议异常立即停，保留完成与缺失。只向本表执行，不跑第四组、额外trial或故障实验。

## 审批与准确命令

审批模板 [APPROVAL_REQUIRED](https://github.com/qingtian-4/ecommerce-agent/blob/fix/tau-formal-recovery/results/tau_recovery_v21_20261022/APPROVAL_REQUIRED.json)
保持false。需要后续人工明确接受以下政策：#1668全额计入；task97新尝试；沿用原总上限40元，
新增最多7.217032元；可能不足以完成；保留所有失败，不保证成功才停止。

当前路径的监督预演恢复协议SHA：
`e732daa76bc77be8472ad2ef9e611a3f90988dec344061b97232e1f05c340d3c`。
换目录/源码/依赖/模型配置后必须重建预演和审批，不能只编辑旧SHA。

```bash
# 只有批准后，才准备对应 approved=true 的 approval.json，并运行下面命令。
# 当前任务不执行此付费命令。使用恢复分支，不能切换为V2.1。
git switch fix/tau-formal-recovery
tmux new-session -s tau-retail-recovery
cd '/home/qingtian/projects/E-commerce Agent'
TAU2_DATA_DIR=/tmp/acs-external-tau-20261022/data \
  .venv/bin/python scripts/tau_supervise.py \
  --python /tmp/acs-external-tau-env-20261022/bin/python \
  --log results/tau_retail_formal_supplement_attempt2.console.log \
  --status results/tau_retail_formal_supplement_attempt2.process.json -- \
  --resume-from results/tau_retail_formal_20261022/live \
  --out results/tau_retail_formal_supplement_attempt2 \
  --upstream /tmp/acs-external-tau-20261022 \
  --v1-root /tmp/acs-external-v1-20261022 \
  --preflight results/tau_recovery_v21_20261022/acceptance_dry_run \
  --approval-file approval.json --live
```

Ctrl+B再D只detach，`tmux attach -t tau-retail-recovery`返回。Ctrl+C/向子进程SIGTERM安全停止。
控制台、supervisor真正退出码、补充目录run_status/exit、单请求记录、attempts和summary均独立保留。
临时外部环境丢失时先按锁定版本恢复并重新校验，不自动重新联网付费；旧上游dd8757d4保持不动。
如果再次中断，从最新补充目录重新做只读计划和审批，不直接重复本条从原目录启动的命令。

## B：V2.1的最小修复与边界

根因：本店SCOPES与官方工具求交，SKU触发orders却没有匹配工具；Loop把普通/DSML文本当最终答复，
没有将完成声明绑定真实执行。新路由在非明确拒绝且匹配能力为空时回退全部现有工具；
有本地草稿的明确补充/更正确认只补充读取及更新草稿能力，普通跨单物流不开放取消工具。
Registry权限/确认/事务不变，不增加英文任务规则或Task ID特判。

`write_receipt_guard_enabled=false`为新独立开关；原开关默认不变。开启时：

1. 模型文本先缓冲，工具与进度仍可显示；不在无验证时流出完成声明。
2. 真实ToolCall、执行错误、结构化回执与目标字段分开记录，DSML内容不能变成调用。
3. 本地草稿核对SQLite实际状态、修订和本轮sources；预览仍不是申请。读取失败或不确定写停止，不重放。
4. 外部ToolMessage按原schema/ID验证；地址字段、取消状态、申请状态等与参数绑定。商品替换核对前后SKU数量，
   支付核对原记录及新增付款/退款差异。只缓存最近返回对象供当前写前后比对，不建通用Fact Ledger。
5. 写操作后的最终确认用最新结构化回执摘要，不能拿订单A的回执证明B，也不能拿旧草稿revision证明新状态。
   历史退款不能证明本次退款，更不能证明非礼品卡到账。
6. ChatService读回任务卡片；最终answer、assistant历史及final Trace一致。write_verification单列UI轨迹和进度。

先红测，再修复：独立样例覆盖无schema DSML、空范围、错误目标/字段、失败、被Router拒绝、
写成功响应丢失、字段未改变、引用/假设、正确短答、默认关闭及逐帧保护。保留首次回归失败：
最初扩大现有任务工具范围造成跨单物流暴露取消工具，原测试检出后收窄，未放宽原断言。
审查还检出回执只匹配类别、历史退款误授权、商品与支付验证不完整，均补独立反例修复。

[独立V2.1证据及冻结协议](https://github.com/qingtian-4/ecommerce-agent/tree/fix/runtime-v21-evidence/results/runtime_v21_20261022)。
原三组全部90个成功轨迹，以固定模型决定/用户消息在真实官方工具的新环境回放：
初始DB、最终DB、工具序列、模型决定数均90/90保持一致；444个最终回复中95个发生变化。
**这不是回答完整性无回归证明。** 写后整段模型回复被回执摘要替换，附带政策/商品解释可能丢失；
不做付费NL重评分，不宣称V2.1官方成功率提高。原失败重放也不会凭空修好错误SKU或认证决策。

范围限制：所有已尝试写操作均以结构化回执控制最终展示；完全没有写尝试时，普通文本完成声明只做有限语法识别，
DSML和已覆盖表达可拦截，不能保证识别任意自然语言隐含虚报。引用/假设样例通过不代表完整语言覆盖。
新增0模型调用；有额外SQLite读回和缓冲首字代价，后续上下文因摘要改变。没有真实Token/延迟收益证据。
因此**保持关闭，不据此宣称生产级可靠性或立即大规模评V2.1**；后续先验证混合问答完整性再申请小规模新对照。

## 验收决策

- A：可提交人工审批的原始V2补充计划；本轮只完成代码/无网预演，不代表已批准或真实补跑完成。
- B：目标反例与业务状态回放通过；有明确回答完整性及自由表达覆盖限制，不默认启用、不冒充原始V2。
- 原102段、未知#1668、失败、历史评分与用户修改全部保持。CI/最终测试以两个独立分支的新提交检查为准。
- 没有合并main或PR #4、改可见性、扩大业务或新增模型审查器。正式120段完成状态仍No-Go，等待恢复政策审批。
