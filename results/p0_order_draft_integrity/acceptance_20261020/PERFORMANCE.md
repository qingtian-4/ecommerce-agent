# 电商客服 Agent P0 状态检查性能验收

采样协议在运行前固定，全部原始样本与失败列表见 [plan.json](microbench/plan.json)、[raw.jsonl](microbench/raw.jsonl) 和 [summary.json](microbench/summary.json)。没有付费模型、网络、pytest 或浏览器任务同时运行；宿主系统后台调度仍可能影响结果。

环境：Linux-6.6.114.1-microsoft-standard-WSL2-x86_64-with-glibc2.35；Intel(R) Core(TM) i7-10700K CPU @ 3.80GHz，16 个逻辑 CPU；Python 3.13.13，SQLite 3.50.4。临时数据库位于 WSL2 的 `/tmp`，采用生产连接方法的相同设置：`{"journal_mode": "delete", "synchronous": 2, "busy_timeout": 5000, "page_size": 4096, "foreign_keys": 1}`。未调整 PRAGMA、事务或权限。

冷样本：每路径 10 个新 Python 进程和数据库副本，不清空操作系统缓存；导入、建会话和准备耗时单列。预热样本：每路径先 5 次预热、再 30 次测量，复用进程与数据库，每次使用新会话和实际生产的新连接。预热的 45 个操作保留在 raw 中，不进入以下分位数。

分位数采用最近秩；冷样本 N=10 的 p95 等于最大值，样本有限，不估计生产尾延迟置信区间。竞争每次四线程共享同一当前轮次，屏障保证均看到任务缺失，屏障等待另外记录。40/120 个线程操作分别来自 10/30 次试验，不能当作独立会话样本。

| 路径 | 状态 | 试验数 / 有效操作 N | p50 ms | p95 ms | 最大值 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| not_applicable | cold | 10 / 10 | 0.246 | 0.304 | 0.304 |
| not_applicable | warm | 30 / 30 | 0.016 | 0.028 | 0.031 |
| blocked | cold | 10 / 10 | 0.632 | 1.139 | 1.139 |
| blocked | warm | 30 / 30 | 0.450 | 0.941 | 1.001 |
| satisfied | cold | 10 / 10 | 0.924 | 1.263 | 1.263 |
| satisfied | warm | 30 / 30 | 0.660 | 1.031 | 1.111 |
| repaired | cold | 10 / 10 | 146.123 | 222.202 | 222.202 |
| repaired | warm | 30 / 30 | 150.264 | 203.031 | 241.593 |
| repaired_readback | cold | 10 / 10 | 142.272 | 222.178 | 222.178 |
| repaired_readback | warm | 30 / 30 | 150.592 | 240.255 | 258.217 |
| contention | cold | 10 / 40 | 232.267 | 434.183 | 534.682 |
| contention | warm | 30 / 120 | 233.424 | 488.460 | 734.573 |

`repaired` 包含真实事务写入、任务视图构造及提交；`repaired_readback` 在其后增加 ChatService 所需的独立权威读回。表内不含基准准备和 Python 进程启动，也不是完整 ChatService、LLM 或 UI 延迟。`not_applicable` 无数据库访问；`blocked` 缺有效订单查询，仅检查已有任务；`satisfied` 直接命中已有草稿，无写事务。

预热后的阶段耗时（真实补存加读回，N=30）：

| 阶段 | p50 ms | p95 ms | 最大值 ms |
| --- | ---: | ---: | ---: |
| intent_ms | 0.013 | 0.020 | 0.030 |
| initial_read_ms | 0.418 | 0.624 | 0.721 |
| write_connect_ms | 0.055 | 0.117 | 0.123 |
| write_lock_ms | 0.010 | 0.021 | 0.029 |
| conditional_write_ms | 0.633 | 0.929 | 0.993 |
| commit_ms | 148.977 | 238.298 | 256.486 |
| readback_ms | 0.624 | 0.911 | 0.950 |

`write_lock_ms` 是原生 `BEGIN IMMEDIATE` 调用耗时，包含固定获取开销和等待，不能精确分离纯等待。`conditional_write_ms` 是写调用扣除连接、锁、提交与屏障后的残差，包含身份、轮次、条件检查、SQL、任务视图和 Python/关闭开销。提交计时围绕真实连接 context exit；计时封装开销未扣除。各阶段分位数不能相加当作总分位数。

补存加读回的逐样本提交占比中位数为 98.78%。非竞争写锁最大 0.029 ms，读回最大 0.950 ms；冷、热补存提交均约 140 至 150 ms 中位数，因此延迟不只发生在冷启动。轻量竞争组 40 次试验均恰有一个 repaired 和三个 satisfied，最终 revision=1、item_id=null；预热竞争锁调用 p95 430.11 ms，最大 729.96 ms。竞争组的提交分布包含三个只读 no-op，不应以其中位数判断真实写入提交成本。

新样本将可重复的主要耗时定位到 SQLite 原生提交边界，竞争还会放大锁等待。提交包含持久化同步、文件系统工作和调度；没有 syscall 或磁盘追踪，不能进一步断言某个物理磁盘故障。原始 404.82 ms 未保留阶段计时，且当时与 pytest 并发，其精确成因无法事后确定；它与提交/调度尾部开销相容，不能据此算稳定 p95 或宣称只是偶发。

本轮不作性能代码改动：意图、条件 SQL 和读回均在毫秒以内；没有证据支持减少安全检查或增加新的存储结构。保留 DELETE journal、FULL 同步和原子事务。原提案 20 ms 是候选目标，新数据证明该环境的真实持久写入通常超过它；它不是已约定的产品硬性 SLO。验收建议依据正确性与披露的写入代价，不靠修改安全设置满足这个数字。

复现（输出目录必须尚不存在）：

```bash
ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python scripts/bench_return_state_check.py --out /tmp/p0-bench-new --cold 10 --warm 30 --warmup 5
```
