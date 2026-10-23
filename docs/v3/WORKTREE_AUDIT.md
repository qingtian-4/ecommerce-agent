# Git 与 Worktree 安全审计

审计基线：`93db5cf`；本轮复用 `/tmp/ecommerce-v3-episode-pilot-20261023`，建立 `fix/v3-response-contract-parity`。没有新增 Worktree，没有删除目录、stash 或历史分支。

## 提交关系与远端

`576f56f → d7da103 → 93db5cf` 是连续的单父提交链，每段仅一个提交。本轮执行 `git fetch origin` 和 `git ls-remote --heads`，远端对应三条 V3 阶段分支分别停在上述 SHA；main 为 `f34981f`。三个提交均已推送。基线 `93db5cf` 的 push CI 已核验为 completed/success：[运行 39954568738](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39954568738)。本轮分支 CI 在提交推送后另行核验。

## 全部注册目录

| 目录 | HEAD / 分支 | 未提交状态 | 忽略内容 | 处理 |
| --- | --- | --- | --- | --- |
| `/home/qingtian/projects/E-commerce Agent` | `576f56f` / `feat/v3-langgraph-poc` | 有未提交内容 | `.agents/，.claude/，.env，.hermes/，.pytest_cache/，.ruff_cache/` | 保留，三个原有文件及活动进程，不能切分支覆盖 |
| `/tmp/acs-action-baseline-20261023` | `29c603f` / `detached` | 有未提交内容 | `src/acs/__pycache__/，tests/__pycache__/` | 必须保留，未提交源码/实验清单 |
| `/tmp/acs-action-completeness-20261023` | `a862a20` / `fix/order-action-completeness` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/acs-execution-baseline` | 已不存在，注册为 prunable | 无法检查历史本地数据 | 无法证明已归档 | 保留注册，先 dry-run，不执行 prune |
| `/tmp/acs-external-final-check-20261022` | `f6c3387` / `detached` | 有未提交内容 | `.pytest_cache/，__pycache__/，scripts/__pycache__/，src/acs/__pycache__/，tests/__pycache__/` | 必须保留，未提交源码/实验清单 |
| `/tmp/acs-final-handoff-babbfab-20261021` | `babbfab` / `detached` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/，.venv/，__pycache__/，scripts/__pycache__/，src/acs/__pycache__/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/acs-p1-review-4b9c3cb` | 已不存在，注册为 prunable | 无法检查历史本地数据 | 无法证明已归档 | 保留注册，先 dry-run，不执行 prune |
| `/tmp/acs-p1-review-candidate` | 已不存在，注册为 prunable | 无法检查历史本地数据 | 无法证明已归档 | 保留注册，先 dry-run，不执行 prune |
| `/tmp/acs-runtime-v21-integration-20261022` | `29c603f` / `integrate/runtime-v21-delivery` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/，.venv/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/acs-seed-clock-20261022` | `add50f2` / `fix/seed-business-clock` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/，.venv/，src/acs/__pycache__/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/acs-v21-before-c-20261022` | `c513061` / `detached` | tracked/untracked 干净 | `src/acs/__pycache__/，tests/__pycache__/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/ecommerce-runtime-v21-20261022` | `c12bb62` / `fix/v21-mixed-answer-evidence` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/，.venv/，scripts/__pycache__/，src/acs/__pycache__/，tests/__pycache__/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/ecommerce-v3-episode-pilot-20261023` | `93db5cf` / `fix/v3-response-contract-parity` | tracked/untracked 干净 | `.ci-artifacts/，.pytest_cache/，.ruff_cache/，.venv/，__pycache__/，results/tau_development_pilot_20261023/intermediate/` | 保留，唯一活跃 V3 开发目录 |
| `/tmp/ecommerce-v3-langgraph-20261023` | `576f56f` / `detached` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/，.venv/，__pycache__/` | 清理候选；需最后复核和明确确认，未执行删除 |
| `/tmp/ecommerce-v3-tau-adapter-20261023` | `d7da103` / `feat/v3-tau-langgraph-adapter` | tracked/untracked 干净 | `.pytest_cache/，.ruff_cache/，.venv/` | 清理候选；需最后复核和明确确认，未执行删除 |

所有存在目录的 HEAD 都被至少一条远端分支包含，未发现本地独有提交。`acs-seed-clock` 本地分支追踪 `origin/main` 并落后 1 个提交，这是追踪配置，不应误当未推送。修复分支审计时刚建立，尚无 upstream，交付时将推送。

## 主目录三个原有文件

