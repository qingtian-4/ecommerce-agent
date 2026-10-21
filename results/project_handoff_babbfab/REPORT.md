# 电商客服 Agent 最终交付与维护验收

日期：2026-10-21；起点`babbfabbf76e8edf237c67e0a9bfad139a5bda2d`，当前功能分支
`feat/order-candidate-facts`。本轮没有改业务 Runtime、四开关默认值、历史分数或数据库结构，
没有新模型调用、τ/ECom 完整评测、合并 main 或修改仓库可见性。

**本地工程 Demo 与面试材料达到交付门槛，进入维护和面试准备阶段。GitHub PR 写入尚有登录阻塞，
不能称全部 PR 收尾已完成；默认启用及生产发布仍 No-Go。**

## 1. 分支、PR 与真实 CI

已 fetch 远端，确认单条依赖链：main `3a4de7c` → P0 `282e98b / 4b10dbe` →
P1 `4b9c3cb / d7bdb10 / a6cec14 / 92094ee` → 颜色/聚合 `c9afdbf` → 最新可靠性 `babbfab`。
两次`git merge-base --is-ancestor`返回 0；没有分叉，也不需要 cherry-pick 或重写历史。
本轮开始 src/tests 无未提交修改，先前成果均包含在 babbfab；用户 TODO、技能锁与附加标记保留。

