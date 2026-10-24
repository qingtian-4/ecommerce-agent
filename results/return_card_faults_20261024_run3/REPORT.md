# 最终五状态 / HTTP / SQLite / Chromium 验收

运行源码 `34a4ba6014ab3acaaa805bdf8d1a600eed89fc04`；运行期间指纹不变。固定模型，10个独立合成Fixture，0付费请求，0页面异常。不是DeepSeek质量成绩。

|场景|申请/确认/审计/幂等回执数|决定HTTP尝试|结果|
|---|---|---:|---|
|normal|1/1/1/1|1|通过|
|reject|0/0/0/0|1|通过|
|cancel|0/0/0/0|1|通过|
|expired|0/0/0/0|1|通过|
|correction|0/0/0/0|1|通过|
|drop_before_dispatch|0/0/0/0|1|通过|
|lost_after_commit|1/1/1/1|1|通过|
|uncertain_before_commit|0/0/0/0|1|通过|
|database_unavailable|0/0/0/0|0|通过|
|csrf_and_cross_session|0/0/0/0|1|通过|

HTTP尝试包含浏览器被中止、未到服务器的请求与故意拒绝的CSRF请求，不等于已执行的业务操作。normal双击只派发1次决定；lost_after_commit确认SQL已提交后才丢响应，先显示UNKNOWN，再只读核实为PENDING_REVIEW。两案均故意损坏Graph文件，独立read_submission_receipt仍验证成功，其余场景正式四表全部0。

[完整Scorecard](scorecard.json)保留逐案等待ID、预览ID、决定、消费次数及实际申请状态；[SQL回执独立对账](sql_reconciliation.json)证明成功不依赖Graph。[源码与保护范围](source_audit.json)证明M6四模块、默认app和CI工作流不变。原始SQL库/服务日志在受限.private-evidence，公开摘要只含合成业务字段。

截图：[READY](READY.png)、[REJECTED](REJECTED.png)、[EXPIRED](EXPIRED.png)、[UNKNOWN](UNKNOWN.png)、[PENDING_REVIEW](PENDING_REVIEW.png)、[320px窄屏](PENDING_REVIEW_mobile.png)。均来自实际页面，320/375无横向溢出，已人工查看。

[源码完整CI](https://github.com/qingtian-4/ecommerce-agent/actions/runs/40082438023)：980 passed，0 skipped/failed/error；Web26、新卡片8、M6原子提交39，Ruff及密钥扫描通过。[JUnit核验摘要](ci_acceptance.json)包含Artifact与JUnit SHA256。原JUnit只留CI Artifact及本地受限目录。

```bash
# 使用独立Playwright环境运行；子服务使用仓库.venv：
python scripts/eval_return_card.py --out /tmp/v3-return-card-browser-new --port 8770
# 应用正常启动（新合成目录）：
uv run python scripts/v3_web_demo.py --state-dir /tmp/v3-return-card-new --init
```

五状态均经过真实HTTP/SQLite与浏览器验收。加载文字、色彩与未回执HTTP标识是界面层，不是业务成功凭证；未核实请求可以持续UNKNOWN，不自动写入恢复。原M6显式操作员恢复API仍保留，不由卡片自动调用。

Go：本机合成数据Demo与后续独立本地评测。No-Go：生产身份、真实平台售后、审核/退款及正式模型质量结论。本轮不自动合并、评测或部署。
