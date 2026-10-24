# Demo 查询结果及模式披露修复验收

运行源码 `ddbef6180313157f9bc4615799414651027c1985`，指纹未变。真实Chromium复现用户截图的商品查询与订单查询，验证商品事实可见、本人订单可见、两条答案不同；固定模式和正文不是逐字流式的说明可见。

随后沿用原冻结10阶段输入，草稿/预览/更正/重启/SSE/应用确认/重复点击和SQL终态检查全部通过，0页面错误，0付费模型请求。相对数据目录启动也已真实执行。

[修复后的查询页面](query-results-desktop.png)、[Scorecard](scorecard.json)、[SQL终态](sql_final.json)、[最终页面](submitted-desktop.png)。测试器在前两阶段读取实际聊天DOM断言，不能用后台工具成功代替答案内容检查。

新增四项HTTP/Graph/SQLite回归通过；源码完整CI为[40088696789](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40088696789)，Ruff、密钥扫描及全量Pytest结果以该CI日志为准。

边界：此为固定模型展示修复，不是新DeepSeek成绩。自由对话没有在当前服务启用，真正的正文Token流仍未实现；没有伪造打字机效果或自动增加付费请求。详见docs/v3/DEMO_RUNTIME_LIMITATIONS.md。

完整CI实际日志：984 passed；Ruff和密钥扫描通过，见verification.json。业务回放与实际自然语言模型效果仍须区分。
