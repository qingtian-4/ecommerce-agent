# 手动体验修复交付

基线`5f14b72a5adc0297627e6f33e38e9d816321222c`，本轮新增付费请求**0**。
实现、来源和复现命令见[说明](../../docs/SHOP_MANUAL_REPAIR.md)。

| 验证 | 结果 | 证据 |
|---|---|---|
| 同口径本地流式持久化 | 15.074→2.477秒，75→13次增量，最终全文一致 | [原版](baseline/metrics.json)、[最终](candidate_final/metrics.json) |
| 只读不争写锁、流式合并 | 原版2项失败，修复及流式安全11项通过 | [红](regression-red.log)、[绿](regression-green.log) |
| 订单筛选与卡片投影 | 原版5项失败，相关43项通过 | [红](order-red-verified.log)、[绿](order-green.log) |
| 完整Pytest | 1262通过，0失败、0跳过，18项既有警告 | [日志](pytest-final.log)、[摘要](validation.json) |
| Ruff、代码空白检查 | 通过，保留源数据继承的文件尾空行 | [Ruff](ruff.log)、[说明](validation.json) |
| 原有Chromium验收 | 56/56通过 | [结果](browser_final/scorecard.json) |
| 新增截图问题验收 | 19/19通过，SSE调用ID与实际回执一致、无断线 | [结果](manual_browser_round2/scorecard.json) |
| 40商品目录，旧Loop/V3 | 各40/40候选、6次Mock请求，最大32543字节 | [结果](catalog_scan.json) |
| 新数据 | 可字节重建、物流与SQL一致、权限及Trigger有效 | [测试](data-green.log)、[页面](preview-check.json) |
| 历史保留 | 248个相关已跟踪文件逐字节未变 | [证据](preservation.json) |

第一轮完整测试3项失败保留在[日志](pytest.log)：2项为工具schema增长挤占目录预算，
1项为新数据初始化字段名错误，均已修复。第一轮浏览器55/56的失败为异常结束后残留
draft标识，已修复，见[原结果](browser_round1/scorecard.json)。专用浏览器第一轮因测试
表达式触发CSP中止，改为函数表达式后完成；没有放宽应用CSP。原始JUnit私有保存，公开
JUnit已脱敏并压缩。两份早期metrics中的`journal_mode=business.sqlite`误写为文件名，
不用于性能结论；最终文件记录实际模式wal。所有耗时原数保留。

用户真实会话只做只读审计，见[耗时与费用](manual_audit.json)。首次查订单52.30秒，
模型HTTP合计6.75秒，订单SQL查询不足0.1秒。已确认主要问题是逐片写入与读取争写锁。
商品完整查询仍需要5页/6次模型请求，这部分正常开销没有被算成数据库慢。

原真实回答“10笔入耳耳机订单”漏列5笔，SQL实际15笔。新工具筛选已覆盖15笔，但历史
原文和分数未改。旧回执卡片仅按明确提及ID作展示投影，包含排除说明中的引用；不是
语义筛选器，也不能证明整段回答正确。见[独立核验](manual_answer_review.json)。

仍有的限制：修复后没有新真实模型实验，没有独立语义盲审；长历史上下文仍增长，目录
32KiB余量小；订单搜索是字面匹配；新物流仍只有一条脱敏模板。性能结果仅覆盖当前磁盘
和固定文本。完整功能回归使用内存盘临时数据库，性能对照单独使用真实磁盘。

原真实会话和额度保留；免费新数据预览明确标为固定脚本。没有重开预算或扩大写入权限。
