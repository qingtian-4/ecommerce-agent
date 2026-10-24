# 退货卡片故障证据

开发期10场景真实浏览器检查完成，0页面异常；HEAD7e6f1f8加当时工作区源码，精确源码见fingerprints。它不替代最终提交后的再验收。每个场景使用全新合成Fixture，模型为固定脚本，付费请求0。

已通过场景：normal, reject, cancel, expired, correction, drop_before_dispatch, lost_after_commit, uncertain_before_commit, database_unavailable, csrf_and_cross_session。

原始Scorecard保留：[scorecard.json](scorecard.json)。业务SQL终态及wait/request绑定已在cases中逐案记录；原始库与服务日志在受限的.private-evidence目录，不提交凭证。
