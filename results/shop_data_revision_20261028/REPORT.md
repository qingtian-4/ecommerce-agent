# 数据质量修订交付

日期2026-10-28，基线9d2a72a。数据shop-realism-v1.2；新增付费请求0。
实现与复现说明见[数据修订](../../docs/SHOP_DATA_REVISION.md)。

| 结果 | 证据 |
|---|---|
| 64商品、304订单、8身份、56运行时文档 | data/demo/shop_realism_v1_2/manifest.json |
| 家电草案未启用，24条上游服务参考隔离 | dataset drafts/、data/demo_revision_v1/service_references.json |
| 24个家电72条规格逐字对应上游 | source_verification.json |
| 旧10个未知ANC错误、30个商品缺订单，修订后消除 | audit-final/report.json |
| 4路线、16节点序列、239个独特时间间隔组合 | logistics.json、数据回归 |
| 64/64检索文档、128/128指定事实 | audit-final/report.json；语义判断pending_independent_review |
| 两臂混合查询38订单+64商品，最大30174字节 | mixed-final/report.json |
| 详细扫描步数与长历史正文失败保留 | pressure-final/report.json；不能说16项全部成功 |
| 1291 Pytest通过，0失败、0跳过，18项既有警告 | pytest-final.log、pytest-final.xml.gz |
| Ruff通过、Chromium16/16通过 | ruff-final.log、browser-final/report.json及截图 |
| 1999个历史文件无变化 | history_and_secrets.json |

`red.log`在旧数据上复现3个数据缺陷。初次green.log的SKU列名、审查脚本的金额/文本字段、
浏览器前两次的隐藏说明读取和重复按钮定位属于本轮验收代码接入错误，修正后通过；
不计为Agent质量提升。失败日志保留，不修改既有冻结实验。

压力矩阵复用了旧shop-boundary-v1的原用例和原期望，dataset改为新版本，因此all48这一
历史ID实际扫描64个商品。两臂都在8页后用尽8步；history2在56个候选后拒绝35361字节的
下一请求，history_exhausted拒绝33445字节，均未获得最终答案。filtered、no_match完成；
last_page属于非首游标，不声称全目录完整；early_stop明确提示8/64。概览和38订单的混合
查询是本轮已验证的正常路径，不能推广为任意历史、完整SKU目录都可完成。

没有新DeepSeek、Judge、Embedding、Rerank调用。BM25和HTTP Mock复用现有Runtime。
没有验证真实模型是否会选概览、完整拆分意图、保留跨轮约束或正确应用新证据。
商品规格是固定公开测试资料的选取，不等于经过厂商认证；物流仍是合成，政策仍是草案。
