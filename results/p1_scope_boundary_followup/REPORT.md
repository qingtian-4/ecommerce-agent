# 电商客服 Agent P1 接管边界最终验收

验收日期：2026-10-21。固定比较基线 `4b9c3cb09735c99840c96d2db05e6084449b9a6c`；
本轮起始 HEAD `a6cec143db6db8c52a8b07bd014ca23a38afb3aa`。
全部开发仍在 feat/order-candidate-facts；**本轮没有 commit、push、merge 或开启默认开关**。
当前远端既有 a6cec14 不包含本轮未提交修复，不能将本轮通过结果归给远端旧代码。

**验收建议：本地候选通过受限入口的离线验收，可作为默认关闭的改动进入合并审阅；
不建议默认开启。** 合并操作及 P0 依赖收尾由用户决定，本轮不改 main 或 P0 PR。

## Standards

规范轴独立复审：原 P1 缺少请求边界（P1）由 user_text 完整匹配及历史护栏约束；
原嵌套协议和占位明细缺陷（P2）由既有 a6cec14 结构校验修复，本轮保留并复验。
没有发现本轮新增的规范阻塞问题。生产改动只涉及 order_candidates、ChatService、
list_orders 的现有事实透传；无新 SQL、模型、持久化结构或业务写入。

当前原话直接来自 ChatService 的 message，历史判据来自未裁剪的 snapshot.messages；
不以模型最后回答授权，不因检索或列单出现订单号创建任务。类别来自既有 get_order 的
SQL JOIN，不凭商品名猜类别。新增类别映射在开关关闭时不出现，保持原工具 payload。

## Spec

需求轴独立复审：原整体覆盖回归（P1）在本轮冻结主矩阵中消除；异常协议安全回退
12/12 保持。额外复现了历史“仅白色”限制被当前短句重置的问题，先红后修为只在新会话
首轮接管。正常后续轮、中断重试和恢复会话均回退原 Loop，不推理或改写历史约束。

需求轴未发现本轮新增的阻塞问题；判定仅限明确的首轮展示协议。多轮事实一致性、
未知问法、任意自由文本和新增元数据对真实模型生成的影响，均不在已验证结论内。

两轴当前新增阻塞发现均为 0；不能将已修的局部缺陷等同于通用客服可靠性问题全部解决。

## 冻结、失败证据和设计决策

- 复用[原冻结 15 段任务](../../data/eval/order_candidate_scope_v1.json)：七类诉求主14段
  加原始 orders-01；题目、答案事实和评分口径不改。
- 新增[22 个边界问法](../../data/eval/order_candidate_boundary_guards_v1.json)，先冻结再跑；
  正例 7 个、拒绝 15 个，覆盖否定、引用、附加筛选、统计、时间窗、混合诉求等。
- 异常工具数据沿用 12 类，另验类别元数据缺失、未知和非字典、商品名称误导。
- [冻结清单](freeze.json)保留源码起点和 143 个保护文件哈希；旧评测及用户原有修改未变。
- [生产修改前设计](DESIGN.md)、[语法原型](grammar_prototype.json)先于生产改动生成。
  本轮“先提交分析”指先提供本地可审阅文件，遵循最新要求不自动 git commit。
- 原代码真实 Loop 失败证据：[12 failed、3 passed](scope_red.log)。不是只检查函数签名。

方案 A 为理想化全量白名单：冻结矩阵仅三个明确全量请求可接管；不是上线通用分类器。
[实际 Loop 回放](policy_a/summary.json)完成主14/14、有害覆盖0，但原始两单颜色精度
回落到1/2。它虽然最短，却损失已验证的事实收益，拒绝采用。

方案 B 增加三个完整匹配模式，并按 SQL 类别形成耳机候选。首版[105 项定向通过](targeted_initial.log)，
但随后新增两轮补验复现历史限制被清空：[context_red.log](context_red.log)。
[场景](context_guard.json)先限定后续只看白色耳机，再说原始退最近耳机原话；首版仍列其他颜色。
最小修复是已有任何历史就不接管，不引入历史条件解析。初版 B 的输出和第一轮完整测试
仍留在 policy_b/ 与 full_pytest.log，未用最终结果覆盖。

## 最小代码修改

1. [order_candidates.py](../../src/acs/order_candidates.py)：有限 fullmatch，接受当前原话；
   有历史、非完整匹配、异常/混合工具、已有任务或不可读状态均返回 None。
   耳机候选要求所有来源明细的类别可识别，按 product_id 对应 SQL 类别过滤。
2. [cs_tools.py](../../src/acs/cs_tools.py)：仅开关开启时，将已有查询结果中的类别以
   product_categories 顶层映射透传；不逐明细重复类别，不增加 DB 查询或工具。
3. [ChatService](../../src/acs/chat_service.py)：传原话和 bool(snapshot.messages)。
   复用原有 answer/assistant/final Trace/finish_turn 同步；不改 P0 检查或事务。

