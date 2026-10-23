# 响应合同修复后的真实 Development Pilot

运行 ID：`tau_response_contract_pilot_20261023_r1`。真实模型已经执行，**Task 33 的 V3 与旧 Loop 均通过官方 DB/Task Reward，Task 34 只完成旧 Loop；V3 未启动**。累计 48 次请求达到冻结上限后停止，没有追加预算或重跑。

## 冻结与门禁

源码为已提交并推送的 `3c4ab1c5625469c0a162c9c6b886baaeb7b66889`，分支 `fix/v3-response-contract-parity`。官方 τ²-bench 固定 `dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd` / 1.0.1，原任务、Retail 初态、Policy、工具、用户模拟器及 ALL/strict_replay 评分不变。模型统一为 `deepseek-flash`，temperature=0，非思考，max_tokens=1024，SDK retries=0，单 Worker、seed=20261023。旧 Loop 显式启用 receipt_guard；新旧两臂都使用本轮共享响应合同。

付费前源码 [CI 39967136857](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39967136857) completed/success。下载并核对原始 JUnit：**839 passed，0 failed/error/skipped**，Ruff 和全量密钥扫描成功。其中 Episode 预算 48 项、响应合同 29 项、原 Adapter 42 项、Bridge 1 项。CI JUnit 及 Artifact SHA256 见 `../v3_response_contract_20261023/gates-summary.json`。CLI 下载工件曾遇到 TLS 超时，随后通过 GitHub 插件下载同一工件并校验原 archive SHA256；没有把下载错误算作产品测试失败或跳过门禁。

`manifest.json`、`gates.json`、`price_review.json` 和 `price_preflight.json` 在执行前冻结/核验。Smoke、Agent、User、Judge 使用同一持久化账本，累计 5 CNY、48 次实际 HTTP 尝试上限先到即停。单任务累计 24 请求、24 工具、80 官方步数、单次模型 60 秒、Episode 300 秒。原始响应/账本/Graph checkpoint 不充当官方业务写入证明。

## 官方 Scorecard 与费用

| 阶段 | Runtime | Task / DB Reward | HTTP 请求（Agent/User/Judge） | 工具执行 | 输入 / 输出 Token | Episode 秒 | 时段费用估算 CNY |
| --- | --- | --- | --- | ---: | --- | ---: | ---: |
| 只读 Smoke | 旧 Loop | 不评分 | 3 / 0 / 0 | 2 | 15248 / 290 | 未单独记录 | 0.002484160 |
| 只读 Smoke | V3 | 不评分 | 3 / 0 / 0 | 2 | 15248 / 287 | 未单独记录 | 0.002472160 |
| 33 | 旧 Loop | 1 / 1 | 8 / 5 / 0 | 6 | 54317 / 1158 | 24.864 | 0.009776520 |
| 33 | V3 | 1 / 1 | 7 / 4 / 0 | 6 | 46053 / 1095 | 31.290 | 0.008786920 |
| 34 | 旧 Loop | 1 / 1 | 11 / 7 / 0 | 7 | 83074 / 1964 | 37.074 | 0.015415120 |
| 34 | V3 | 未运行，null | 0 / 0 / 0 | 0 | 未运行 | 未运行 | 0 |

合计 **48 次真实付费 HTTP：Agent 32、User 16、Judge 0**；输入 213940、输出 4794、缓存命中输入 198144 Token。按请求时段/缓存 usage 估算 **0.038934880 CNY**；统一高峰价估算为 0.07786976 CNY；不可退款的保守预留为 **4.228444 CNY**。全部 48 次有 usage，未知请求为 0。上述金额是 API usage 与本轮核验价格的估算，**不是已核对的供应商结算账单**。

