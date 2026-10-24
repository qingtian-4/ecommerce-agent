# 隔离 LangGraph Interrupt 确认 PoC

**已实现并实际执行。** 前置Context代码3090695的真实冻结8阶段全部通过后才开始本阶段；本阶段全部离线，新增真实付费请求0，不创建任何正式申请。

源码：src/acs/local_confirmation.py、LocalReturnPoc.confirmation、scripts/local_return_poc.py中的confirmation-*入口。测试：tests/test_local_confirmation.py。宿主保持create_agent原生模型/工具流程，确认是一个无模型的确定性StateGraph，使用同一SqliteSaver和`local-confirmation:` thread_id前缀；不是新Agent框架，也不是checkpoint_ns。

## 实际状态路径与信任边界

1. 应用open核验owner、无running聊天Turn、当前有效预览，绑定preview_id/session/user/revision/policy_hash/expires_at到隔离表local_confirmation_waits。
2. 原生interrupt输出绑定及“无正式提交”说明，Checkpoint待application_confirmation，尚无event_id、consumptions=0；重启仍等待。
3. 受信任应用decide产生唯一UUID事件并持久化issued；聊天文字、模型Tool Call和任意Command都不能生成SQL事件。
4. Command(resume=event_id)恢复，再次核验owner/session、最新预览、revision、policy、expiry、完整业务内容；同一业务SQL事务将结果与consumptions=1保存。
5. Graph完成后对照SQL真实回执；Checkpoint不能证明消费成功，更不证明正式申请已创建。

接受返回accepted/formal_submission=false；reject/cancel结束等待但不取消退货草稿。若聊天明确取消草稿，正常业务工具执行后会使等待失效。更正订单/商品/数量/原因、替换预览、policy或expiry变化均拒绝旧接受；已消费的历史回执标historical_receipt/reusable=false，重查当前绑定有效性，不给下一次提交权限。

应用本轮为Linux/WSL本地可信CLI；--user-id表示应用注入合成身份，**没有实现网络认证**。模型没有SQLite连接、确认工具、confirm_and_submit或管理员权限。正式return_requests/return_confirmations/return_reviews/return_audit保留12个deny Trigger，实际始终0。

## 可复现零费用入口

先用全新目录建立离线预览（固定模型演示，非DeepSeek效果）：

```bash
uv run python scripts/local_return_poc.py --state-dir /tmp/NEW_CONFIRM_DEMO demo
```

从最后JSON事件取session_id，分别执行以下命令；WAIT_ID来自open或pending，确认入口无需env-file或模型Key：

```bash
uv run python scripts/local_return_poc.py --state-dir /tmp/NEW_CONFIRM_DEMO confirmation-open SESSION_ID
uv run python scripts/local_return_poc.py --state-dir /tmp/NEW_CONFIRM_DEMO confirmation-pending SESSION_ID
uv run python scripts/local_return_poc.py --state-dir /tmp/NEW_CONFIRM_DEMO confirmation-decide SESSION_ID WAIT_ID accept
uv run python scripts/local_return_poc.py --state-dir /tmp/NEW_CONFIRM_DEMO confirmation-resume SESSION_ID WAIT_ID
```

拒绝/取消将accept改为reject/cancel。重复decide返回非零退出码；resume是读取/恢复原事件，不是第二次点击授权。open回执丢失后pending通过owner/session找回原等待，无需直接查SQLite。每fixture单worker/文件锁，不承诺Windows或生产多worker。

实际跨进程CLI记录：results/context_engineering_20261024/interrupt_cli_smoke.json。open→退出→pending仍waiting→accept→退出→resume历史回执→重复accept被拒绝；5个CLI进程、消费1次、正式四表0、费用账本未创建。原始私有fixture位于/tmp/v3-context-confirm-demo-20261024，避免与真实DeepSeek实验混用。

## 故障与审查结论

已执行28项离线合同（23核心＋2取消/替换＋1历史失效＋2镜像/假Checkpoint反例），覆盖原生暂停/重启、伪造Command、聊天确认、跨用户/session、重复/迟到点击、event与consume提交前后故障、真实进程退出、初始open回执丢失、过期/政策/完整内容更改、Graph与SQL回执错配。专项原始日志/tmp/v3-confirmation-*.log；最终完整回归以最终CI为准。

开发失败原件保留，不将首轮测试报绿：

- 只检查_content无reasons会接受被改写content_json的预览，首轮该回归实际失败；补完整内容一致性，未弱化断言。
- 伪造resume原先抛异常，LangGraph缓存错误值使后续合法事件卡住。改为拒绝后沿原生conditional edge进入新的interrupt任务；仍精确匹配SQL issued事件，不能把任意Command当确认。
- open已提交但Graph尚未落时又过期，原先永久卡在镜像缺失；允许无事件invalidated绑定完成终态Graph，仍拒绝已issued/consumed却丢失Graph的未知镜像。
- open回执丢失无法知道wait_id，增加受权限约束pending入口；不是新增提交授权。

架构和安全独立复核已实际运行相应红绿恢复路径及5项/1项专项，最终没有剩余阻塞发现；仍须以完整CI和受限业务范围理解该结论。

限制：隔离事件表不是正式return_confirmations；accepted只是已消费的一次实验决定，不是未来正式提交许可。消费与Graph之间没有跨库原子事务，依赖同业务库的持久回执对账。任意破坏/丢失已发放事件的Graph镜像会停止，要求成套恢复。未实现UI认证、生产HTTP/SSE、正式申请、审核、退款或换货。

[官方Interrupt语义](https://docs.langchain.com/oss/python/langgraph/interrupts)已结合本机锁定LangGraph1.2.16源码/执行核对：恢复会重跑节点，所以消费必须发生在interrupt之后且可核验，不能在暂停前写正式业务。
