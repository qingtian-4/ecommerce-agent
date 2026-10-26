# 本地电商Benchmark v1.1离线基线

本轮完成真实业务工具、合成SQLite、原生LangGraph与BM25的离线执行及评分器校准。
**没有运行真实模型，没有测得Agent智能成功率或新旧Runtime胜负。**

运行ID：`commerce-baseline-v1_1-20261026-run1`。
合同：`local-commerce-v1.1/m8.1-open-answer-v3`。
执行代码为基线`4f006c5f6a35343594e43d236d4df46f4ffec0dd`加本轮新增文件，
完整逐文件SHA256在summary.json；provenance.json已核对这些文件与交付源码相同。
最终Git提交SHA见本分支及CI。没有把未提交源码执行伪称为已有Git SHA执行。

## 实测

|项目|结果|边界|
|---|---|---|
|新数据|3用户、8商品、18订单、19明细、16SKU、13物流|全新合成，与Demo隔离|
|多轮正向执行合同|14/14 SQL和工具检查通过|11共同能力、3 V3应用；固定决定，非模型预测|
|工具回执|38条|由实际ToolRegistry/业务SQLite产生|
|模型决定|68次|既有M8 EvaluationModel，HTTP为0|
|本地Episode累计耗时|240196.174 ms|真实Graph/SQLite/文件I/O，包含本机负载；不是云模型Latency|
|RAG上下文完整证据|29/32（90.625%）|3个上下文缺漏；证据组非穷尽的语义真值|
|RAG平均证据Recall|0.9375|仅32条有标注证据的问题|
|RAG候选6完整证据|31/32（96.875%）|诊断候选与工具输出上下文分开|
|无答案上下文暴露|4/4|不是4条模型幻觉，也未验证答案拒答|
|RAG执行错误|0|实际V3 LocalKnowledge BM25；Dense/RRF未运行|
|对抗评分控制|13/13符合预期|6条真实业务/回答变体、7条检索评分控制|
|模型HTTP、Token、费用|0次、null、0元|没有模型Token用量，不用0冒充测量|

逐题结果见agent_scorecard.json；每步SQL和确认状态见authority_checks.json，最终权威SQL见
sql_final_states.json。tool_receipts.json只发布调用ID、工具、许可参数及状态字段，完整回执留本地。
raw/display独立评分见answer_audit.json；新措辞待审控制不因不等于参考而判失败。

CB12重复接受只创建一份pending_review申请；CB13拒绝不创建申请。
CB14在COMMIT后丢失应用回执，重新打开Graph后只读查到一份完整申请、确认、审计和幂等回执。
该恢复检查是SQL权威结果恢复，**Graph诊断仍为`reconciliation_required`**，不能描述为镜像已恢复一致。
原有运行时正确未据Graph镜像重放写入；新基准没有另做SIGKILL实验或修复镜像。

## Badcase与判断

|案例|观测证据|可以确认的结论|
|---|---|---|
|RQ18 C35 PD版本|正确B106#4在候选第4位，上下文仅前三|词法排序与上下文截取造成必要证据丢失|
|RQ30发货承诺及轨迹延迟|shipping_policy#2在候选第4位，只取到了另一个事实|多事实上下文覆盖不足|
|RQ31聊天确认和真实退款|capabilities#3连候选6也未进入|词法候选召回不足，不只是top3截取|
|RQ33–36库存件数/快递员电话/医疗/循环阈值|都无直接Gold答案，但工具返回非空上下文|缺少经验证的无答案检索拒答边界；尚未测模型是否编造|

评分器变体清楚地区分：漏写/错订单/错数量造成SQL失败；否定取消中的非法调用尝试即使
数据库拒绝仍判工具无效；SQL正确但“申请已提交、退款完成”的回答判fail；未知合法改写为pending_review。
不以“工具调用成功”代替任务或回答正确，不用Grounding自身规则评价Grounding。
answer_control_audit.json保留6个变体的原文、展示、SQL事实及独立评分。
虚假提交变体被展示层改写后仍为pending_review，未获独立语义裁决，不能据此宣称纠错成功。

v1到v1.1只修复安全读工具名单和等价政策证据遗漏，两条原先失败的有效性测试现在通过。
两版主基线汇总相同，未将合同改进包装为业务质量提升。

## 验证、隔离与复现

新专项31项通过，包含可字节重建v1和v1.1、篡改拒绝、原Fixture Trigger保持不变、
真实业务正向/负向/恢复、RAG以及吞掉联网异常仍被检测。
全量回归、Ruff、密钥扫描和远端CI结果见validation.json（完成后记录）及分支CI。

preservation_audit.json核对1492个原有受保护文件；业务源文件、前端、历史数据与实验没有修改。
用户TODO及原有未跟踪文件保留。没有动main、PR#4、默认Runtime或Demo业务数据库。
本地私有证据路径见provenance.json，不提交数据库、费用日志、完整Prompt或大体积中间文件。

```bash
uv run python scripts/eval_commerce_benchmark.py --mode verify
uv run python scripts/eval_commerce_benchmark.py --out /tmp/commerce-new-run
```

完整数据/契约/上游固定版本审计与复现命令见docs/COMMERCE_BENCHMARK.md。
本阶段Go：可用的本地离线执行与评分器校准基线。
No-Go：宣称模型质量提升、独立泛化成绩、官方成绩、Graph镜像完全恢复或生产可用。
本轮到此停止，不启动付费评测、RAG调优或下一阶段。
