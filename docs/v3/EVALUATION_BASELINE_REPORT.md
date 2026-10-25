# M8 冻结基线与执行记录

开发基线953a00b的GitHub CI 40167867447成功，1048 passed/0 skipped。远端main仍f34981f，原M7为27eec1b；复用原活跃V3 Worktree，用户主目录文件与历史结果保留。

Phase0完成5ef59d5：完整1064 passed/0 skipped，6项失败先复现，修复后原有及新增反例通过。没有模型网络请求。

新增Runner的开发期失败原件保留：首次试图在Fixture Trigger安装后加订单被拒，改为全新种子初始化时加数据；商品attributes字段实际为分号文本而非JSON，评分器改按真实结构读取；首次消息审计把SDK不会发送的ToolMessage.name算成差异，依据锁定SDK序列化及配对请求修正。没有改变SQL预期或评分断言来掩盖这些错误。

执行前封存30任务/10开发20测试、25共同能力5增量、参考及源码指纹。完整实验的源码SHA、Scorecard、分集结果、失败和费用将在执行后追加。离线合同结果不回答真实模型孰优；本轮真实配对为未执行，不能复用M7.1的13次模型请求当作M8成绩。


v1封存源码634cc43已真实执行55 Episode，25对的初态/原生工具调用/SDK消息和SQL终态一致，SQL/工具合同全通过。原Task Success 22/25每臂只代表有限参考事实合同。封存后审计记录在results/m8_offline_20261025_run1/validity_review.json：55个Fixture共165笔新增订单金额不一致，及证据时钟、答复完整性盲区。不能把v1当作完整任务质量基准。

v2保留v1并作单独测量修正：24项专项（含2项新红绿反例）通过，随后完整回归及新版本封存另记。v2不是未见数据，禁止将分数变动写成模型改善。

## v2最终执行

源码5d7bbf950007edb0c5a97ac16e3281232ff19b3f，[CI 40178434018](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40178434018)成功：1088 passed、0 skipped，Ruff/密钥扫描通过。results/m8_offline_v2_20261025_run1完成55/55 Episode，25配对的初态、SDK消息/工具及SQL终态一致，5增量合同通过。只读审计金额错误0、时钟一致。

共同合同Task Success均18/25（开发6/10、来源测试12/15），原文事实正确49/52、展示51/52；相比v1的22/25下降是目标覆盖评分修正，不能称Runtime退化。每臂3/25 SQL/工具成功但原文错误，另有忠实却未充分回答的案例。没有哪个共同任务证明V3更可靠；没有观察到两臂固定轨迹的行为退化。V3应用增量能力单独5/5，不当成胜场。

两臂模型决定均103、真实HTTP均0、Token未测、费用0。52轮聊天旧Loop均值1.095秒/V3 3.150秒，额外约2.055秒/轮，含真实SQLite/Checkpoint/Trace I/O，非线上LLM延迟估计。

完整逐题SQL/工具/答复审计、有效性审计与限制见该目录REPORT.md/scorecard.json/sql_checks.json/answer_audit.json。v1与v2保留，不将v2已见复验包装成独立未见成绩。默认Runtime、原main和PR #4保持不变。
