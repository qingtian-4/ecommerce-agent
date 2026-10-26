# 聊天排版与等待状态验收

2026-10-26，基线 `de86dd0`，独立分支 `fix/v3-chat-presentation`。

## 对照截图的改动

参考图采用右对齐用户气泡、自然展开的客服正文、轻量处理摘要与紧凑发送栏。
原页面的角色标签、独立进度卡、大段模式说明和发送提示占用了聊天空间。
这次保留系统中文字体、深墨绿和绿色动作色，使用原生 HTML/CSS/JS，不引入字体、UI 库或新服务。
检查5处 / 修改5处 / 本轮范围遗留0处：

| 区域 | 实现 |
|---|---|
| 聊天正文 | 移除可见的“你/客服”角色标签；用户气泡按内容宽度右对齐；客服正文去卡片边框，Markdown和原文不变。ARIA仍保留消息身份。 |
| 执行进度 | 进行中显示3个轻量圆点；只反映真实请求/作业/SSE状态。真实工具逐项展示请求及回执，结束后折叠为处理摘要；无工具的普通回复不留空摘要。 |
| 输入栏 | 仅保留输入框、短占位和“发送”按钮，键盘说明转为屏幕阅读器提示。Enter发送、Shift+Enter换行、IME保护继续生效。 |
| 提问示例 | 真实模型与固定模式均可看到商品、订单等原有9个示例，紧凑横向滚动。点击仅填入输入框，不自动执行模型或业务动作。 |
| 页面说明 | 运行模式仍在右上角明确显示；详细能力/费用说明点击展开，合成数据与申请风险说明保留在业务区域。 |

右侧退货卡片、权限、默认Gradio、正式提交事务及所有模型/工具协议保持原实现。
未复制参考图中的虚构处理步骤、产品图片、人工客服入口或上传能力。

## 等待和流式合同

圆点是CSS的opacity/transform动画，1.4秒周期，无定时器制造正文或工具进度。
只在SSE连接有效且实际请求/作业进行时启动；完成、失败、断线停止，恢复连接后依真实状态恢复。
开启减少动态效果时显示静态圆点，依据[W3C C39](https://www.w3.org/WAI/WCAG22/Techniques/css/C39.html)。
圆点对屏幕阅读器隐藏，原有status文字、生成中业务待核验提示及历史未核验警告保留。
这不是模型隐藏思维的转录，不宣称整个界面已获WCAG认证。

正文沿用原生SDK → LangGraph → 持久化answer_delta → EventSource链路。
模型协议拒绝仍撤回临时正文并显示错误；不会自动重发业务提交。

## 浏览器与SQL证据

使用真实Chromium、SDK MockTransport和全新合成SQLite，原任务/业务规则未改。
同一最终浏览器合同分别固定旧版HTML/JS/CSS和候选资产执行，旧版缺少相关交互，候选27项全部通过。
包含示例仅填入、键盘/IME、真实正文在终态前出现、三点动画、减少动态、执行中SSE断线注入、
重连/刷新不重复HTTP、回放工具去重、错误撤回、脚本无异常和SQL核验。
断线项通过关闭当前EventSource并注入error事件验证前端反应，再建立真实SSE连接补读；
这是明确的故障注入，不伪称本轮做了公网丢包测试。

五种宽度均无页面横向溢出：320、390、850、851、1280像素。
发送栏高度：1280/390像素宽时194.25 → 76像素；320像素宽时236 → 76像素。
示例和状态摘要的点击区域至少40像素，输入栏采用组级聚焦提示，避免重复内边框。

实际SQLite：3个聊天作业中2个完成、1个因预设length响应被真实Guard拒绝；
get_product有唯一成功回执；草稿/预览/申请/确认/业务审计均0，无正式业务创建。
完整SQL计数与工具回执摘要见结果目录。受影响专项58 passed；全量1130 passed、0 skipped，
142.23秒、18个既有警告；Ruff通过。CI结果以GitHub运行记录为准。

另在8767的现有真实DeepSeek服务上创建一个独立浏览器会话，核对模式标识、76像素发送栏和
示例填入。没有发送/messages，没有新模型HTTP，不读取或覆盖用户已有会话。
本轮自动验收费用0元，未重新运行真实语义实验；上轮真实SSE证据仍保留原报告。

## 重现与截图

```bash
/tmp/ecommerce-ui-browser-20261026/bin/python scripts/eval_chat_interaction.py \
  --presentation --out /tmp/chat-presentation-new-run --port 8773
# 同一合同下检查旧资产，预期以非0退出并保留失败项：
/tmp/ecommerce-ui-browser-20261026/bin/python scripts/eval_chat_interaction.py \
  --presentation --baseline --out /tmp/chat-presentation-old-run --port 8774
```

工具环境使用单独的Playwright 1.64.1；不变更项目依赖或冻结Benchmark清单。
模式模拟仅限离线浏览器测试的bootstrap元数据，不改变服务端授权或工具。
资产源码hash、几何测量和各次Scorecard位于`results/chat_presentation_20261026`。

[桌面](../../results/chat_presentation_20261026/after-desktop.png)、
[手机](../../results/chat_presentation_20261026/after-mobile.png)、
[等待中](../../results/chat_presentation_20261026/after-loading.png)、
[实际服务就绪](../../results/chat_presentation_20261026/live-page-live-ready.png)。

实际服务继续使用`http://127.0.0.1:8767`，Ctrl+F5重新加载三个静态资产即可，无须重置数据库或会话。

## CI首次失败及界限

源码ef30213的[CI首次运行](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40266582634/attempts/1)
出现1失败/1129通过：原有确认恢复测试before_consume_commit预期issued，实际waiting。
本轮后端.py和原测试均未改。临时诊断记录每次POST状态及失败时私有异常，独立本地复现15次
全部通过；不能因此确认是CI抖动，也不宣称业务缺陷已修复。原始失败摘要保留在结果目录。
后续CI保持原断言、原业务规则复验；最终运行状态见GitHub，不覆盖首次失败。
这是待定位的后端恢复风险，用户界面5处/27项验收不等于全项目没有遗留问题。
