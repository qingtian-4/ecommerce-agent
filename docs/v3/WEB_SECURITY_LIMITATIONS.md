# 本机Demo安全边界

这是**合成身份的本机演示，不是生产认证**。启动器固定127.0.0.1、单Worker；服务仅接受配置的精确Host和同源Origin，禁止跨站请求、不开放CORS，页面CSP仅self，禁止frame和任意脚本执行。

浏览器HttpOnly、SameSite=Strict Cookie映射服务端凭证hash与业务session。POST验证CSRF和Origin；服务端固定合成USER001，另加浏览器session隔离，不能通过猜sid读其他浏览器消息/事件/确认。所有权仍由M6业务服务再次核验；JSON拒绝用户、数据库路径、Graph thread和任意确认event输入。不同浏览器仍是同一合成客户的独立会话，不是多个真实客户账号。

原draft Fixture的12禁止正式写入Trigger保留。新Web只接受M6专用formal synthetic Fixture，模拟申请只能经应用桥，创建pending_review；审核、退款、支付、物流修改均禁止。模型不获得submit工具或连接。业务库读取失败不能创建替代空库或当作未提交。

公开状态与事件只发布指定字段，合成姓名/电话、配置密钥、常见凭证/邮箱/手机号进行清洗；不公开原始模型响应、CoT、请求Header、任意工具JSON、内部连接或数据库路径。正则清洗不是通用PII检测器，因此只允许合成数据；不要将真实客户信息输入Demo。

生产仍No-Go：没有真实登录/权限管理、会话撤销与服务端TTL、TLS、可信反向代理、多Worker协调、限流、部署监控、平台回执、备份及隐私治理。HttpOnly/SameSite不能对抗恶意本机程序或被攻陷的同源页面；SQLite文件所有者可改库。本轮不开放外网，不支持Windows原生fcntl，不建议绕开启动器监听0.0.0.0。CSP/Origin校验不能代替生产认证。

静态服务只允许index.html/app.js/style.css，不提供任意路径或私有证据下载。SSE连接断开、HTTP超时、Graph镜像损坏都不是业务失败证明；恢复必须以M6只读权威回执为先，未知结果不得盲目重写。

五状态卡片的UNKNOWN是保守只读门禁。sessionStorage只保存未收到结果的wait_id/request_key，不能发放身份、消费确认或证明提交；清空或修改它也不能绕过M6的SQL事务、业务绑定和权限校验。卡片在数据库不可核实时隐藏接受操作，不能据HTTP断线推断失败或自动换key重试。原M6操作员显式恢复与UI自动行为应区分，见RETURN_CARD_CONTRACT.md。
