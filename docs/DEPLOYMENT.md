# 单进程运行与数据保留

客户模拟服务使用 SQLite 持久化会话、草稿、凭证、申请与审计，最多同时处理两个模型
轮次。相同会话拒绝并发，其他会话可见排队。当前不支持多个客户服务进程共享这一
并发上限，也没有真实客户认证或支付退款连接。

## 本机入口

```bash
uv run python app.py                       # 原数据与历史，7860
uv run python admin_app.py                 # 原数据审核，7861，管理账号认证
uv run python scripts/build_scenario.py    # 独立 data/scenarios/v2，已存在则拒绝覆盖
uv run python app.py --scenario data/scenarios/v2 --port 7862
uv run python admin_app.py --scenario data/scenarios/v2 --port 7863
```

v2 固定业务日期为 2026-10-15，全部型号、规格和可用性为合成事实；不代表品牌官方
资料。使用选择整篇文档，不套用原知识库的相关性标定。旧库缺少的颜色与库存保持未知。
场景清单校验业务表和知识 hash；会话、草稿和申请属于可变化的运行状态，不据其增长
要求重建场景。更改业务种子或知识后生成新的场景目录，不覆盖旧目录。

```bash
uv run python scripts/check_runtime.py --url http://127.0.0.1:7860/
uv run python scripts/check_runtime.py --scenario data/scenarios/v2 --url http://127.0.0.1:7862/
```

检查仅覆盖本地数据库完整性/迁移、知识及索引版本、可选 UI HTTP 存活，不调用付费
API，不能证明外部模型此刻可用。`provider_checked=false` 会明确出现在结果中。
模型不可用时保留实际失败、部分回答和不完整用量，不宣称已完成或已转人工。

## 容器入口

2026-10-15已在Docker Desktop Linux引擎实际构建与运行。此前引擎未启动、Ubuntu的
WSL集成关闭；启用后原Docker Hub基础镜像请求仍返回EOF，改用官方Astral的Python镜像，
并固定版本和镜像digest。实际结果见[容器证据](../results/container_acceptance/)。
锁文件与依赖层先于源码复制，uv下载缓存独立挂载；修改文档或业务代码不再重新下载
整套依赖，避免一次源码重建因PyPI的TLS中断失败。

直接调用已安装包还暴露了知识目录指向site-packages的问题；镜像设置`PYTHONPATH=/app/src`，
确保页面、健康检查、CLI和`docker exec`都使用同一份源码与数据根目录。

```bash
docker build -t acs:local .
docker volume create acs-persist
docker run --rm --name acs-customer --env-file .env \
  -e ACS_DB_PATH=/app/persist/business.db \
  -e ACS_INDEX_DIR=/app/persist/index \
  -e ACS_TRACE_DIR=/app/persist/traces \
  -p 127.0.0.1:7860:7860 -v acs-persist:/app/persist acs:local
```

镜像排除 `.env`、原数据库、缓存、开发工具目录与本机结果；凭据只在运行时注入。
持久目录包含数据库中的上传凭证和历史，索引单独保存。首次检索启动可能需要构建
索引并调用 embedding；过期索引不静默服务，应先校验知识、模型和标定，再重建。
原评测结果没有打进镜像，界面会显示历史证据不可用；需要时单独只读挂载结果目录。
独立管理入口使用已配置的管理账号，已验证未认证拒绝、真实登录后可加载组件。
管理容器须单独指定端口健康检查，不能沿用客户容器的7860端口。

```bash
docker run --rm --name acs-review --env-file .env \
  --entrypoint /app/.venv/bin/python \
  -e ACS_KNOWLEDGE_MODE=documents \
  -e ACS_DB_PATH=/app/persist/business.db \
  -p 127.0.0.1:7861:7861 -v acs-persist:/app/persist \
  --health-cmd '/app/.venv/bin/python scripts/check_runtime.py --url http://127.0.0.1:7861/' \
  acs:local admin_app.py --host 0.0.0.0 --port 7861
```

实际验收包含：真实模型查单；上传原始凭证、预览、明确确认和审核；第三会话排队、
同会话并发拒绝；API不可用时报告失败；旧版合成数据库迁移保留15张已有表的数据；
索引构建、过期embedding模型索引拒绝；停止重启及删除容器后用同一数据卷重建。
历史、草稿/申请、审核与原始凭证hash均保留。另以检索模式启动并完成真实政策检索，
整篇文档模式通过不被冒充为检索模式通过。

```bash
# 在新建的、仅供验收的卷中执行；write会创建合成申请并审核，勿对已有用户数据重复运行
docker exec acs-customer /app/.venv/bin/python scripts/check_runtime.py --url http://127.0.0.1:7860/
docker exec acs-customer /app/.venv/bin/python scripts/container_probe.py write
docker exec acs-customer /app/.venv/bin/python scripts/container_probe.py index
docker exec acs-customer /app/.venv/bin/python scripts/container_probe.py migration
docker restart acs-customer
docker exec acs-customer /app/.venv/bin/python scripts/container_probe.py read
```

先确认上述 HTTP 就绪检查通过，再启动写入探针，避免与服务器启动时的中断恢复竞争。
`--env-file` 会覆盖镜像中的默认环境变量；必须显式设置上例的 `ACS_DB_PATH` 等持久路径，
否则容器重启可能仍能读到内部文件，但删除重建后无法从挂载卷恢复。

验收用客户/管理入口分别为7864/7865，使用独立`acs-acceptance-final-persist`卷，
未挂载或重置项目原数据库。镜像内容检查排除了`.env`、本机数据库、场景数据库和
结果目录；敏感配置只在运行时注入。这是单机容器验收，不等于公网、多实例或生产SLA验收。

已有数据库只追加迁移，不执行种子重建。升级到 schema 5 增加 SKU 表，不修改旧六张
业务表和历史申请；旧数据不会自动获得颜色。升级前使用 SQLite backup 保存完整副本，
代码回退不能删除新历史。旧代码拒绝打开更高迁移版本时，应使用兼容版本或明确选定
的备份，不能清除迁移记录强行降级。

## 预算口径

默认整轮120秒、6个step、16个外部API请求、12个已执行工具、4次知识查询。
排队计入整轮时间；对话、核验、embedding 和 reranker 共用预算。外部请求使用
可取消的异步 I/O，整个响应流有截止时间，持续发片段也不能无限延长请求；SDK 隐式
重试关闭。同步工具在执行前检查预算，SQLite 事务不等待模型；这些边界不是生产 SLA。

输入上限24,000字符按消息与工具schema的紧凑JSON计数，不是token；按完整轮次保留
最近历史，当前规则、用户问题和任务必须保留。每次 Agent 请求重新检查，核验调用也
检查输入上限。无法放下必要内容时明确停止，不截断用户问题或拆散工具消息配对。
原历史始终完整保存，不把裁剪称为摘要或长期记忆。
