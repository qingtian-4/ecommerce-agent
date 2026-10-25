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
