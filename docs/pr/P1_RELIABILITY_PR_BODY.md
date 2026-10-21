订单工具返回正确，模型仍可能串用不同订单 SKU、忽略历史颜色限制或把商品明细数当件数。
本 PR 在 P0 之上交付 P1 事实绑定、有限颜色约束、三口径聚合和最终可靠性修复，并整理可复现 Demo。

- 单次完整本人订单结果绑定订单/明细/SKU；明确有限请求才接管，异常、重复或部分结果不报权威总数。
- 从已存本人用户原话重建有限颜色约束，明确更正/取消生效；聚合区分订单数、明细数与 quantity 合计。
- 受保护正文核验后才显示，保留原答诊断；取消草稿须当前轮明确授权，事务、确认、幂等和审核权限不变。
- 查询、预览及提交共用政策日期规则；裁判错误保留原文/原因并单列未判定，不改写历史成绩。
- 四实验开关默认 false，仅 Demo 进程显式启用；无新框架、审查模型或数据库迁移。

验证与测量边界：

- babbfab 及本轮独立检出完整离线 695 passed、1 skipped；Ruff、追踪文件和历史 blob 密钥检查通过。
- 原 P0 458 测试/8 组探针保留，最新 Gradio 实际页面完成工具查询、订单更正、刷新恢复、确认提交、
  认证审核及查询；无网络脚本注错另验错误取消与数量门控，有截图和静音操作录屏。
- DeepSeek 固定 11 个已见任务每臂一次为 6/11→11/11，58 请求，五个同次错误原答被纠正；
  费用估算 0.143 元，非总体成功率或稳定因果证据，目标首字中位观察增加约 1123ms。
- 历史τ零售 114×3×2 共 684 段结果仅作原官方环境 Loop 评价，本轮未重跑；历史本地 140/160 未提升。
- 原始失败和评分均保留。新裁判语义尚未在线复验，有限表达及自由附注仍有风险。

[最新交付](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/results/project_handoff_babbfab/REPORT.md) ·
[真实对照](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/results/final_delivery_c9afdbf/REPORT.md) ·
[Demo](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/docs/DEMO.md) · [架构](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/docs/DELIVERY_ARCHITECTURE.md) · [面试材料](https://github.com/qingtian-4/ecommerce-agent/blob/feat/order-candidate-facts/docs/INTERNSHIP_PROJECT.md)

此 PR 先以`fix/order-draft-integrity`为基线，避免重复 P0 差异。P0 经审阅合并后再改 main，
等待该次 push/PR 合成提交 CI 通过再作合并决策。不在本轮自动合并或修改仓库可见性。
