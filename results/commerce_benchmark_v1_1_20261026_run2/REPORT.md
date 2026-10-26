# v1.1 run2：干净环境隔离修复后的完整离线基线

当前推荐结果。数据仍为`data/eval/commerce_benchmark_v1_1`，合同仍为
`local-commerce-v1.1/m8.1-open-answer-v3`。没有改任务、初态、回放输出、评分器或历史结果。
执行基线`2023e575f3da957e07ec24504a9c569485dac43e`加本次RAG隔离修复，
源码文件SHA256在summary.json，已与本次交付源码逐文件核验；最终提交及其CI是交付版本。

## 为什么另起run2

run1在本机通过，但首次干净CI为1176 passed、1 failed：RAG的ToolRegistry初始化
未明确指定业务数据库，间接要求默认业务库存在。默认文件在本机存在，CI不存在。
BusinessDB构造尚未读取默认库中的业务记录，仍构成可复现性及隔离边界缺陷。

回归将默认路径设为不存在：原实现1 failed，修复后同一测试1 passed。
修复只在RAG入口新建临时合成SQLite并显式绑定，结束清理自己的临时目录；
不创建、迁移或修改Demo默认库。另核查M8执行器，其Registry已绑定gateway Fixture路径。
源测试和断言保留，未改CI去初始化默认库。详见ci_failure_and_repair.json。
原v1/v1.1 run1目录、首次失败CI及源码提交均保留，没有用绿灯重跑遮盖失败。

## 实测结果

|项目|结果|
|---|---|
|合成业务数据|3用户、8商品、18订单、19明细、16 SKU、13物流|
|正向多轮执行合同|14/14，11共同能力、3 V3应用增量|
|SQL终态、工具执行合法|各14/14|
|真实工具回执、固定模型决定|38条、68次|
|错误/待审评分控制|13/13符合预期|
|RAG上下文完整证据|29/32，90.625%|
|RAG平均证据Recall|0.9375|
|候选6完整证据|31/32，96.875%|
|无直接答案但返回非空上下文|4/4，单列风险，非模型幻觉计数|
|RAG错误|0|
|本地Episode累计延迟|275089.210 ms，包含真实Graph/SQLite/I/O及本机负载|
|模型HTTP、Token、费用|0、null、0元|

这只是固定模型决定下的业务执行和评分器校准，不是真实Agent效果或官方Benchmark成绩。
无云模型延迟、SDK推理、Judge或Dense/RRF运行，不能据本地耗时比较Runtime优劣。
任务与Gold同一作者，运行后全部为已见开发数据；不声称独立泛化。

逐题看agent_scorecard.json、逐步看authority_checks.json；最终SQL见sql_final_states.json。
CB12重复确认、CB14提交后回执丢失，各只创建一份pending_review申请，确认/审计/幂等回执完整；
CB13拒绝为0申请。CB14只读恢复SQL成功，Graph镜像仍为reconciliation_required，
未把镜像当作成功证明或自动重放写入，也未宣称Graph已完全恢复一致。

RQ18和RQ30的必要证据排在第4位，被上下文3截掉；RQ31第二个必要事实未进入候选6。
RQ33–36没有直接答案但返回知识上下文，未测生成答案是否拒答或编造。
这些Badcase保留在badcases.json，不在本轮调优默认RAG。
answer_control_audit.json保留漏写、错误对象/数量、非法取消、虚假完成及未知改写的独立判据。
虚假完成原文判fail，展示改写仍待审，不使用Grounding规则给自己宣判纠错成功。

## 验收与复现

原专项31项通过，干净CI随后发现上述依赖；补强原RAG测试后已确定转绿。
最终完整Pytest、Ruff、密钥扫描及原Chromium流程由本修复提交的CI验证，
验证摘要在validation.json，原始JUnit/浏览器证据由CI Artifact提供。
输入哈希在manifest，源码哈希在summary，公开材料哈希在provenance。
私有业务SQLite、Graph、完整回执与请求投影保留于provenance指定的新本地运行目录。

```bash
uv run python scripts/eval_commerce_benchmark.py --mode verify
uv run python scripts/eval_commerce_benchmark.py --out /tmp/commerce-new-run
```

完整设计和复用审计见docs/COMMERCE_BENCHMARK.md。
Go：本地离线工具/RAG执行与评分器校准；No-Go：模型质量提升、官方分数或生产能力结论。
本轮停止，不启动付费模型、优化检索或下一阶段实验。
