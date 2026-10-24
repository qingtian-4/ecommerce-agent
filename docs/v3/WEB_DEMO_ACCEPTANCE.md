# M7 可交互售后Demo验收

实现：`src/acs/web_service.py`、`src/acs/web_assets/`、`scripts/v3_web_demo.py`。默认Gradio入口、app.py、main、旧PR #4及历史实验保持原样。

```bash
uv sync --extra dev --extra v3 --extra tau --frozen
uv run python scripts/v3_web_demo.py --state-dir /tmp/v3-web-new --init
# 浏览器访问 http://127.0.0.1:8767；首次创建新合成Fixture，重启删除--init参数。
```

默认是固定脚本模式：展开“演示用例”，按编号发送即可操作完整闭环；自由问题会明确告知固定脚本限制，不宣称是模型回答。真实模型必须显式--deepseek和--env-file，且Fixture内已有核实的price_review.json；沿用已有EpisodeLedger/MockTransport/SDK取证，硬上限3元、24次HTTP尝试，未知或无usage请求保留预留，不重置旧账本。

页面为单一同源HTML/JS，不引入React、CDN或新前端框架。浅色、深绿重点，连续对话在左、草稿/预览/显式确认/权威回执在右，窄屏上下排列。公共工具进度来自真实节点更新。确认按钮与聊天输入分开；申请ID只来自核验SQL，不根据模型回答猜测。

## 验证方式

1. `tests/test_web_service.py`：真实FastAPI TestClient+Graph+SQLite，禁止模型网络；覆盖HTTP/SSE、CSRF、跨会话、幂等、终态事件事务、COMMIT前后异常、持久确认恢复、过期/更正/政策、Graph损坏和业务库不可用。
2. M6的39项原子提交专项及全部既有回归保留。原SDK MockTransport预算测试参数化32/24，分别证明第N+1次在HTTP前拒绝、重开账本仍拒绝。
3. `scripts/eval_web_demo.py`：真实Chromium点击、刷新、SSE断开重连、服务进程重启、显式应用确认、重复点击和SQL终态；1280/375/320宽度检查，保存桌面/移动截图。浏览器依赖仅用于外部验收环境，不加入应用运行依赖。

```bash
# 已有Playwright环境，或单独测试环境安装Playwright及Chromium后：
python scripts/eval_web_demo.py --out /tmp/v3-web-browser-evidence --port 8768
# 脚本启动应用使用本仓库.venv/bin/python；浏览器测试进程可来自独立环境。
```

离线固定脚本、真实模型与UI视觉检查分开记录。新运行目录保存Scorecard、源码SHA/文件指纹、逐阶段SQL、截图、完整私有Trace和浏览器状态；浏览器Cookie/CSRF和原始请求响应仅留在受限、Git忽略的.private-evidence目录。

本轮发现与失败均保留：严格CSP不允许浏览器测试使用字符串eval，修正测试函数而不放宽CSP；测试函数拼接错误另存失败运行；业务库缺失时默认sqlite连接曾建空库，回归先红后改为只读打开；确认issued/waiting恢复与事件终态竞争均补合同测试。最终运行记录和Go/No-Go在本文件及results报告结尾收敛后补齐。

## 已完成的离线验收

`results/web_demo_offline_20261024_run4/scorecard.json`：真实Chromium固定模型10阶段全通过，0 pageerror，源码运行期间未变；提交/确认/审计/回执各1，重复点击SQL不变。桌面/移动截图已人工查看，320/375/1280无横向溢出。

HTTP/Graph/SQL专项17项通过；真实服务进程在COMMIT前后退出2项通过；SDK MockTransport的24/32预算2项通过。完整原回归与CI作为真实付费冒烟的门禁，未通过前不调用模型。付费结果将单独存新目录，不复制历史模型成绩。

服务退出补充：默认Uvicorn会等待长SSE连接；启动器设置5秒连接退出宽限，后台executor在lifespan关闭时仍等待实际任务收尾，连接取消不重新执行任务。

独立对抗审查另发现并修复两处问题：固定脚本重复输入的Tool Call ID与恢复游标，采用原生HumanMessage.id前缀和持久AI历史计数；多标签页可能收到旧预览与新等待的混合快照，服务端标refresh_required，页面额外核对两preview_id一致才允许接受。两项先运行红测，再连同HTTP工具回执丢失恢复共3项通过；最终源码的浏览器重验与CI另行归档。run4是修复前已通过的完整业务证据，不能把其指纹冒充后续源码。
