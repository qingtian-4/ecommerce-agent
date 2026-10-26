# OpenChatShop 与用户原型审查

参考仓库：https://github.com/BeamusWayne/OpenChatShop

本地只读参考：`/tmp/OpenChatShop-reference-20261026`，clone HEAD
`06721403faad713a25c18f0f9985b9156bcd098d`。没有运行其安装脚本、Agent 或数据层。
用户原型实际收到的路径为`docs/V3_customer_service_interactive.html`，与所附桌面截图一致。
该文件为用户已有的未跟踪文件，仅作设计输入，不覆盖、不擅自提交。

| 实际源码 | 已审查内容及本轮取舍 |
|---|---|
| `frontend/src/components/ChatWindow.tsx` | React/AntD聊天头、固定输入、欢迎页、连接提示，采用产品布局，不复制本地清空历史当服务端新会话 |
| `frontend/src/components/MessageBubble.tsx` | 按messageType分派订单/物流/商品卡片，采用同轮真实Tool回执分派，不信任模型自报payload |
| `frontend/src/components/rich/OrderCard.tsx` | 状态标签、明细、金额组件，改接本人订单白名单及分页 |
| `frontend/src/components/rich/LogisticsTimeline.tsx` | 时间线及空轨迹处理，改接原BusinessDB归属检查及真实合成物流 |
| `frontend/src/components/rich/ProductGrid.tsx` | 网格和缺图占位，采用明确标注的类别示意图，不编造图片URL |
| `frontend/src/hooks/useChat.ts` | WebSocket、sessionStorage、pendingMessages自动重发，后两者不作为业务真相，不移植自动补发消息 |
| `frontend-agent/src/App.tsx`、`AgentChat.tsx`、`hooks/useAgent.ts` | 坐席登录/工作台、会话队列、历史与实时对话、WebSocket更新；本项目未接坐席，仅保留明确的不可用说明 |
| `TransferStatus.tsx` | 组件存在默认等待时间及状态映射，本轮不采用缺少后端证据的预计等待/转接状态 |
| `DESIGN.md` | charcoal、单一橙色强调、字体层次/圆角；最终以用户原型为主，不原样替换为参考项目暖色canvas |

以上为源码研究，不声称参考项目的生产能力已在本机实际验收。

## React/Ant Design 评估

参考项目依赖React19、AntDesign6和Vite/TypeScript，两个前端分别面向消费者与坐席。
当前V3已有原生SSE、Cookie/CSRF、确认/恢复和Python静态服务。本轮以原生ES模块独立目录
实现消息/订单/物流/反馈/确认组件，不增加Node构建、不导入其后端或另一套运行时。
这减少合同迁移面，同时满足原型交互。未来真实坐席工作台再单独评估React重用。

## 许可证

上游LICENSE为Apache License 2.0，副本保留在
`docs/third_party/OpenChatShop-APACHE-2.0.txt`。
本轮未复制其TSX、Agent或业务实现；自行实现交互，只引用其设计和组件组织方式。
仍保留来源、固定SHA和许可证以便后续复查。若将来复制/修改其组件，须继续履行Apache-2.0
的许可证/版权/NOTICE及修改标记要求，不能将参考代码描述为本项目原创。
