# 退货卡片故障证据

开发期前6场景通过；lost_after_commit场景的测试器误将Playwright第二回调参数当场景名，没有转发真实POST，因此等待PENDING_REVIEW超时。SQL证明没有被误写。已将默认绑定改为keyword-only，保留失败，不弱化SQL断言。

已通过场景：normal, reject, cancel, expired, correction, drop_before_dispatch。

原始Scorecard保留：[scorecard.json](scorecard.json)。业务SQL终态及wait/request绑定已在cases中逐案记录；原始库与服务日志在受限的.private-evidence目录，不提交凭证。
