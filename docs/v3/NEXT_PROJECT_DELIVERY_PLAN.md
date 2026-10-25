# M8 后续交付和真实Pilot门禁

本轮无新增付费授权，停在离线交付。历史M7共享账本/截止时间不复用，不为它创建替代账本。本轮Runner默认offline；live入口在读取凭据、创建输出目录和发送HTTP前要求新的显式授权。

## 后续六任务提案（不是授权）

冻结D02/D03/D04/D05/D07/D09共同能力任务，每臂一次，旧Loop→V3，单Worker、固定任务顺序，共12 Episode。优先候选澄清、补信息、混合咨询、更正、否定和引用取消。两侧同模型配置temperature0/non-thinking/max_output1024，保留同一Schema和业务规则；云端模型别名不证明权重永不变化，必须记录返回模型名和价格快照。

请求上限建议48，全实验最多5元；每Episode最多4次HTTP，1024输出Token，真实序列化请求最多20000 UTF-8字节。沿用现有2*bytes+4096保守输入界，即44096 Token，峰值现有价目假设每请求0.096384元、48次4.626432元。不是账单估计；价格必须重新核实并与现有传输价目一致，否则停止。超过请求长度、额度、时限或单题预算就中止，不临场扩限保证完成。

授权文件必须绑定本次完整源码SHA、上述任务/两臂、唯一run_id、唯一绝对output_dir、cap_cny、max_requests、expires_at及真实用户授权引用。它是受信任本机操作者的执行凭据，不是生产认证；代理不能自行制造授权。输出目录必须不存在，重复执行不会换账本重置。HTTP边界再次检查授权截止时间，EpisodeLedger仍在每次HTTP前持久预留。未知/失败usage不免费，失败不自动重跑。

```bash
# 只有获得新的明确授权并完成独立价格核验后，才由操作者填写授权文件：
uv run python scripts/eval_v3_multiturn.py --mode live \
  --ids D02 D03 D04 D05 D07 D09 \
  --out /absolute/path/m8-new-authorized-run \
  --authorization /private/path/new-authorization.json \
  --price-review /private/path/fresh-price-review.json \
  --env-file /private/path/model.env
```

六题均来自开发集；不宣称未见泛化。新自然语言进入匿名审查队列，SQL成功与待判定语义分别展示。之后再考虑独立人工留出任务和重复试验；没有足够重复次数不报告pass^k。

## 项目交付

可交付：本机合成Demo、源代码、事务/恢复合同、真实模型历史冒烟及其缺陷、可复现评测脚本。README/简历应明确“自定义Loop与LangGraph对照”“模拟待审核申请”“有限展示核验”；不要写“V3显著提高成功率”“消除幻觉”“生产退货办理”。

下一步先完成独立语义审查及新授权Pilot，量化误纠正与总链路成本，再决定是否值得扩大Evaluation。暂不增加新Memory、Router、前端重写或业务功能。


v1已见数据发现的Fixture与目标覆盖问题在v2单独修正；保留两版，不用新结果覆盖旧分数。后续Pilot只能称开发期对照，不能称独立未见测试。当前必要答复词项检查仍保守，真实新语言一律保持待审。

2026-10-25只读核对[官方CNY价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，deepseek-flash对应DeepSeek-V4.1-Flash-1017，峰值每百万输入2元/输出8元，与本地预算保守价一致。执行时仍需重新保存并核对CNY/USD页面指纹；本次浏览公开文档不包含任何付费模型请求。


## 最终Go/No-Go

合成Demo、离线对照代码及诚实的README/实习材料交付Go；可以描述“原生Interrupt、SQL原子幂等与可复现合同评测”。不可写“模型成功率显著提升”“消除幻觉”或“真实电商平台售后”。

独立模型质量结论No-Go：本轮付费请求0，v2又是已见题复验；25对固定轨迹行为打平，额外聊天执行开销约2.055秒/轮。还不能证明真实Token/Latency收益值得新增状态/适配复杂度。

立即处理的有效性缺陷已在v2修正并用反例验证：金额、证据时钟、忠实却未回答目标。下一步优先完成独立人工目标/事实审查、本地错误参数与中断的跨臂协议覆盖，再以新授权执行六题Development Pilot。历史预览表达漏检、临时流错误风险及Graph镜像待对账保持明确限制；不在当前冻结任务上临场扩词表涨分。长期外部新任务、更多独立重复和生产容量等留待以后。

T20的SQL回执已恢复，但Graph镜像仍reconciliation_required；不以此声称Graph已经自动修复。已知挂起镜像的业务恢复与后续Graph修复应分别验收，不能通过重放业务写操作补镜像。
