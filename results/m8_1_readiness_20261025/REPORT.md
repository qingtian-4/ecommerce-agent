# M8.1 离线验收与真实 Pilot 准备

真实模型 HTTP **0**，实际费用 **0 元**。没有创建有效付费授权，没有运行 DeepSeek。
独立分支 `fix/v3-evaluation-live-readiness`；业务 SDK 回放源码
`95c14fb522085c574abb3c7b4c9bb45e9dbbc52c`，两个实验启动时工作区均干净。
其后仅收紧盲审 A/B 随机源并补证据；执行器、评分器与 SDK 入口文件哈希相同。

## 实际结果

| 无网络实验 | old | V3 | SQL/tool | 配对/语义已决 | 待审项 |
|---|---|---|---|---|---|
| 原固定决定及回答 | D02 fail，D04 pass | D02 fail，D04 pass | 4/4 | 2/2 | 0 |
| 新冻结开放表达反例 | 2 pending_review | 2 pending_review | 4/4 | 2/0 | 16 |

每个实验均为 4 Episode、16 次真实 SDK MockTransport 调用；没有真实网络模型请求。
新开放表达只改变固定测试模型的回答，原用户输入、工具决定、业务初态和评分要求未改。
模型输入、工具 ID/执行与 SQL 终态两对一致，不将此解释为模型成功率或 Runtime 优劣。

- [固定输出 Scorecard](../m8_1_sdk_fixed_20261025_run1/scorecard.json)
- [开放表达 Scorecard](../m8_1_sdk_open_20261025_run1/scorecard.json)
- [固定输出 SQL 对账](../m8_1_sdk_fixed_20261025_run1/sql_reconciliation.json)
- [开放表达 SQL 对账](../m8_1_sdk_open_20261025_run1/sql_reconciliation.json)
- [专项矩阵](offline_contract_matrix.json)、[验证摘要](verification.json)
- [历史目录逐文件哈希](history_preservation.json)

D02 首臂失败后，确实执行了 V3，再执行 D04 两臂。D02 原固定回答“请先选择订单”等
未包含要求的候选/状态，保留失败，没有改题或参考使之通过。新同义表达缺少独立语义
裁决，成功字段为 null，不能因为看起来合理而算通过。

D02 两臂最终没有草稿、预览或申请，各 2 个工具回执。D04 两臂均为 O80041 / item 1040 /
1 件 / revision 1 草稿，各 4 个回执，其中仅 1 次草稿更新；没有重复调用 ID。
所有实验中 return_requests、return_confirmations、return_audit、return_reviews 均为 0。
每轮 SQL 都直接读取 `return_tasks.data_json` 和实际回执表，Graph 不能替代这些证据。

## 验证与失败保留

本地全量 1124 passed、0 skipped；新增专项 36 passed，包含真实 SDK、业务 SQLite、
提交前后故障、实际子进程退出、重开账本、授权到期及输出/body 上限。
源码 95c14fb 的 [GitHub CI](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40186948541)
同样为 1124 passed、0 skipped，Ruff 和 all-tracked 密钥扫描成功。最终提交 CI 链接由交付回复提供。

开放表达错误归类和盲审顺序分别先出现红测，再修复；没有删除失败测试。原候选审查包
因公开 Seed 可影响 A/B 身份而废止使用，但文件保留。正式材料改用系统熵，见
`results/m8_1_human_review_20261025_run2`。历史 v1/v2 四个保护目录连同忽略的证据逐文件
哈希不变；main、默认 Runtime、PR #4、主目录三个用户文件没有修改。

完整模型输入/输出、业务 DB、Graph、账本、JUnit/测试日志只保留本地私有证据与 CI Artifact。
公开 Git 仅含摘要、数据合同和源码指纹。复制的历史 Graph 绑定原路径，只用于只读审计，
不能把归档副本作为可恢复执行会话。重新复现应使用全新输出目录。

## 七项结论

1. 同义回答不再仅因不同于参考而失败，而是 pending_review；未经审核不能宣称答对。
2. SQL 成功且语言未审时，任务为 pending_review、task_success=null；SQL 指标单独保留。
3. 旧臂普通失败后继续 V3 的真实无网络业务执行已验证，不重跑失败臂。
4. 未知业务写入、实际安全违规、不可核实协议/费用、全局预算和授权到期仍全局停止。
   单 Episode 上限/截止仅终止该臂，未完成 pair 不进入完整配对分析。
5. 正文最大实测 24,849 字节；32 KiB 的输入上界 69,632 Token，单次最大预留
   0.147456 元。计划 32 次最大 4.718592 元；40 次可能先触发 5 元费用限额。
   未来必须新授权、重新核价、绑定实际源码，当前价格仅为冻结假设。
6. 228 条来源材料可分层盲审，已抽 48 条及 4 组匿名 A/B；无人独立审查，正确回答被改写
   的历史样本为 0，覆盖缺口保留，不报告独立语义正确率。
7. 离线准备 **Go**；获得新授权、最新干净 SHA 和价格复核后，两题开发 Pilot **条件 Go**。
   现在调用付费模型、宣称模型提升/泛化、正式 Benchmark 或生产部署均 **No-Go**。