费率来自本轮实际读取的 [DeepSeek 官方人民币价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，快照 SHA256 见 `price_review.json`。每百万 Token 高峰输入未命中/命中/输出为 2/0.04/8 元，空闲时段减半；保守预留始终使用高峰未命中价。本轮在空闲时段执行。费用的逐角色、逐题、缓存与延迟见 `accounting-summary.json`，逐请求见 `ledger.public.jsonl`。请求 latency 覆盖 transport 收取和本地响应取证，不等于纯供应商服务时间；Episode 秒为完整执行与官方评价的墙钟耗时。

Task 33 两臂相同初态及完全相同终态哈希：`5fe996d49aefd73c215343df13c30e1d43a1b28fbba0d813e61aa9495f40e7bc`。两臂都是 user_stop，执行 6 个工具，其中 `modify_user_address` 各一次，所有调用 ID 与回执顺序吻合。Task 34 旧 Loop 由原评分器评为 1，V3 的结果不能从旧 Loop 或历史成绩推断。

两个 Smoke 的 DB 都未改变。32 个 Agent 响应中有 5 个合法 mixed content，各已执行 Episode 一次；包含 V3 Smoke 和 Task 33。合同拒绝为 0，重复 ID 派发为 0，未知写结果为 0。真实模型原始输出没有因 Guard 丢失，但本轮没有真实拒绝输出可用来展示拒绝取证；拒绝前持久化由实际 SDK MockTransport 负例测试证明。

## 未完成与证据边界

Task 34 V3 在开始前达到总次数 48，未创建该 Episode、未发请求 49、Reward 保持 null。原 `scorecard.json` 的通用状态 `not_started_after_failure_or_budget_stop` 保留原样；具体原因见 `protocol_audit.json` 和 `accounting-summary.json`。不是模型失败、不是 Reward=0，也不是完成了四个 Episode。5 元仍有空间不构成越过次数限制的授权。

任务 33/34 已用于 Adapter 开发，只能称 Development Pilot。这里只对 Task 33 有完整真实配对；没有未见任务泛化结论、总体成功率或 pass^k。两次真实模型对话并非固定输出，即使 temperature=0 也不保证供应商完全确定。Task 33 V3 少两次总请求但 Episode 更慢；缓存、对话、SQLite/日志 I/O 及运行顺序都影响结果，不能归因为质量或速度提升。COMMUNICATE/NL_ASSERTION 不适用于本次 DB 评分，Judge 没有调用，不能证明 LLM Judge 准确性。

历史 Task 33 V3 第四次响应为何触发复合守卫仍不可追溯；没有保存历史原始响应，不能因新运行 mixed 成功就断言旧运行必然是 mixed。历史目录四份关键文件和主目录三个原有文件已保留。

## 私有原始证据与重现入口

本地受限目录为本实验的 `.private-evidence/`，70 个文件均已核对文件 0600、目录 0700：48 份 HTTP 响应、Graph SDK 回调结果、三个已运行任务的原始压缩 Trace、partial、Graph SQLite 和完整账本。公开只提交脱敏结构、调用 ID 哈希和证据文件指纹，详见 `private_evidence_inventory.json`。原始目录被 Git 忽略，不上传普通 CI Artifact。

DeepSeek 本次没有提供所检查的 x-request-id/x-ds-request-id 响应头，该字段为 null；本地生成的 request_id、原始响应体 completion ID 和 Callback request_id 保留用于关联。不能声称已取得供应商请求头 ID。

```bash
# 新运行必须新目录；以下为本次实际命令，不能原位重跑 RUN_STARTED 目录。
.venv/bin/python scripts/eval_tau_development_pilot.py --freeze \
  --upstream /tmp/acs-external-tau-20261022 \
  --out results/tau_response_contract_pilot_20261023_r1
# 先准备该精确源码成功 CI 的 gates.json/JUnit 和当轮核验的 price_review.json。
# --run 会重新验证全部指纹、官方价格及预算，显式读取本地密钥文件。
.venv/bin/python scripts/eval_tau_development_pilot.py --run \
  --upstream /tmp/acs-external-tau-20261022 \
  --out results/tau_response_contract_pilot_20261023_r1 \
  --env-file '/home/qingtian/projects/E-commerce Agent/.env'
```

重复执行不是恢复机制。Graph checkpoint 与官方 DB/Orchestrator 恢复隔离，未知写操作不自动重放。原始文件只在本机保留，公共 clone 不能凭结构摘要还原完整原始会话；新实验需新授权预算和新的冻结目录。

## Go / No-Go

**Go：下一轮在隔离 DB 上连接已有退货草稿与预览，验证插问、对象更正、取消、确认过期及恢复。** 共享响应合同、拒绝证据和恢复预算阻塞已有离线及 Task 33 真实证据支持关闭。

**No-Go：替换默认 Runtime、启用真实退货提交、宣称完成 Task 34 配对或启动全量 Benchmark。** Task 34 V3 仍缺实测；完整写入还需业务幂等、真实回执和 Graph/SQLite 对账验收。没有跨系统原子恢复保证。本轮至此停止付费及业务开发。
