# Ecommerce-Agent-Bench v2.2：数据质量与长商品回执

基于 `3f618da`，分支 `codex/bench-v2.2-data-tool-receipts`。本轮仅使用现有离线组件。
数据入口为 `data/eval/commerce_benchmark_v2_2`；v2、v2.1、历史分数与真实 Pilot 原样保留。
完整结果见 [本轮交付证据](../results/commerce_v2_2_20261027/REPORT.md)。

## 数据和评分修订

60 个任务是 **12 个已见场景族各 5 个参数实例**，覆盖 170 个应用/对话步骤；不是 60
种独立复杂场景，也不是未见测试。保留 development/validation 字段便于历史对应，
这些名称不再代表未开放的评测数据。没有增加新用户模拟器或模型推理能力。

EC04-1 至 EC04-5 的第三轮 Gold、Claim span 与回放文本改为实际明细 720011、720071、
720131、720191、720251。此前 SQL 和 Claim 值正确，文字却仍是 6111；生成器仅修正
用户输入，评分器只核验 Claim 值，造成假通过。现在同一实体映射覆盖所有文本。

评分器检查**精确已标注参考答案**里的显式实体绑定；损坏的参考标注记为评分证据错误。
独立标注的历史错误答案仍说“明细6111”，但其 Claim 必须描述 6111，因此即使 SQL 正确
也判语言失败。没有扩展为任意自然语言关键词裁决；陌生措辞、否定和引用仍进入独立语义
审查，不能因未命中错误规则自动判通过。脚本标准回答通过不代表真实模型回答质量。

120 道 RAG 问题逐条校验 chunk 和原文映射。商品事实加上商品 ID；12 道多事实问题明确
询问佩戴类型、最大功率或电池容量，替代含混的“首要规格”。退货期限/运费分别要求证据，
允许政策表格或等价正文；价保与保修补充同义合法来源；配对步骤要求完整两步证据。
保留 30 篇知识原文和切分参数；没有修改 embedding 输入或生产索引/相关性阈值。
`answer_facts` 是必要事实，不要求唯一措辞。证据抽查由编码助手完成，未称独立人工盲审。
目录文档按原划分隔离；政策的合法替代证据可跨历史 split，例如保修问题引用退货政策。
不因维持分数或划分外观而排除这类合法证据，全部问题均按已见开发证据解释。

旧成绩的影响由 [独立勘误](../results/commerce_v2_2_20261027/ERRATUM.json) 标记，
不回写历史 scorecard。新旧 RAG 分数差异同时包含问题明确化和证据口径修订，不能用来
声称检索器或 Agent 质量提升。

## 商品查询合同

`list_products` 使用 `catalog-pages-v1`，旧 Loop、只读 V3 和退货 V3 共用参数校验及
实际 ToolRegistry 实现。默认按 `product_id` 升序每页最多 8 件，每页最多 8,000 UTF-8
字节；不足一页时可以原样返回全部商品。完整规格、品牌、现价/币种和 SKU 的佩戴方式、
降噪、颜色、可售状态保留。只去掉基础旧价、重复 product_id 和详情元信息。
SKU 行采用返回的 `sku_fields` 列名，列名每页只发送一次；全部 SKU 值可无损还原。

支持原文名称子串 `name_contains`、精确类目 `category`、现价上限 `max_price`，不把
自然语言条件猜成筛选值。EC01 的显式系列约束在两臂回放中使用同一个名称筛选。
返回 `total_count`（匹配数）、`catalog_count`、`count`、`has_more`、`next_arguments`，
以及完整目录/完整单次查询标记。最后一页不是“这一页就是全部目录”。每页按完整商品
边界组装，任何候选都不会被截断；单件超限返回明确错误和商品 ID，可用 `get_product`
获取完整详情。若详情或累计请求仍超预算，原 Runtime/传输层明确停止，不提高上限。

连续页在前端同一轮商品卡片中按 ID 合并，避免后一页覆盖前一页。订单权限、确认桥、
正式写入禁令、幂等键和事务代码未改变；所有正式提交测试仍走应用桥和业务 SQLite。

筛选可以减少结果，但只接受用户明确约束；字段投影只删除冗余；分页负责长度边界。
没有引入语义压缩、裁切字符串或新的 Agent/Memory 框架。目录的候选定位和自然语言推理
仍由模型负责，分页元数据本身不保证模型会正确续查。

## 免费复现

```bash
.venv/bin/python scripts/audit_commerce_v22.py --mode data --out /tmp/v22-data-audit
.venv/bin/python scripts/audit_commerce_v22.py --mode replay --out /tmp/v22-sdk-replay
.venv/bin/python scripts/eval_commerce_comparison.py --mode agents --out /tmp/v22-agent
# 使用已经缓存且 hash 匹配的本地权重；此命令不下载权重或调用模型 API。
PYTHONPATH=src /tmp/commerce-model-env/bin/python scripts/eval_commerce_comparison.py \
  --mode retrieval --model-root /tmp/commerce-model-weights --out /tmp/v22-rag
TAU2_DATA_DIR=/path/to/pinned-tau2/data .venv/bin/python -m pytest
.venv/bin/ruff check .
```

输出目录独占创建，重复运行请换新目录。`build(..., revision=...)` 可分别字节重建 v2、
v2.1、v2.2。SDK 复现使用历史 EC03 已观察的 `get_order`/`list_products` 选择，后续
决策是脚本；它验证恢复路径和请求边界，不构成新的真实 Pilot。

本阶段完成后停止。复杂多意图、实体识别和工具决策的真实基线不在本轮执行；任何新的
DeepSeek、Judge、Embedding 或 Rerank API 实验需要新的费用授权。
