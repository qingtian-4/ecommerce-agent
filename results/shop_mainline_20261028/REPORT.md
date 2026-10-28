# 家电咨询和主线离线验收

基线780e9df；环境shop-realism-v1.3，业务数据仍为64商品/304订单/8身份，知识57文档。
正式用例shop-mainline-v1.1，8场景16轮，每臂36子目标；新增付费调用0。

最终证据：

- `summary.json`、`final/scorecard.json`：两臂通用路径7/8，来源限定路径8/8；证据70/72→72/72。
- `rag-probe.json`：8品类同查询6/8→8/8；这是显式检索选择，不是模型自主选择成绩。
- `final/.private-evidence/episodes.json`：原始回答、SQL与工具证据仅本机保留，不公开私有原始状态。
- `final/scorecard.json`保留36个遗漏反例，全部失败；陌生回答不自动裁决。
- `pytest.log`、`pytest.xml.gz`：完整1315通过、0失败、0跳过；既有弃用警告保留。
- `targeted-final.log`：最终合法替代证据检查与政策边界24项通过。
- `browser/report.json`：新咨询页面11/11；`legacy-browser/report.json`：旧版16/16。
- `history.json`：历史冻结数据及结果无变化；`price_review.json`：只读官方价格页面哈希。
- `pilot_proposal.json`：4段最多32次、5元、2小时的新实验方案，未授权。

失败与修正如实保留：red是未暴露咨询状态；green记录通用RAG漏政策；green2是仅用单文档
建BM25导致负IDF，green3转绿。offline是离线总账超过既有5元/48次限制的入口错误；
后续每个免费独立回放使用独立限额账本，真实Pilot仍用单一共享5元/32次账本，未放宽预算。
offline2使用原shop-mainline-v1；SX05缺预期拒绝Gold导致误判。v1.1仅修正拒绝标注，final
在同一修订协议内对照，原始v1及结果均保留。

最终评分器补充了完整宽查询、正确商品文档等合法证据路径，以及缺失权限回执的反例；
对final原始输出重新评分，0个成绩变化，见summary中的source SHA256及变更列表。
32段回放共168次Mock请求，最大正文30584字节；账本的假usage不是实际token或费用统计。

家电只能模拟咨询，不能生成可提交预览或申请。保修月末/部件语义仍待核实。此前8步和
32KB长上下文问题未整体解决。真实模型如何拆意图、选商品和检索来源尚未在此新数据上验证。