类别协议目前只认识本店已验证枚举“无线耳机”“充电配件”；缺失或未知类别整批不接管
耳机模式，不按名字猜、不静默把未知项排除。已知配件即便改名“耳机清洁配件”，也不混入。
“最近”没有明确时间边界时，只显示按日期排序的候选并明确追问时间范围；不推断天数。
不选择商品或订单、不创建草稿/申请、不核定退货资格。

## 核心对照

每例独立复制合成 SQLite，固定模型决策和两轮输入，socket 网络被禁止。
固定 4b9c3cb 的 54 条回放与上轮原始记录逐项相同，证明不是换一套基线。
核心三臂各 54 次回放，共162次，全部无业务副作用；另保留初版B54次和旧探针8次，
不把测试内部回放并入这些任务数。没有真实模型或付费 API。

| 指标 | 固定 4b9c3cb | 方案 A | 最终方案 B |
| --- | ---: | ---: | ---: |
| 主14段被识别的行/显式声明事实精度 | 100% | 100% | 100% |
| 主14段用户任务完成 | 3/14 | 14/14 | 14/14 |
| 主14段正确回答文本被替换 | 14 | 3 | 4 |
| 其中有害覆盖 | 11 | 0 | 0 |
| 原始 S20008/S20003 两颜色纠错 | 保留 | 丢失 | 保留 |
| 原始耳机候选无关订单 | 3 | 0，但原文仍错色且候选不完整 | 0 |
| 异常12类安全不接管 | 0/12 | 12/12 | 12/12 |
| 当前轮第一次模型输入增量 | 0 | 0 | 0 |
| 当前轮第二次模型输入增量 | 0 | 0 | +226字符 |
| 主14段下轮增量中位数/最大值 | +1332.5/+1990 | 0/+226 | +226/+452 |
| 主14段两轮输入总增量 | +16091 | +653 | +7139 |
| 额外模型调用 | 0 | 0 | 0 |

所有字符增量相对各版本开关关闭的同条件对照。关闭时27条回放在固定基线和最终候选
逐项相同。B 当前轮总增加 14×226=3164字符，下轮总增加3975字符，两项合计7139；
不能只拿下轮3975与原16091比较而漏掉新增类别元数据成本。
这份快照有10种商品ID，每次成功 list_orders 的类别映射增加226字符；规模会随商品增加。

“文本替换4次”不是4次回归：三个全量请求和一个明确耳机候选请求被正确重排；
其他10个原正确回答在固定假模型下保留原文。真实模型可能受额外类别字段影响，
不能把这个逐字不变结论推广到线上。

| 用例 | 候选协议完成：旧→新 | 无关订单：旧→新 | 下轮字符增量：旧→新 |
| --- | --- | ---: | ---: |
| list-all | 1 → 1 | 0 → 0 | 226 → 452 |
| list-subset | 0 → 1 | 18 → 0 | 1767 → 226 |
| category-headphones | 0 → 1 | 3 → 0 | 692 → 226 |
| category-accessories | 0 → 1 | 13 → 0 | 1434 → 226 |
| colour-white | 0 → 1 | 13 → 0 | 1432 → 226 |
| colour-not-white | 0 → 1 | 4 → 0 | 694 → 226 |
| status-shipped | 0 → 1 | 16 → 0 | 1565 → 226 |
| status-delivered | 0 → 1 | 12 → 0 | 1233 → 226 |
| count-orders | 0 → 1 | 0 → 0 | 1990 → 226 |
| count-items | 0 → 1 | 0 → 0 | 1989 → 226 |
| return-all | 1 → 1 | 0 → 0 | 201 → 427 |
| return-headphones | 0 → 1 | 3 → 0 | 667 → 384 |
| short-colour | 0 → 1 | 19 → 0 | 1975 → 226 |
| short-full-control | 1 → 1 | 0 → 0 | 226 → 452 |
| legacy-orders01 | 0 → 1 | 3 → 0 | 1618 → 1371 |

任务完成按原冻结契约判定，颜色/类别/状态/统计/短答分别检查对象集合和输出形式。
统计中的订单都与总量有关，无关订单为0；旧版多出的25行记 unwanted_rows，而非无关订单。
事实精度只覆盖固定模板中可识别的明细行、颜色与计数，不审查任意附加自然语言。
原始 orders-01 的原自由文本只审计两项颜色声明，不与主14段混合求平均。
新原始候选协议完成不代表“最近”时间筛选已完成；时间范围仍显式待确认。

## 最终离线检查

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 请求边界、协议、P0共存、UI恢复定向 | 106 passed | [targeted_final.log](targeted_final.log) |
| 当前功能分支完整 pytest | 564 passed、1 skipped、4 warnings；exit 0 | [full_pytest_final.log](full_pytest_final.log) |
| Ruff | 通过 | [ruff_acceptance.log](ruff_acceptance.log) |
| 最终B配对与安全回退 | 有害覆盖0、异常12/12、业务不变 | [summary](policy_b_final/summary.json)、[raw](policy_b_final/raw.jsonl) |
| 旧四组原始/改色/用户/正确事实探针 | 通过；按当前SQL类别检查精确候选集合 | [legacy_probe.log](legacy_probe.log) |
| 原始两单颜色绑定、25条完整明细、UI Trace和恢复 | 通过原定向测试 | [tests](../../tests/test_order_candidate_facts.py) |
| 哈希及净收益闸门 | 全部通过 | [verification.json](verification.json) |

