# 查询展示浏览器验收：保留的失败记录

商品/订单展示回归已通过，但本次浏览器CLI用相对数据路径启动时，在第一个阶段前失败，completed=false。根因是WebEngine虽保存root.resolve()，web.sqlite路径仍使用原相对root，新增只读URI查询因而抛出ValueError。

之前TestClient和故障矩阵均传绝对路径，没有发现该CLI路径缺口。新增真实HTTP回归先红后绿，将web.sqlite路径统一到self.root；本目录保留，不覆盖Scorecard。模式说明已渲染，但本运行不能作为业务验收通过证据。
