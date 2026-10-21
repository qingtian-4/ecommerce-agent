# 电商客服 Agent 外部评测兼容性

2026-10-21，仅静态审查，不安装或运行外部环境，不开发Adapter，不重复τ零售实验。
来源为[XiaoduoAILab/ECom-Bench](https://github.com/XiaoduoAILab/ECom-Bench)，固定提交
`bc5018daaf45eea12330941bce68cebd293cfa85`，Apache-2.0。只保存聚合分析，不复制外部用户数据。
[静态计数证据](../results/final_delivery_c9afdbf/ecom_static.json)由AST解析生成，没有执行上游代码。

## 数据与执行契约

该提交[公开任务文件](https://github.com/XiaoduoAILab/ECom-Bench/blob/bc5018daaf45eea12330941bce68cebd293cfa85/envs/story/tasks.py)
包含**53个Task**，每个绑定用户、店铺、平台、画像和多诉求，不等于53轮请求。
这是当前公开代码的计数，不替代论文可能采用的完整数据规模。
[运行环境](https://github.com/XiaoduoAILab/ECom-Bench/blob/bc5018daaf45eea12330941bce68cebd293cfa85/envs/story/env.py)
通过stdio MCP启动隔离JSON数据服务，用户模拟器与Agent交替对话，默认最多20轮。
工程依赖含LangChain/LangGraph，但它们是外部实现，不要求本项目迁移。

## 工具与业务范围

| 上游接口／任务 | 本项目可复用部分 | 缺口或限制 |
| --- | --- | --- |
| get_user_orders_info / manage_order查询 | Loop工具协议、身份由应用绑定、订单查询思想 | 平台/店铺/用户标识及返回结构不同；取消/修改订单不支持 |
| get_logistics_info | 物流事实查询 | 不支持加急或实际物流操作 |
| get_product_info / compare_products_info | 商品目录与RAG依据 | 不能将本店耳机数据替换官方商品来声称官方成绩 |
| manage_return | 草稿、预览、明确确认、审核 | 官方写入协议与本地申请不同；禁止绕过本项目页面确认去匹配评分 |
| manage_exchange / get_repair_info | 政策咨询 | 无实际换货或维修登记 |
| installation / auxiliary_materials / fault_code | 检索工具协议 | 本店KB无外部设备安装和故障码资料 |
| discount / gift / cashback / ecard / invoice | 可复用一般工具Loop | 未实现这些业务，不纳入当前产品Scope |
| get_image_info / schedule_service / manage_urgent | 照片上传的格式校验 | 没有视觉理解、上门预约或加急执行，上传不等于图片事实核验 |

当前公开任务有8类期望写动作和13类检索动作，出现次数不是独立任务数。
例如ecard期望动作30次、订单动作27次、预约17次、退货10次，不能据这些次数推导53题
中有多少题完整可做：一题可同时包含多个域。逐题适配前只能给部分接口覆盖，不能给通过率。

## 评分差异与Adapter代价

源码calculate_reward把动作、检索和输出三个二值分相乘；时间分单列，不参与最终乘积。
动作分比较隔离业务数据状态，检索分要求工具名和参数哈希集合包含全部期望，输出分是
预期字符串在所有assistant文本中的包含检查。它没有替代本项目逐帧约束、明确确认、
审核权限或语义裁判校准；仅调用“类似”工具不保证官方参数匹配，关键词通过也不证明事实正确。

未来最小接法应参考已有τ适配：保留官方环境/用户/工具/评分，复用真实Loop与预算，
把MCP异步工具桥接成Registry；不是数据替换或任务重写。ChatService当前四种保护与本地
SQLite/SKU协议绑定，不可未经适配就声称外部已受保护。预计只读桥接及冒烟需1–2工程日；
完整写接口、状态隔离和复现口径预计另需数日到两周，此为静态工作量估计，未做原型验证。
缺失业务应明确不支持，不能为得分增加退款、订单修改等当前Scope外功能。

## 调用预算估计与决策

53题×两臂×三次=318段；若每段平均10个用户轮次，每轮1次模拟用户与2次Agent请求，
约9540次模型调用。假设每调用输入6000、输出300 Token，按DeepSeek Flash高峰
输入未命中2元/百万、输出8元/百万估算，约137元；20轮场景约275元。
这些是假设预算，不是实测；上下文增长、工具链、图片、空回复重试和其他模型会改变费用，
上游空回复循环也不是严格的请求上界。它明显不属于本轮1元在线预算。
价格来源：[DeepSeek官方价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

决策：本轮保留已有τ结果，完成本地可靠性及Demo。只有确需验证多平台、多模态或
这些额外业务，才另行冻结官方子集、预算与隔离环境；不自动开发完整Adapter或运行全量。
