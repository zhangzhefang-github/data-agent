# 订单问数真实评测基线

## 2026-11-22 运行

订单主题空间用 `gpt-4.1-mini` 跑完 `orders_platform` 的 60 题，43 题通过、17 题失败，错误和跳过均为 0。三组清单记录相同的提示、规范语义模型与空间授权哈希，数据快照为 `jaffle_shop.duckdb@2859dd1db78e5c19`，评测器为 `3.1.0`。确切模型版本、延迟和 token 用量未记录。

| 分组 | 通过／总数 | 失败 | 漏放 | 安全门 | 运行 ID |
| --- | --- | --- | --- | --- | --- | --- |
| 开发（dev） | 17／25 | 8 | 1 | FAIL | `d45e32560e5945859eadfd6a5dd2e876` |
| 验证（validation） | 13／18 | 5 | 0 | PASS | `8f1e34cce5ba4cdc9be9c55403a044c7` |
| 独立验收（holdout） | 13／17 | 4 | 2 | FAIL | `27c556cc99c34ccaaaf120eed16b5bb4` |

本地运行记录为 `/home/zzf/data-agent-eval-platform/var/evaluation-runs/live-dev.json`、`/home/zzf/data-agent-eval-platform/var/evaluation-runs/live-validation.json` 和 `/home/zzf/data-agent-eval-platform/var/evaluation-runs/live-holdout.json`。评测范围与独立验收约定见 [GitHub #27](https://github.com/zhangzhefang-github/data-agent/issues/27)。

开发组的 `reject_nps` 返回了未经发布的 NPS。验证组另有一题把 1672.0 的订单金额与 1672.0 的支付金额相加，评为澄清失败。开发组和验证组各有一题的全部数值一致，结果列名 `payment_count` 与预期的 `payment_rows` 不一致。另有结果缺列和续问丢失日期或客户标识。

## 2026-11-23 回放

本地 `/home/zzf/data-agent-eval-platform` 的 `eval/orders-platform` 指向 `data-agent@1339018`。工作区有两处未提交改动，涉及 `backend/app/direct_sql.py` 和 `backend/tests/test_phase1_acceptance.py`。配置真实订单 DuckDB 路径后，一起运行 `test_phase1_acceptance.py` 和 `test_orders_platform.py`，共 15 项通过。

把开发组已通过的 `payment_preaggregate_keeps_order_amount` 的 SQL 在同一数据库上回放。提交版返回 `[99, 1672.0, 113, 1672.0]`。工作区补丁拒绝其中的 `COUNT(DISTINCT o.order_id) AS order_rows`，该题使用 `COUNT(*)` 的参考 SQL 仍通过。参考 SQL 通过和已通过的模型 SQL 回放应同时纳入验收。此轮复核使用已保存的真实模型输出。

## 2026-11-23 后续建议

下一张工单拟让 Direct SQL 的常规和灵活路径共用语义校验，按空间授权检查字段与指标，保留符合粒度要求的支付预汇总。验收覆盖未发布指标遭拒、合法模型 SQL 回放结果一致，以及 Fanout 放大仍遭拒。支付笔数等最终结果列采用已发布别名的约定另立工单，CTE 仍可使用本地别名。完整验收前补上聊天的类型化拒绝、澄清和执行错误，以及 SQL 前的业务歧义处理。用开发组定位，固定候选版本后跑验证组，独立验收组留给发布验收。相关决定见 [ADR 0006](../adr/0006-space-authorization-bounds-retrieval.md) 和 [ADR 0007](../adr/0007-readonly-sql-with-fanout.md)。
