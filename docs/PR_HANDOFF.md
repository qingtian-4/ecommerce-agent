# 电商客服 Agent 分支与 PR 收尾

本轮从`babbfab`继续，只做交付整理和验收脚本，不改业务 Runtime、默认开关或历史实验。
远端已 fetch，当前依赖是单条祖先链：

```text
main 3a4de7c
  └─ P0 282e98b → 4b10dbe  fix/order-draft-integrity，PR #1 → main
       └─ P1 4b9c3cb → d7bdb10 → a6cec14 → 92094ee
            └─ 颜色/聚合 c9afdbf → 最终可靠性 babbfab
                 └─ 本轮交付整理  feat/order-candidate-facts
```

P0 两提交与 P1 后续六提交没有分叉；`git merge-base --is-ancestor`验证包含关系。
不重复 cherry-pick、rebase 或按文件拆分已完成的实现/测试/证据。P1 依赖 P0 状态协议和会话服务，
最新安全修复又依赖 P1/颜色/聚合，因此不建议从最新树中手工摘零散代码。

## 推荐合并顺序

1. 更新并审阅[P0 PR #1](https://github.com/qingtian-4/ecommerce-agent/pull/1)，保留 458 项测试、
   8 组探针、事务约束、写入 p95 限制及显式启用的实际 Gradio 验收，不把它写成通用 Agent 成功率。
2. 创建后续 PR，head 为`feat/order-candidate-facts`，base 为`fix/order-draft-integrity`，
   范围为 P1 到最新可靠性及交付材料；可以先 draft，清楚标注依赖#1。
3. 两份 PR 均审阅满意后，建议#1 使用保留祖先的 merge commit，再将后续 PR 改 base 为 main。
   确认 diff 不重复 P0、合成提交 CI 通过后，由维护者决定是否合并后续 PR。
4. 合并后四保护仍默认关闭。默认启用必须单独配置决策，不与 PR 收尾捆绑。

若#1 选择 squash，后续分支仍保留原 P0 祖先，PR 可能重复显示旧提交或扩大 diff；先重新检查，
不要自动重写远端历史。本轮没有必要承担这一风险。Git 版本 2.34.1 不支持`merge-tree --write-tree`，
本轮已记录该命令失败，改用祖先关系和树差异核查；没有声称运行了不支持的合成检查。

## 当前远端证据

#1 实际页面为 OPEN，head `4b10dbe`，base main，两个提交，尚未合并。
GitHub Actions 实际页面记录`babbfab`的 Offline checks 成功，
[运行 39571122226](https://github.com/qingtian-4/ecommerce-agent/actions/runs/39571122226)。
新 push 必须重新核对新 SHA，旧绿灯不能自动替代新 CI。

当前 CLI 保存的 qingtian-4 登录凭据失效，未认证 API 又触发限流。SSH 可推送不意味着能写 PR。
本轮已请求维护者在终端重新`gh auth login -h github.com`；如尚未恢复，创建/更新 PR 属明确阻塞，
不是已完成。已准备完整正文，恢复后可直接执行，不需要重做实验：

```bash
gh auth status
gh pr view 1 --repo qingtian-4/ecommerce-agent
gh pr edit 1 --repo qingtian-4/ecommerce-agent --body-file docs/pr/P0_PR_BODY.md

# 先检查是否已有相同head的PR，防止重复创建
gh pr list --repo qingtian-4/ecommerce-agent --head feat/order-candidate-facts --state all
# 仅不存在时创建；已有则用gh pr edit更新对应编号
gh pr create --repo qingtian-4/ecommerce-agent --base fix/order-draft-integrity --head feat/order-candidate-facts --draft --title 'fix(agent): ground order answers and harden protected workflows' --body-file docs/pr/P1_RELIABILITY_PR_BODY.md
```

发布前核对正文中的证据路径和实际状态，发布后`gh pr view`读回 URL、head/base 和正文。
本轮不自动执行 merge、close 或改仓库可见性。最终操作结果以[交付报告](../results/project_handoff_babbfab/REPORT.md)为准。