pytest 的跳过来自未安装的可选 tau2；4个警告为Gradio/Pandas弃用提示，未改依赖。
初次 Ruff 的一条长行失败也保留在 ruff.log，修正后通过；没有覆盖失败日志。
最终完整测试在当前功能分支进行；用户原有变动仅 docs/TODOS.md 与 skills-lock.json，
两者哈希保留，未用于提供代码或修改测试结果。

## 实际允许与拒绝边界

所有允许问法都必须在**新会话首轮**，且满足单次有效 list_orders、无既有任务、
状态可读、无混合工具/检索/错误。之后的轮次和恢复会话全部留给原 Loop。

| 当前原话示例 | 是否接管 |
| --- | --- |
| 请列出我的全部订单和全部商品明细。 | 是，完整明细 |
| 请展开全部订单和商品明细，不要只给一句话总结。 | 是，完整明细 |
| 我想退货，请先列出全部订单和商品明细让我选择。 | 是，全量候选，仍待用户选择 |
| 我想退耳机。 | 是，有据的耳机候选澄清 |
| 我想退最近买的耳机。 | 是，但明确时间范围待确认，不执行时间筛选 |
| 我想退耳机，请只列耳机类商品的候选明细让我选择。 | 是，同一受限耳机候选协议 |
| 只列耳机/充电配件的订单、只看白色、排除白色 | 否，一般筛选回原Loop |
| 只列已签收/已发货、S20008/S20003、统计笔数/件数、一句话回答 | 否 |
| 最近7天、上周、否定列单/退货意图、引用、混合额外条件、未知/英文问法 | 否 |
| 已有任何会话历史，即便原话与允许例相同 | 否 |
| 异常明细、缺失/未知类别（耳机模式）、已有任务或工具错误 | 否 |

同义表达并非全部支持；完整匹配不识别就回退，不扩大关键词猜测。
一般类别筛选不接管，明确的耳机退货候选是上述唯一有限类别例外。

## 已解决、未解决和下一步

已解决：被动全表覆盖筛选/数量/短答；耳机候选混配件；当前短句重置历史限制；
并保持既有异常数据安全退出、来源绑定和终态同步。P0源文件、权限、事务、确认、幂等
及状态语义未变；ChatService 的 P0代码段未改。没有新增任何业务写入。

仍存在：任意自由文本事实错误、未见问法识别率、既有历史/中断重试/恢复后的候选事实
不受本展示器保护，原Loop可能仍犯错；“最近”仍需时间澄清；已流式展示文字无法撤回；
只保护ChatService，直用Agent及τ适配不受保护；空明细/未知类别保守回退；
新增类别信息对真实模型决策和成本的影响尚未验证。未声称通用多轮一致性已解决。

下一步**值得进行单独授权的小规模真实模型评测**，不需完整τ或大规模Benchmark。
先冻结代表性的6–8段任务及预算，比较开关与基线，覆盖原错误、正确短答、否定、
限定时间和带历史限制的恢复；检查真实模型工具选择、最终正确性、首字/总延迟、
真实 usage 和费用。当前所有任务已见结果，不能再当未见验证；真实模型结果另存，
本轮没有执行或承诺真实成功率/Token收益。是否评测及预算由用户决定。

本轮工作可由 git diff 审阅，尚未提交；关闭 ACS_ORDER_CANDIDATE_RENDER_ENABLED 并重启
可停用，原会话和业务数据保留，无数据库迁移。需要撤销本轮代码时只撤销本轮自有文件
对应补丁，保留用户修改及此前a6cec14的结构修复，不用reset/clean或改写历史。

复现命令（输出目录需全新，基线运行可复用已存在的固定detached工作树）：

```bash
ACS_REVIEW_RUNTIME_ROOT=/tmp/acs-p1-review-4b9c3cb .venv/bin/python \
  scripts/review_order_candidate_scope.py --cases data/eval/order_candidate_scope_v1.json \
  --out /tmp/p1-baseline-fresh
ACS_REVIEW_RUNTIME_ROOT=/tmp/acs-p1-review-candidate .venv/bin/python \
  scripts/review_order_candidate_scope.py --cases data/eval/order_candidate_scope_v1.json \
  --out /tmp/p1-full-only-fresh --full-only
.venv/bin/python scripts/review_order_candidate_scope.py \
  --cases data/eval/order_candidate_scope_v1.json --out /tmp/p1-final-fresh
.venv/bin/python -m pytest tests/test_order_candidate_scope.py \
  tests/test_order_candidate_protocol.py tests/test_order_candidate_facts.py
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

方案A使用a6cec14等价源码的隔离运行树，仅验证理想化全量接管；不能把它与最终候选源码
混用。副本源码在运行前已对git对象验证。其环境和结果与本轮当前分支完整测试分开记录。
