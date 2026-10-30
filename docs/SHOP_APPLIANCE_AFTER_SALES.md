# 家电模拟售后政策 v1.4

2026-10-30。v1.4 保留 v1.3 的 64 商品、304 订单、8 用户和全部业务数据，只新增售后
政策文档并更新版本；v1.3 原文件不改写，历史结果仍可复现。所有条款均为本地合成假设，
不是品牌、平台、法律或真实客服承诺。

## 覆盖范围

| 品类 | 无理由退货 | 质量问题退货 | 保修期 |
|---|---:|---:|---:|
| 电热水器 | 7 天 | 15 天 | 36 个月 |
| 燃气热水器 | 7 天 | 15 天 | 36 个月 |
| 厨房热水器 | 7 天 | 15 天 | 24 个月 |
| 洗碗机 | 7 天 | 15 天 | 24 个月 |
| 油烟机 | 7 天 | 15 天 | 24 个月 |
| 净饮机 | 7 天 | 15 天 | 24 个月 |
| 燃气灶 | 7 天 | 15 天 | 24 个月 |
| 冰箱 | 7 天 | 15 天 | 36 个月 |

耳机、充电器、充电宝沿用原有政策。家电现在会得到 `return=true`、`warranty=true`，
因此订单/商品不再显示“仅咨询，暂不支持办理”；可以进入模拟退货预览和待审核申请。
安装、换货、真实退款、真实维修派单仍是咨询口径，不生成相应单据。

## 合成方式

先写 `prompts/appliance_after_sales_v1.md`，固定品类、7/15 天、24/36 个月、未安装/
未通水通电/未接入燃气、凭证与边界，再按 prompt 生成：

- `data/demo/shop_realism_v1_4/docs/return_policy.md`
- `data/demo/shop_realism_v1_4/docs/warranty_policy.md`
- `data/demo/shop_realism_v1_4/docs/appliance_after_sales.md`

生成物保留 `return_rules.read_return_policy` 所需的解析锚点（7/15 天、条件、退货运费、
申请方式、保修表），并更新 `manifest.json` 的版本和逐文件哈希。

## 验证

- `tests/test_shop_appliance_after_sales.py`：8 个家电品类的 `policy_scope` 均为
  `return=true`、`warranty=true`；v1.3 仍是 `return=false`、`consultation=true`；
  一台已签收的 v1.4 家电订单能生成 `ready=true` 的模拟退货预览，不再返回“缺少适用政策”。
- `tests/test_shop_policy_boundary.py`、`test_shop_demo_data.py`、
  `test_shop_data_revision.py` 通过，v1.1/v1.2/v1.3 的旧边界未被改写。
- `ruff` 通过。

## 限制

- 安装状态没有独立数据库字段；“未安装/未通水通电/未接入燃气”目前由政策文案与用户
  声明/页面确认体现，不是自动核验。不能把预览通过说成真实资格审核通过。
- 退货政策仍使用同一套 7/15 天窗口和条件；家电的特殊安装、高空作业、辅材、部件差异
  尚未逐项建模。
- 安装、换货、真实退款、真实维修派单未实现；v1.4 只覆盖模拟退货/保修咨询和申请。
- 所有条款是合成演示，不能用于真实售后判断。