[P0 PR #1](https://github.com/qingtian-4/ecommerce-agent/pull/1)实际 OPEN，base main，
head `4b10dbe454380d9ed906a49a1795c7bfcb258de8`，两个提交，mergedTime=null。
后续列表匿名页面仅见#1。读回身份见[github_pr1.json](github_pr1.json)。
Git2.34.1 不支持`merge-tree --write-tree`，初次调用报错后用祖先关系确认快进组合，
没有通过 checkout/stash 清理用户工作，也没有声称执行了不支持的合成检查。

真实 Actions 页面内嵌结构化状态确认 babbfab 的 Offline checks 为 COMPLETED/SUCCESS：
[运行 39571122226](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39571122226)，
[状态凭据](github_actions.json)。未认证 API 被限流，gh 保存的 qingtian-4 凭据失效。
SSH 推送能力不等于 PR API 授权；已请求维护者重新`gh auth login -h github.com`，
尚未恢复前**没有创建/更新 PR**。不是自动审查拒绝，也不是需求不明确。

[PR_HANDOFF](../../docs/PR_HANDOFF.md)与两份完整正文已准备：
[P0 更新](../../docs/pr/P0_PR_BODY.md)、[P1/可靠性后续 PR](../../docs/pr/P1_RELIABILITY_PR_BODY.md)。
建议后续 PR 先 base P0 分支，审阅后#1 用 merge commit 保留祖先，再将后续 PR 改 base main，
确认新 diff 与 CI 后由维护者决定合并。本轮不 merge、close 或变更默认配置。
本轮最终 push 的新 SHA 须另查 CI，babbfab 绿灯不自动覆盖它；最终远端核查在本轮回复中单列。

## 2. 本轮实际检查

| 项目 | 实际执行结果 | 证据与口径 |
| --- | --- | --- |
| 锁定依赖 | 独立 detached worktree 按 uv.lock 安装 66 依赖 | [install.log](install.log)，不依赖用户脏工作树或模型密钥 |
| 完整离线测试 | **695 passed、1 skipped、7 warnings，393.39 秒** | [pytest.log](pytest.log)，跳过可选 tau2；警告为 Gradio/Pandas 兼容问题，不计失败 |
| Ruff | 独立检出通过，当前最终文件另运行 | [isolated_ruff](isolated_ruff.log)、[ruff_final](ruff_final.log) |
| 默认配置 | Settings 与。env.example 四开关均 false | [verification.json](verification.json)；Demo 进程显式开启 |
| 原始业务代码/测试 | 与 babbfab 字节一致 | verification 中的源码哈希及 git diff 检查；不重复实现 P0/P1 |
| 完整页面 Demo | 商品/政策/订单、P0 订单补存、更正后刷新恢复、预览确认、审核、查询、负取消通过 | [browser_v2/summary](browser_v2/summary.json)、[后台回执](backend_receipts_v2.json) |
| 数量逐帧保护 | 注错基线 25 件可见，开启最终 29 件，15 个候选 DOM 观察帧没有 25 件 | 脚本模型、真实 ChatService/SQLite/页面；不当成真实模型成功率 |
| 统一启动 | 实际 workbench.py 客户和管理员均启动；admin 未登录 config=401，登录后=200 | [launch_acceptance](launch_acceptance.json)，本次 Key 强制为空，零付费调用 |
| 当前与历史秘密 | 全追踪扫描通过；全部本地 refs 的 1215 blob 模式扫描未命中，当前 Key 未出现在对象中 | [history_secret_scan](history_secret_scan.json)，不等同穷尽隐私审计 |
| 历史保护 | 冻结的 788 份历史结果哈希及 3 份用户工作均保持 | [freeze](freeze.json)、verification；不覆盖先前失败或评分 |

本轮完整测试在 babbfab 独立检出执行。本轮后续只有文档与验收脚本变化，核心 src/tests 不变，
Ruff 和实际页面操作覆盖新增验收路径；不为文档编辑反复跑付费或 Benchmark。
没有增加业务测试来凑数量。695 是工程回归数量，不能称业务成功率。

## 3. Demo 与录屏

[五分钟操作脚本](../../docs/DEMO.md)给出推荐原话、预期工具和后台状态。
新启动仍用`uv run python scripts/workbench.py --demo`，客户就绪后第二终端加`--admin`。
首次初始化必须串行；两个入口同库。原先用户 7860 工作台没有被停止、替换或重新播种。

实际页面证据：

- [商品/政策/订单](browser_v2/knowledge.png)：get_product、整篇 get_knowledge_documents 和 get_order，
  不声称是向量 RAG。BM25/Dense/RRF 为既有独立实现和历史对照，本轮未付费演示检索。
- [订单仅补存](browser_v2/order_only.png)：S20017 查到但脚本漏调草稿工具，P0 记录订单，未选商品。
- [刷新恢复](browser_v2/restored.png)：跨轮改为 S20002、明细 3、数量 1、无理由、未拆封，恢复后卡片相同。
- [提交](browser_v2/submitted.png)、[审核](browser_v2/reviewed.png)、[用户查询](browser_v2/queried.png)：
  本次仅一条 S20002 申请，一次 approved 审核；场景种子 S20001 仍是另一条记录，无真实退款。
- [原答/事实/最终 Trace](trace_diagnostic.png)：工具计数 20/25/29、脚本原答 25 件只在标注诊断中，
  聊天正文为 29 件。示例 usage 为脚本占位，不是真实 Token/费用。
- [客户静音录屏](browser_v2/video/customer-demo.webm)、[管理员录屏](browser_v2/video/admin-demo.webm)：
  实际浏览器自动化操作，不是五分钟真人解说，也不是付费模型自然复现。

`backend_receipts_v2.json`复核 get_product、知识文档、get_order、update_return_task、
list_orders 和被拒绝的 cancel_return_task 均有回执；P0 状态 repaired，误取消没有清空，
最终任务 submitted revision=3、S20002 一条 approved 申请与一条认证审核，全部轮次 completed。

保留本轮调试失败：第一次验收启动时客户与管理员同时播种，管理员构建目录竞争退出，
浏览器因管理员连接失败而中止，初次视频与日志保留；第二次先完成建库再启动，完整通过。
独立 Trace 截图驱动也曾因新会话/开发面板异步回调清空过早输入而超时，改为先等面板可见后再发。
修的是验收驱动等待，没有改 Runtime、放宽业务判据或把这些问题记成模型提升。
当前操作建议等待页面完成和卡片更新后再点下一步。以上支持本机固定流程，不能承诺任意并发 SLA。

## 4. 文档与指标包装

README 从历史实验流水说明收敛为定位、架构、启动、关键结果、限制与证据导航，历史材料不删。
[架构图](../../docs/DELIVERY_ARCHITECTURE.md)补 Evaluation 关系，Demo 区分 documents 与向量 RAG，
CONTRIBUTING 不再硬编码 private 或过期测试数量，运行采用锁定依赖。

[INTERNSHIP_PROJECT](../../docs/INTERNSHIP_PROJECT.md)包含四段简历、实现与集成边界、
三个完整 Badcase、两分钟与五分钟讲解、20 个面试追问及参考答案，所有数字附源记录。

| 必须保留的实验 | 如实描述 |
| --- | --- |
| 工具效率 | 相同 35 题各 3 次，共同 v4 判据，调用 160→122 少 23.8%；严格 63.8%→95.2%，平均 Token 多 4.2%，不声称纯算法因果或全面提速 |
| 多轮业务 | 历史前后 140/160 未提升；开发 34/48→37/48、验证 22/24→23/24，未判定与已知任务边界保留 |
| 官方τ零售 | 114×3×2 共 684 段；项目 86.5%/78.9%，官方同模型 87.4%/75.4%（pass^1/pass^3）；政策、数据与工具原样，不换本店商品 |
| 最新 DeepSeek | 11 已见题每臂一次 6/11→11/11，五个同次错误原答纠正，58 请求、估算 0.143 元；不是总体成功率或生产保证 |
| 工程验收 | 本轮 695 离线测试通过、脚本页面及录屏通过；不能写成真实 LLM 成功率 |

真实最新语义复核由编码助手按冻结契约完成，不是独立外部人工盲审。旧报告中的“人工复核”
不能在简历里升级为独立用户研究。评分修正与 Agent 质量改善分别说明，未重评分旧数据。

## 5. 公开准备与剩余问题

[PUBLICATION_READINESS](../../docs/PUBLICATION_READINESS.md)保存公开准备建议。
未认证能读取当前 GitHub PR/Actions 页面，本轮没有修改可见性；API 枚举受限，旧 private 描述已移除。
当前追踪约 141.9MB、最大 38.4MB 为τ raw，历史 blob 约 144.5MB；不删除原始证据或擅自改写历史。
根目录没有许可证，维护者需先决定许可与复核第三方来源后再声称有明确开源复用授权。

| 严重程度 | 剩余问题 | 对交付的影响与处理 |
| --- | --- | --- |
| 阻塞 PR 写入 | qingtian-4 CLI 凭据失效，未认证 API 限流 | PR #1 未更新、后续 PR 未创建；正文/命令就绪，重新登录后执行并读回，SSH push 可独立完成 |
| 生产高风险 | 客户是模拟身份，实验保护有限，非公网认证或通用事实验证 | 不影响合成 Demo；No-Go 生产，不允许直接开放工作台 |
| 中 | 有限约束同义表达、复杂对象/隐含统计退出，未保护自由附注仍可能错 | 已知 S20009 附注尚未通用治理；四开关默认 false，展示支持边界，不新增框架掩盖 |
| 中 | 新评分器语义未在线独立校准，真实对照小且已见 | 不声称通用提升；保留旧分，下一阶段仅在新授权任务与预算下验证 |
| 中，分发待决策 | 缺根许可证及人工隐私/第三方许可确认 | 不自行选许可证，不自动公开；没有扫描命中凭据，但模式扫描不是安全担保 |
| 低 | get_order 工具描述仍提 7 天，实际引擎已按政策配置计算 | 原政策 7 天 Demo 不受影响，未来改政策须同步该说明；见 src/acs/cs_tools.py 的工具注册。记录，不在无新功能轮继续改 Runtime |
| 低 | 首次启动两个入口并行建库会竞争；自动化操作须等异步状态就绪 | 文档串行启动、等待卡片，复验通过；保留失败日志，无大 UI 重构 |
| 低 | 既有计时波动、Gradio/Pandas 警告、历史证据较大 | 不承诺硬实时，未放宽测试或删证据；后续作为维护项处理 |

## 6. 停止条件与完成清单

1. **完成**：Runtime 不扩展，完整离线、Ruff、密钥及历史保护检查；既有机制默认不变。
2. **完成**：本机完整业务 Demo、刷新恢复、负取消、数量对照、后台凭据、真实截图与静音录屏。
3. **完成**：README、架构、运行说明、指标边界、四段简历、三个 Badcase 及 20 问材料。
4. **完成准备但远端阻塞**：PR 关系、推荐合并顺序、可直接发布的正文；因登录无效未写 PR。
5. **待维护者决策**：PR 审阅/合并、许可证、公开元数据复核；本轮不执行这些授权外操作。
6. **本轮提交推送**：通过检查后按 AGENTS 提交到当前功能分支；最终 SHA、远端 ref 与新 CI 在最终回复核实。

结论：Agent 工程实习的代码、Demo、复现、量化证据和讲解材料门槛已达到；
PR 写入依赖登录恢复，开源许可和生产部署未达门槛。进入维护与面试准备，停止新增功能。
