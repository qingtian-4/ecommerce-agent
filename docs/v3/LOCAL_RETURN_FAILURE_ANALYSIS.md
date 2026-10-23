# 本轮失败与恢复分析

开发阶段原始日志保存在本机 `/tmp/v3-local-*.log`，属于离线实现调试，不是 DeepSeek 结果。后续真实实验使用全新 results 目录，历史 Pilot 不覆盖。

1. 初版尝试暴露 get_knowledge_documents，但当前 Registry 的 retrieval 模式没有注册它，首次构建报 KeyError。改为已注册 search_knowledge，复用既有 BM25 与源文档；未添加虚假工具。
2. native create_agent 的单工具线程与 custom stream 队列共享执行器，max_concurrency=1 下挂起。faulthandler 定位后采用 updates streaming，保留串行批次执行；不宣称实现即时 custom 事件。
3. ToolRuntime 是原生注入参数，严格 Pydantic 类 schema 会把它当额外字段，空参数工具还可能丢失注入。使用 JSON Schema 描述暴露给模型，Gateway 内对真正业务参数作严格 Pydantic 校验；身份字段始终拒绝。
4. Graph 旧镜像与业务 DB 新镜像不得当成正常恢复。入口核对已完成 Turn 的 Graph turn_id/最终消息和回执；运行中合法初始窗口也核对 offset。仅“本轮没有回执”不足以证明镜像正确。
5. 业务拒绝通过原生 error ToolMessage 返回，SAVEPOINT 回滚部分修改，同时保存拒绝回执。恢复后旧预览失效必须重新询问/明确重新预览，不自动创建另一个预览。

确定性故障注入是协议/一致性证据；真实模型效果另见最终报告和新实验 Scorecard。固定模型不得冒充真实模型执行。
