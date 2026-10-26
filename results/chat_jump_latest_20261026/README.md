# 回到最新消息按钮

聊天区底部中央圆形箭头，向上翻阅时显示，靠近底部时隐藏。鼠标点击与键盘 Enter 均立即滚至最新消息，不发模型或业务请求。复用既有90px自动跟随边界，滚动、消息更新、布局变化和详情展开时同步状态。

- 相关HTTP测试14 passed；Chromium完整离线流程51/51，包含5种宽度。
- 费用：0次付费请求。28次SDK MockTransport属于既有离线流程。
- [桌面](jump-latest-desktop.png)、[手机](jump-latest-mobile.png)、[完整浏览器结果](scorecard.json)、[执行源码指纹及诊断](evidence.json)。
- 本轮不修改后端、业务数据库、历史评测或用户已有文件。
