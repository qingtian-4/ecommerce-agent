# 电商客服 Agent 原始 τ 续跑实现

原始102段与账本只读校验完成；最后#1668没有usage，按全预留0.163316计入新账本，
继承32.782968元、余额7.217032元；历史原账本不修改。

新入口 scripts/tau_resume.py 默认只出计划；--dry-run用原工具/脚本模型做18段无网络接线。
--live必须同时提供与精确恢复协议SHA匹配的审批文件及通过的dry-run。当前没有审批文件，未发请求。
所有102个已评分结果校验哈希、身份、reward与请求归属；多文件重复/坏JSON/丢账本均拒绝。
同一协议共用flock；SIGKILL由内核释放锁，不删锁inode。补充目录保留父账本SHA及来源，
从1669接续编号；任务97重做是隔离新环境的新尝试，不是旧写操作重放。

原执行函数增加显式resume_plan，仅允许eval runner观测/持久化源码改变；
原Runtime、Adapter及其余冻结源码、上游工具数据仍精确校验。因此V2.1工作树不能补原V2成绩。
新请求单条JSON+fsync/replace+目录fsync，余额缓存不代替原始请求记录；正常退出才导出完整账本。
进程在写结果后、登记完成前死亡时，以原子结果文件校验判定完成，不重复执行。
再次中断只生成新的待审批补充计划，不自动付费恢复。

RunMonitor保存PID/boot+start token、2秒心跳、task/arm/序号/完成数/金额；SIGINT/SIGTERM退出记录，
SIGKILL无exit文件，凭进程身份及持久记录识别异常死亡，不能依赖finally。
实际子进程SIGKILL、SIGTERM与结果写完后SIGKILL测试，以及进程间锁测试见resume_green_v2.log。
日志中Provider List原本重复打印，但旧日志仅268KB，不足以证明资源故障；
旧账本44.9MB，每次请求多次整文件覆写带来压力。新journal避免每次重写父账本，安全fsync保留。
退出根因UNKNOWN；exit_investigation.json保留内核/资源只读检查，未发现对应OOM或重启事件。

无网络预演resume_dry_final/summary.json：18/18接线通过、0新请求、继承预算不变。
不能将18个脚本结果与102个真实结果相加冒充完整120段真实成绩。

最终提交采用acceptance_dry_run，监督器exit_code=0；其协议SHA及待批准模板见REPORT和APPROVAL_REQUIRED.json。
先前resume_dry_final为早期开发预演，不作为当前付费启动依据。
