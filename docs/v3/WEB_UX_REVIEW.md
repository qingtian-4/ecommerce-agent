# M7.1 Web UX 审查与修复

基线 `feat/v3-deepseek-answer-stream / d3f5078`，原M7 `27eec1b`。复用原V3 Worktree，新分支 `fix/v3-demo-ux-grounding`。主目录的skills-lock、TODOS和Zone.Identifier用户文件未修改；main、PR #4、默认Gradio和原M6正式事务不变。依赖按uv.lock恢复，未升级依赖；本机恢复环境为Python3.13.13。

## 复现与改动

`results/m71_ux_baseline_20261025_run2`是冻结旧源码的真实Chromium、合成SQLite证据，0付费。普通咨询可用；UNKNOWN全页锁定、明确403仍变UNKNOWN、Markdown无结构、无订单卡片、无时区和不存在的旧预览表述均已复现。首个测试器曾读取旧DOM：API已含新答案，DOM仍是上一轮；第二次增加新job计数和用户/助手DOM轮次等待，未改变业务输入。

| 路径 | 原因 | 最小修复及核验 |
|---|---|---|
| UNKNOWN | 一个busy与readonly条件锁所有动作 | 分开sessionReady/requestPending/executionActive/connectionState/card。确认未知仍可刷新和查询；提交和修改禁止 |
| 未核实状态下聊天 | 只解锁按钮会重新开放写工具 | MessageBody只增加可收紧的read_only；应用重新读UNKNOWN并强制OR；原生Agent过滤写工具、整批响应Guard、工具执行与恢复前权限检查共同拒绝写入 |
| 4xx | 清空本地marker后又无条件设置UNKNOWN | 明确4xx只提示未受理并GET刷新。网络断开/5xx/不可解析确认响应保留marker，禁止自动重发POST |
| 读取竞争 | 先读旧history再读新completed job | 先取任务状态，再读业务与展示数据；completed不能搭配更早的未完成history |
| 慢响应 | 仅显示技术函数名 | 根据真实SSE请求/回执显示核对订单、查询政策、保存信息、生成答复；没有计时打字或虚构完成 |
| Markdown/卡片 | 所有输出仅纯文本 | 限定Markdown语法，纯DOM创建标题、列表、表格、代码和http(s)链接；不使用innerHTML、不执行HTML或图片。商品来自查询回执，订单明细由原owner校验的数据层读取 |
| 调试与恢复 | 技术Trace占主要页面；折叠后恢复按钮也被藏住 | 技术Trace收纳details，恢复业务按钮独立放在聊天区；移动端堆叠并保持表格横向局部滚动 |
| 模式 | 易误认为固定脚本是实时模型 | 固定模式/DeepSeek继续由服务端bootstrap明确披露；免费模式仅冻结用例；付费模式保留真实SDK正文流与可撤回临时文字 |

## 状态边界

五种确认状态仍来自原return-card DTO和allowed_actions。PENDING_REVIEW只由完整SQL申请、一次性确认、审计及幂等回执支持；HTTP断线或Graph文字都不是提交凭证。没有有效预览显示“暂无预览”，不暗示曾存在一个已过期预览。真实过期/更正仍为EXPIRED。

SSE连接仅订阅持久事件，重连/分页/刷新没有POST副作用。确认未知与模型执行未完成分别解释；模型预算或协议中止不会被当作一笔未知申请。服务端全局单Worker互斥保留；其他浏览器占用时可能收到明确409，不能据此自动排队重发。

## 浏览器与限制

`scripts/eval_m71_ux.py`相同输入检查DOM、HTTP次数、SQL三表终态，并截图1280与375宽度。十场景包括普通咨询、混合退货更正、UNKNOWN实际查询、403、请求丢失、正常确认、SSE刷新、COMMIT后回执丢失、真实退出恢复、数据库不可用恢复。`scripts/eval_answer_stream.py`另用真实SDK MockTransport验证正文提前可见、工具阶段撤回、协议拒绝与服务重启。结果及最终源码指纹以对应Scorecard为准。

Markdown是明确的安全子集，不宣称完整CommonMark。长记录仍需滚动；没有虚拟化、真实登录、多Worker或生产容量保证。只使用合成数据，正则脱敏不是通用PII识别器。
