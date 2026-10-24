# Context 优化后的冻结8阶段真实验收

运行ID `local_return_context_20261024_run1`；真实代码 **3090695135ca6d4235eb9f35e966b223a5f08923**。输入逐字等于历史run1，业务种子/模型/采样/政策相同；新目录、新账本，未覆盖旧失败。单次开发期实测，不是正式Benchmark或pass^k。

**8阶段全部完成，包括第8阶段最终答复。** 这是实际DeepSeek请求，不是脚本回放。前置CI [40046792295](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40046792295) 为878 passed，0 skipped，Ruff和全仓密钥扫描成功。

| 阶段 | 业务结果 | HTTP | 输入/输出Token | 估算元 | CLI秒 | revision |
|---|---|---:|---:|---:|---:|---:|
| 1 | 保存订单草稿＋运费咨询 | 3 | 10018/401 | 0.005350000 | 13.63 | 1 |
| 2 | 补耳机、数量、原因、状况 | 2 | 12151/284 | 0.005007960 | 9.04 | 2 |
| 3 | 商品插问，草稿保持 | 2 | 14936/161 | 0.003161440 | 8.30 | 2 |
| 4 | 更正充电宝＋预览 | 3 | 28468/377 | 0.008400320 | 12.04 | 3 |
| 5 | 政策咨询，预览保持 | 1 | 11370/229 | 0.002627120 | 6.11 | 3 |
| 6 | 新进程读取历史答复及fresh业务状态 | 0 | 0/0 | 0 | 2.59 | 3 |
| 7 | 明确取消，旧预览失效 | 2 | 22592/92 | 0.003140480 | 8.41 | 4 |
| 8 | 重新保存耳机草稿＋新预览＋最终答复 | 3 | 39697/367 | 0.007923400 | 11.07 | 5 |

每阶段为新CLI进程，阶段6不重复模型请求。模型HTTP累计23.044秒；CLI合计包含启动、免费价格核验、SQL/Graph持久化。逐阶段缓存和累计预留见per_stage_usage.json；完整逐请求usage/状态/延迟/费用见ledger.public.jsonl。

## 费用与上下文

16次实际HTTP尝试，全部Agent；User Simulator/Judge均0。输入139232、输出1911、缓存113536；总Token141143，平均每阶段17404输入/17642.875总Token。保守累计预留 **2.264664元**，usage时段估算 **0.035610720元**；峰值估算0.07122144元。费用非供应商账单，真实精确扣款未查询。所有usage齐全，无失败HTTP、未结算或未知消耗，无停止原因。3元/32尝试上限与发送前持久预留完全保留，没有释放额度或虚构缓存。

旧run1为15次请求、第8阶段未完成；新run完整16次。因此全记录总Token180877→141143（-21.97%）并非等工作量比较。共同完成的阶段1–7，两侧均13请求，输入138081→99535（-27.92%），估算费用0.026432040→0.027687320（+4.75%）。新全流程费用高于旧不完整流程16.11%，不能宣称费用降低。缓存命中率88.07%→81.54%，且输出增加，是本次观测差异，不推断长期效果。

private原始请求逐一按body SHA核验，新每次只有1份临时事实，Checkpoint中业务System消息0；旧冗余历史事实占wire29.89%，新为0。保留全部合法原生用户/模型/工具消息，不删除回执。组成估算和离线相同决定对照见../context_engineering_20261024。

## 最终答复及业务对账

第8阶段最终答复346字，描述第5版耳机1件的新预览，含最新preview标识，并明确“没有”创建申请、提交、审核或退款。编码助手对照最新工具回执核验，不是独立人工盲审或LLM Judge；公开不复制完整答复。

补充的初版“未/不”词组探针没有识别“没有”，不能据此把模型判失败；保留私有probe原件，reconciliation.json明确区分初始探针False与实际句义核对。**原冻结用户输入、原Scorecard和业务判据没有修改**，Scorecard始终8项True。未使用改评分/重跑获得成功。

10条唯一工具回执、10次实际执行：get_order1、search_knowledge1、update_return_task4、get_product1、preview_return2、cancel_return_task1。两个预览绑定revision3和5，没有同call重复写。正式四表均0。最终Graph无pending、Turn completed、turn_id及写结果和SQL回执匹配；最新预览在阶段结束时有效，随后按10分钟TTL正常失效，不保证本文阅读时仍有效。

## 原始证据和复现

本目录 `.private-evidence/` 为0700，Git忽略；包含16份请求body、16份HTTP响应取证、16份SDK结果、原生Trace、Graph SQLite、业务SQLite、初始+8阶段DB快照、每CLI stdout/stderr及初版探针。raw文件不公开，清单SHA可核实本地原件。公开只保存结构Trace、数值账本、冻结条件与核验结果。

实际命令：

```bash
.venv/bin/python scripts/eval_local_return_poc.py \
  --out results/local_return_context_20261024_run1 \
  --env-file '/home/qingtian/projects/E-commerce Agent/.env' \
  --price-review /tmp/v3-context-price-review.json
```

目录已存在，入口拒绝覆盖；未来重验须新运行ID和新的付费授权。收费完成后未再发送真实请求；Interrupt在独立离线fixture执行，不能冒充本次真实模型成绩。

结论：Phase D通过，允许本轮进入隔离Interrupt；不授权真实申请提交或生产替换。