- `skills-lock.json`：未跟踪的技能安装来源锁，version=1，七个条目指向 `mattpocock/skills` 的 skillPath 和 computedHash。Git 无创建提交，不能证明具体安装人或时间；不是本轮依赖锁，不删除、不纳入本轮提交。
- `docs/NEXT_ROUND_PLAN_AND_ACCEPTANCE_C9AFDBF.md:Zone.Identifier`：62 字节 Windows 下载来源标记，ZoneId=3，并含来源站点 URL；它不是规划正文。真实规划 Markdown 已由 Git 跟踪，历史日志可查。本轮不把下载来源扩散进公开报告，也不擅自删除。
- `docs/TODOS.md`：未提交地新增四行说明，链接 `NEXT_ITERATION.md`，说明两周范围与后续计划；此前 Git 历史包含 `babbfab`。未提交差异作者不可由 Git 证明，本轮不修改、不提交。

| 受保护路径 | 审计 SHA256 | 字节 |
| --- | --- | ---: |
| `skills-lock.json` | `7f113550091bfdddd4d92647e8935262b801ef50a19edfd3b7c1ac40af3fdf97` | 1752 |
| `docs/NEXT_ROUND_PLAN_AND_ACCEPTANCE_C9AFDBF.md:Zone.Identifier` | `a6ea93f4dc0cb539fb9db4291cd57b9086547bf18e86a82f7443c372c68fd28c` | 62 |
| `docs/TODOS.md` | `8ea75cd51f1a686faaaf0bf53f9966ecef1e0c7aee6d1fa74db78b95f9fa119d` | 7745 |

交付前会重新核对这些指纹。历史 `REPORT.md`、Scorecard 和失败 checkpoint 不覆盖；新实验使用独立运行 ID 与目录。

## 未提交、忽略内容与进程

`acs-action-baseline` 有未提交评测脚本、SQL、冻结清单；`acs-external-final-check` 有修改中的 Adapter、测试及新增分析脚本。这两处必须保留，不能拿“HEAD 已推送”当作全部工作已保存。

活跃 V3 目录含被忽略的 `.ci-artifacts/` 和旧 Pilot `intermediate/`，约 11 MB 的原始检查日志仍在本地，默认不进 Git。主目录有 `.env`、业务 DB 与备份、索引等；不得归入可删除缓存。其它目录主要是 `.venv`、pytest/ruff 和 Python 缓存，已跟踪结果由 Git 保留，但删除前仍须列出忽略内容并核对。

本轮只读扫描 `/proc` 的 cwd 和 comm，未输出可能含凭据的进程参数。发现主目录有 Python、Claude、MainThread 和 shell 进程；历史 Worktree 未发现 cwd 在目录内的进程。该扫描不能证明编辑器没有打开文件或外部进程没有引用路径，因此仍需使用者确认关闭相应窗口。没有杀进程。`git stash list` 为空，没有清空或改动。

## 建议最终布局与安全清理

最终建议保留主项目目录和本轮活跃 V3 Worktree。主目录当前仍是带用户修改的 `feat/v3-langgraph-poc`，本轮没有强行切到 main；待用户独立保存三个文件后再选择稳定分支。GitHub 历史分支可以继续保留。

以下命令仅为清理方案，**本轮未执行 remove/prune**。每个候选都须先满足源码已推送、工作区干净、忽略结果已归档、相关进程和 VS Code 窗口已关闭，并得到目录删除确认。

```bash
git worktree list --porcelain
git -C '/tmp/ecommerce-v3-tau-adapter-20261023' status --short --branch -uall
git -C '/tmp/ecommerce-v3-tau-adapter-20261023' ls-files --others --ignored --exclude-standard --directory
git -C '/tmp/ecommerce-v3-tau-adapter-20261023' log --oneline HEAD --not --remotes
# 以下仅在上述检查及用户确认之后，对单个目录执行：
git worktree remove '/tmp/ecommerce-v3-tau-adapter-20261023'
# 同样条件满足后，旧 PoC 目录可单独移除：
git worktree remove '/tmp/ecommerce-v3-langgraph-20261023'
# 仅预览已经不存在目录的失效注册：
git worktree prune --dry-run --verbose
```

不要使用 `remove --force`、`reset --hard`、强制 push 或 `git clean`。普通 `worktree remove` 也可能移除被忽略文件，所以它的成功不等于没有数据丢失；必须先核对和归档。未证明安全的目录继续保留。

## 交付前复核

复用目录的源码已推送到 `fix/v3-response-contract-parity`，冻结源码为 `3c4ab1c`。该 SHA 完整 CI 839 项通过；新真实实验仅写入本目录新的结果目录和被忽略的受限原始证据。没有新增 Worktree，历史目录没有 remove，失效注册只执行 prune dry-run。主目录仍保留原分支与三个原有修改；它们的 SHA256 以及上轮四份失败记录再次核对未变。

所有历史目录继续保留。两处有未提交代码，必须保留；其它候选仍包含忽略文件，且没有取得关闭旧 VS Code 窗口和目录删除的确认，因此没有把“源码已远端保存”当作删除整个目录的充分条件。唯一活跃 V3 开发目录已固定，历史分支无需删除。清理命令仍是条件方案，不代表本轮已经移除目录。
