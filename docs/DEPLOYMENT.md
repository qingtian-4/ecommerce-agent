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

已提供 Dockerfile、构建排除规则、只在数据库不存在时初始化的入口，以及健康检查。
当前开发环境的 WSL Docker 命令不可用，提示启用 Desktop 的 WSL 集成，**本轮没有完成镜像构建和容器运行验证**。
下面是待运行的客户服务部署路径，不能将本机通过等同于容器通过。

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
独立管理入口仍需配置管理账号，本文件未验证管理服务的容器部署。

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
