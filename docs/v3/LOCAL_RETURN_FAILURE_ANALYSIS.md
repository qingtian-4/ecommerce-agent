# 本轮失败与恢复分析

开发阶段原始日志保存在本机 `/tmp/v3-local-*.log`，属于离线实现调试，不是 DeepSeek 结果。后续真实实验使用全新 results 目录，历史 Pilot 不覆盖。

1. 初版尝试暴露 get_knowledge_documents，但当前 Registry 的 retrieval 模式没有注册它，首次构建报 KeyError。改为已注册 search_knowledge，复用既有 BM25 与源文档；未添加虚假工具。
2. native create_agent 的单工具线程与 custom stream 队列共享执行器，max_concurrency=1 下挂起。faulthandler 定位后采用 updates streaming，保留串行批次执行；不宣称实现即时 custom 事件。
3. ToolRuntime 是原生注入参数，严格 Pydantic 类 schema 会把它当额外字段，空参数工具还可能丢失注入。使用 JSON Schema 描述暴露给模型，Gateway 内对真正业务参数作严格 Pydantic 校验；身份字段始终拒绝。
4. Graph 旧镜像与业务 DB 新镜像不得当成正常恢复。入口核对已完成 Turn 的 Graph turn_id/最终消息和回执；运行中合法初始窗口也核对 offset。仅“本轮没有回执”不足以证明镜像正确。
5. 业务拒绝通过原生 error ToolMessage 返回，SAVEPOINT 回滚部分修改，同时保存拒绝回执。恢复后旧预览失效必须重新询问/明确重新预览，不自动创建另一个预览。

确定性故障注入是协议/一致性证据；真实模型效果另见最终报告和新实验 Scorecard。固定模型不得冒充真实模型执行。

## 真实运行：预算停止，不能标为全流程完成

`local_return_poc_20261024_run1` / 源码 `1e7f86a`：实际15次HTTP，均有usage与回执。前7阶段完成；第8阶段草稿revision5和新预览已写入，最后回答所需HTTP16在发送前被global_cost_limit拒绝。累计预留2.774388元，已知时段费用0.030671040元；两者不同不能混为账单。失败原件在私有stage-8.stderr.txt，公开Scorecard未覆盖。

`refresh`每次模型调用追加当前业务事实SystemMessage；原生历史随轮次增长，重复事实快照与预览内容又增大HTTP字节保守上界。第8阶段两次实际请求累计41267输入Token，说明当前上下文效率不足。保守预留维持峰值、缓存未命中和不退款规则，因此实际费用远小于3元也必须停止。预算门禁正常工作，不应为通关清空账本或放宽上界。

零模型请求对账确认：10个唯一回执、2个分别对应revision3/5的预览，正式四表0；Graph待model、最后Turn运行中，所有已执行写回执匹配，无未知写结果、未知费用或重复业务写入。阶段8没有最终模型答复，后续请求没有发送，也没有自动重跑。

离线第五/第六调试曾因构造的finish_turn测试消息为空或与原输入不一致而失败；另有跨用户get_order测试误把合法found=false业务拒绝当作工具异常。修正fixture与真实接口断言后，32项专项通过，最终完整CI871项0skip；没有修改官方任务/Reward或弱化越权断言。原始调试日志在/tmp保留。
