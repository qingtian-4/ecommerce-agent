# 电商客服 Agent V2.1离线验收

分支fix/runtime-v21-evidence，基线462569b；与fix/tau-formal-recovery互相独立，不用于补原始V2成绩。
本轮真实模型请求0，所有新增机制默认关闭。
[整体方案及待审批续跑](../../docs/TAU_FORMAL_RECOVERY_AND_V21_PLAN.md)。

## 修复与证据

- Router不再把非拒绝请求的空能力交集当作高置信空工具集。确认/补充当前任务保留读/更新草稿，
  普通跨单物流不开放取消。源码routing.py，不按Task ID/答案制定规则。
- 新write_receipt_guard_enabled缓冲模型文字，实际调用后验证目标字段和真实回执，任何DSML文本都不会被执行。
- 本地ToolRegistry读回SQLite sources/revision；外部只使用官方回执和最近对象写前状态，精确比对字段。
- 最终确认由最新scope的结构化回执生成，旧订单/旧草稿不能冒充当前对象；历史退款不是到账凭证。
- ChatService任务卡、历史、最终answer/Trace同步；app/progress有独立write_verification显示，原答仅供诊断。
- 预览未就绪继续展示官方业务reasons；已提交申请/权限/事务/幂等/审核流程不改，取消沿用既有授权判据。

先运行独立作者样例红测9失败6通过，再修复；对象绑定反例、预览原因丢失分别有红绿证据。
早期失败全部保留：full_pytest首轮1失败（现有草稿时范围开得过宽），收窄后完整full_final为
837 passed、4个可选模块跳过；后续小修与新增例以submission_tests及新提交CI验证。
submission_tests：72通过（本地21、独立官方协议8、已有τ协议/bridge/预算等43）。
Ruff通过；最终CI以实际新SHA记录为准，不把旧日志改为新数量。

最终审查补空Registry高置信问题：empty_registry_red先1失败1通过，最小修复后按
empty_registry_green_v2重新跑73项（本地22、官方协议8、已有43）；final_replay冻结最新源码。
empty_registry_green保留一次命令文件名写错、pytest未收集测试的启动失败，不计业务红绿。
最终full_submission完整执行840项通过、退出码0；该轮在空Registry补例之前收集，
最后一行路由修正由73项专项覆盖，并由最终提交的完整CI复验（当前收集841项）。

[CODE_REVIEW](CODE_REVIEW.md)保留两轴审查，发现及修复均列出。
第一次外部专项失败包括旧bridge单工具fixture在新fallback语义下不再构成拒绝场景，
补正常order能力以继续验证真正的范围拒绝；另一次批量替换误改旁边fixture，已恢复。
取消初次新增fixture用的是既有授权判据不支持的表达，改用明确支持的“中止退货草稿”，
没有扩大取消权限；负例预期改为既有更强的“未执行取消”权威提示，不要求特定不确定措辞。

## 固定决策业务回放

success_replay_verified：原三臂全部90个成功Episode，初始DB、最终DB、工具序列、模型决定数均90/90一致。
444个最终回复有95个变化（replay_metrics.json），不能把改写当作纯收益或宣称答案无回归。
submission_replay：全部102个已评分Episode回放，包含原12失败；最终DB均102/102保持，原reward不改。
final_replay为最终空Registry修复后的同批102段回放；最终结果以该目录及verification为准。
未执行在线Agent、用户模拟器或裁判；original_reward只是源记录，并非本轮重新评分。
所有轨迹、source_cases哈希、V2.1源码哈希和明示paid=false协议各留在回放目录。

task39原V2原决定回放中，两个SKU路由都改为完整官方16工具fallback；
固定模型仍输出DSML或无回执完成声明，最终改为“尚未核实”，没有自动执行地址修改。
**修复了虚假完成展示，未证明真实模型会正确完成该任务。**
原失败如最便宜SKU选择错误不会被回执核对自动修好，不能据102/102 DB一致宣称成功102/102。

## 代价与剩余风险

- 写后整段自由回答换成真实回执摘要，附带政策、商品解释可能省略；缺材料的权威原因已保留。
- 完全没有写尝试时，只识别有限完成声明语法和DSML，不能保证任意语言隐含虚报；引用/假设样例不是全集。
- 回执证明对应状态，不证明任意官方工具外部实现没有撒谎；本店身份/授权由现有数据层强制执行。
- 缓冲延迟首个回答文字，增加SQLite读回，后续上下文改变；0额外模型调用不等于0Token/延迟变化。
  没有本轮真实用量或延迟数据；脚本usage不可当真实开销。
- 新机制默认false，保持PR #4/main未合并。离线有限范围可交付，默认启用/宣称生产可靠性No-Go。

复现：按uv.lock安装主依赖，外部τ采用原固定dd8757d4及前轮external-requirements；
`TAU2_DATA_DIR=... /tmp/acs-external-tau-env-20261022/bin/python scripts/replay_v21_successes.py
--source results/tau_retail_formal_20261022/live --out /新的目录 --include-failed`。
默认不加include-failed只回放原成功90段。命令不用Key，程序禁止socket连接，不执行官方NL付费评分。
回退本分支独立提交恢复原始V2，无数据库迁移；原始续跑在另一分支，不需要回退其安全journal。
