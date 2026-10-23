# LangGraph 本地退货草稿与预览 PoC

实现位置：`src/acs/local_return_poc.py`（native create_agent、SqliteSaver、应用生命周期）、`src/acs/local_return_tools.py`（工具 Schema、隔离 fixture、业务幂等回执）、`scripts/local_return_poc.py`（独立 CLI）、`scripts/eval_local_return_poc.py`（冻结的跨进程真实冒烟）。默认客服入口和官方 τ Adapter 未改变。

## 可复现运行

从仓库根目录，使用锁定环境（Python 3.13，uv.lock 未变）：

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/local_return_poc.py --state-dir /tmp/local-return-demo demo
uv run python scripts/local_return_poc.py --state-dir /tmp/local-return-demo inspect SESSION_ID
```

demo 是离线固定输出，真实执行业务 SQLite 与原生 Graph，不代表模型效果。必须使用全新目录；init 不会覆盖已有 fixture。单独创建空 fixture/session：

```bash
uv run python scripts/local_return_poc.py --state-dir /tmp/local-return-real init
uv run python scripts/local_return_poc.py --state-dir /tmp/local-return-real new
```

付费 ask/resume 要求显式 --env-file、当前官方价格 SHA 复核文件 price_review.json；拒绝非直接 DeepSeek 或未核实价格。每次 HTTP 前持久预留，不退款式重复使用预算；本轮总计3元/32请求，单轮8模型/16工具保护。结果中的 historical answer 仅是历史答复，当前预览必须看 fresh business_state。

```bash
uv run python scripts/local_return_poc.py --state-dir /tmp/local-return-real \
  --env-file /absolute/path/.env ask SESSION_ID '我要退O10024，先保存订单草稿'
uv run python scripts/local_return_poc.py --state-dir /tmp/local-return-real \
  --env-file /absolute/path/.env resume SESSION_ID
```

运行中的异常 Turn 保留用于显式 resume；不会启动默认 ChatService 的自动清理。Checkpoint 与业务回执共同核对后恢复，不能用旧权限直接写入。原始请求、响应、Trace 和 DB 快照仅保存在0700私有目录，Git只提交结构化摘要；不提交API Key或真实客户个人信息。

## 验收口径

专项测试覆盖原生多轮补信息/插问/更正/批次预览、取消反例、跨用户、重复call、提交前后故障、真实进程退出、旧预览过期/政策变化、错配镜像拒绝与费用持久上限。完整 Pytest、Ruff、密钥扫描由本轮 CI 实际执行，不以依赖缺失跳过替代验收。

真实冒烟将在离线验收通过后执行，冻结脚本输入、源SHA、政策hash、模型参数和费用上限，逐轮新CLI进程；失败保留且停止后续付费，不自动重跑。最终测试数量、实际费用、失败与 Go/No-Go 在证据产生后补充。

限制：沿用整句取消授权；单 fixture 单 worker，Linux/WSL；不提供生产认证、上下文压缩、SSE服务或跨库事务。预览永远不是正式申请。

## 本轮实际验收结果

实际源码 `1e7f86a`；[完整源码CI](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39988606570) 为 **871 passed、0 skipped**，Ruff/全仓密钥扫描通过；本地32项专项248.59秒。未新增或升级依赖，锁定 LangChain1.4.7、LangGraph1.2.16、SQLite checkpointer3.1.2、OpenAI3.26.0。

真实 DeepSeek 已执行，详见 [本次完整实验](../../results/local_return_poc_20261024_run1/REPORT.md)。15次付费请求、输入179348/输出1529/缓存157952 Token；按已核实时段价估算 **0.030671040元**，非供应商精确账单；保守累计预留2.774388元。前7阶段完成；第8阶段业务revision5与最新预览已生成，最终回答前第16个HTTP请求被3元保守上限拒绝，原Scorecard明确保留completed_all=false。没有重跑或改规则。

真实覆盖草稿→跨进程补信息→商品插问→商品更正→预览→政策咨询→进程重启读取→明确取消。否定、假设、引用取消及故障恢复主要由离线反例覆盖。十条唯一工具回执、两个不同revision预览，正式申请/确认/审核/audit四表均0。只读核对Graph和SQLite已执行结果匹配，最后Graph待model、业务Turn仍running；这是明确未完成，不是跨库原子提交保证。

相对旧Loop，已验证的改进是原生Graph持久消息/执行恢复、相同call幂等回执、批次顺序及分离业务状态与咨询答复。没有本地新旧真实配对成功率证据，不宣称质量提升或无限恢复。新增代价包括独立Graph库、单worker文件锁、显式对账及消息上下文增长；事实快照重复入历史导致本次保守预算提前耗尽，是后续阻塞。

结论：隔离草稿/预览和下一轮Interrupt确认实验有限Go；完整8阶段验收、正式提交及生产替换No-Go。下一轮须先控制事实快照增长并重验冻结流程，确认令牌、提交幂等与未知结果恢复另行实施，不自动开启。
