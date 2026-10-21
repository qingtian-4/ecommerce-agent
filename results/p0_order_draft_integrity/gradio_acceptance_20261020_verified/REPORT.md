# 电商客服 Agent P0 Gradio 多轮验收

2026-10-20，基于已推送提交 `282e98b`，使用真实 Gradio 服务、Chromium 页面、鼠标点击和文本输入，显式设置 `ACS_RETURN_STATE_CHECK_ENABLED=true`。本次使用固定 ScriptedLLM 和独立合成临时数据库，不是实际模型质量评测；付费 API 与 embedding 请求均为 0，用户 .env、默认开关与 main 不变。

最终浏览器场景完整执行三轮：

1. 输入“我想退S20017。”，真实 get_order 后模型不更新；服务端仅补订单，卡片显示 S20017，商品明细仍为空。
2. 输入“我先确认一下，暂时不选商品。”，草稿保持 revision=1，未提交。
3. 刷新页面，在历史会话中恢复，聊天和卡片与刷新前相同；再次输入“我想退S20017。”，独立 state_check 为 satisfied，revision 仍为 1，不自动选明细或创建申请。

[浏览器原始记录](browser_final/browser_evidence.json)、[页面截图](browser_final/ui.png)、[后台逐轮快照](server_evidence.json)和[检查汇总](summary.json)均保留。截图已人工查看：当前用户、三轮对话、任务卡片、未提交提示和独立服务端检查一致。页面 usage 与模型名是现有界面对配置及假模型占位值的呈现，不是实际模型 Token 或费用。

运行前两次浏览器驱动失败也保留：第一次在刷新导航完成前读取旧 DOM，第二次在刷新后开发观察区域隐藏时读取不存在的 trace 节点。两次均为采集驱动问题；已完成的后台轮次没有删除。修正为等待新文档及观察组件出现后，整条场景通过。共执行 8 个真实页面轮次，全部任务 item=null、revision=1，无新申请。最初启动脚本使用了不存在的主题常量，失败日志在相邻 gradio_acceptance_20261020 目录，改为应用实际 launch 参数后启动成功。

申请、确认与审核的种子数量分别为 1、1、1，与独立重建的同一合成场景相同。核验脚本最初误假设种子审核数为 0，已按实际场景对照修正，未改 Agent 或业务判据。原业务事实指纹不变。不是通过重试付费模型筛选成功回答；模型决策始终固定，以上失败均保留为驱动/验收脚本证据。

复现需要已有本地 Chromium、Node（仅用于浏览器驱动）及项目 Python 环境，不新增应用依赖：

```bash
ACS_RETURN_STATE_CHECK_ENABLED=true ACS_LLM_API_KEY= ACS_EMBEDDING_API_KEY= .venv/bin/python scripts/probe_p0_gradio.py --out /tmp/p0-gradio-new --port 7872
node results/p0_order_draft_integrity/gradio_acceptance_20261020_verified/browser_driver.mjs /tmp/p0-gradio-new
```

输出目录必须未存在；服务器仅监听回环地址，Python 外部 socket 连接被拒绝，浏览器阻止 HTTPS 请求。驱动使用本机 9241 调试端口，结束时关闭自己的 Chromium。本次服务在检查后停止。原 P0 源码没有修改，458 项完整测试及 8 组配对证据沿用已验收提交，本轮不重复 P0 性能测试。

P0 PR：<https://github.com/qingtian-4/ecommerce-agent/pull/1>，base=main。PR 仍待审查与合并，默认检查仍关闭；本轮不执行 merge。后续 P1 使用独立分支，不能混入本 PR。
