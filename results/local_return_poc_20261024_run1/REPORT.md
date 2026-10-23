# LangGraph 本地草稿/预览 Development Smoke

运行 ID：`local_return_poc_20261024_run1`；真实代码：`1e7f86a04db886cd275ce4298e23806e51688cf4`。
本轮确实调用 DeepSeek，不是固定脚本回放；离线32项合同与本次真实结果分别统计。

**结论：核心链路真实跑通，冻结8阶段未全部完成。** 阶段1–7通过，阶段8在草稿与新预览已提交后，最终回答的下一次HTTP被保守总预算拒绝。没有自动重跑、修改输入、提高预算或覆盖历史实验。原始 `scorecard.json` 保留 `completed_all=false`。

## 真实逐阶段记录

| 阶段 | 实际行为 | 模型请求 | 输入/输出 Token | 缓存 Token | 估算元 | CLI秒 | 状态 |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | 保存订单草稿＋回答运费 | 3 | 10312/384 | 5504 | 0.006454080 | 14.14 | 完成 |
| 2 | 跨进程补商品、数量、原因及状况 | 2 | 13619/230 | 10624 | 0.004127480 | 8.38 | 完成 |
| 3 | 商品插问，revision保持2 | 2 | 18988/142 | 17152 | 0.002747040 | 8.40 | 完成 |
| 4 | 更正为充电宝并预览，revision3 | 3 | 40318/378 | 34432 | 0.008086640 | 11.35 | 完成 |
| 5 | 政策咨询，草稿/预览保留 | 1 | 17244/161 | 16000 | 0.002208000 | 5.82 | 完成 |
| 6 | 重启后读取历史完成回答和fresh状态 | 0 | 0/0 | 0 | 0 | 2.59 | 完成 |
| 7 | 明确取消草稿，revision4、旧预览失效 | 2 | 37600/83 | 35840 | 0.002808800 | 8.70 | 完成 |
| 8 | 重新建草稿并生成新预览，revision5；最终答复未完成 | 2 | 41267/151 | 38400 | 0.004239000 | 9.44 | 预算停止 |

每阶段均为新CLI进程。输入、采样参数、政策hash、fixture日期、源码/锁文件指纹冻结在 [manifest.json](manifest.json)。本地店铺验收不使用官方 τ Task Reward，不报告 pass^k 或总体成功率。

## 费用与停止

实际发送15次付费请求，全部Agent；User Simulator/Judge均0。输入179348、输出1529、缓存命中157952 Token，模型HTTP累计21.832秒。按2026-10-24核实的官方空闲时段价格估算 **0.030671040元**；峰值口径0.06134208元。尚未读取供应商账单，因此不是精确扣款凭证。

总上限3元/32请求；保守累计预留 **2.774388元**，第16次请求预留会超过3元，在HTTP之前抛 `BudgetExceeded(global_cost_limit)`。剩余0.225612元没有被当作可重置预算。预留按峰值、缓存未命中及实际HTTP字节上界计算且不退款，与已知usage费用不同。32次请求上限未触达。没有未知usage或未结算HTTP。

失败层为费用门禁，不是响应协议拒绝、业务拒绝、重复执行或未知提交。长历史及每次模型调用追加当前事实快照增加输入和预留，必须保留这个效率问题，不能用本次低实际费用掩盖。没有继续收费修复或重跑。

## 业务与Graph对账

10次实际工具执行/10条唯一回执：get_order1、search_knowledge1、update_return_task4、get_product1、preview_return2、cancel_return_task1。两个预览分别绑定revision3、5，属于更正/取消后的不同业务版本；不存在同一call重复生成。四个正式表始终0。

最后Session Turn为running，Graph pending=`model`，turn_id匹配；已执行两个写工具的call/参数/结果与SQLite回执核验通过。只读对账未发送任何模型请求。最后预览在对账时有效，但该轮没有最终回答，不能写成8阶段全部成功。后续超过TTL应失效，本文不保证永久有效。详见 [reconciliation.json](reconciliation.json)。

人工核对阶段1–5、7答复与种子数据/政策，无虚假提交或退款声明。阶段1曾建议“或两件都退”作为用户选择，当前草稿只有单明细，不能据此宣称多商品退货能力；本轮不补业务词组规则。否定/引用/假设取消和过期恢复主要由离线反例验收，不冒充真实模型覆盖。

## 完整测试与证据位置

源码CI：[39988606570](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39988606570)，**871 passed，0 skipped**，Ruff、全仓密钥扫描成功。GitHub JUnit artifact已实际下载并验证SHA；本地32项专项实际通过，见 [offline_checks.json](offline_checks.json)。完整执行日志由CI Artifact管理，不将大量JUnit/中间日志重复提交Git。

公开：原始自动Scorecard、逐请求白名单费用账、逐阶段usage、工具结构Trace、源码指纹、私有证据清单、失败对账。私有原件在本目录 `.private-evidence/`（Git忽略，目录0700）：15份完整HTTP请求body、15份响应取证、15份SDK结果、原生Trace、Graph SQLite、业务SQLite、初始及每阶段DB快照、各CLI stdout/stderr。绝不保存请求Authorization/header，不提交Key或用户个人信息。

复现（会新建实验，不能覆盖本次目录；再次付费需新授权）：

```bash
uv run python scripts/eval_local_return_poc.py \
  --out results/NEW_AUTHORIZED_RUN_ID \
  --env-file /absolute/path/.env \
  --price-review /tmp/v3-local-price-review.json
```

`price_review.json`须依据当时官方中/英文价格页重新审查并记录响应SHA；本次快照在manifest。离线入口与fixture命令见 [LOCAL_RETURN_POC_REPORT.md](../../docs/v3/LOCAL_RETURN_POC_REPORT.md)。

**Go：继续隔离草稿/预览与Interrupt确认实验。No-Go：宣称完整8阶段验收、正式申请提交或替换默认Runtime。** 下一轮先解决当前事实重复入历史带来的预算效率问题并冻结重验，再增加显式确认消费和正式申请幂等事务。本轮到此停止。
