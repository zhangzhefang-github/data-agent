# 订单主题 Direct SQL

## 决定

2026-11-21，用户把长期目标定为对标 Databricks Genie 的 AI Native Data Agent，并同意把跨会话记录放在 `docs/wiki/`。同一天要求先把自然语言对话查询 DuckDB 的完整链路跑到验收通过。

2026-11-21，[ADR 0003](../adr/0003-direct-sql-duckdb-first.md) 将订单主题空间在 DuckDB 上的主路径定为 Direct SQL。销售主题空间继续使用结构化 Query IR。验收口径在 GitHub issue #19，子工单 #20 至 #24 已关闭，#19 当时仍开放。

## 工程位置

2026-11-21，Direct SQL 实现不在本工作区 `integration/sales-space-mvp`。它在 `/home/zzf/data-agent-direct-sql` 的 `implement/direct-sql-20`。该分支在 `991f24d`（Allow the profiled customer-order equijoin in Direct SQL）之上还有未提交改动：`backend/app/direct_sql.py` 在核对等值键前登记新表别名，`backend/tests/test_direct_sql_chat.py` 增加带别名的客户–订单连接测试。

2026-11-21，`PYTHONPATH=backend python3 -m pytest -q backend/tests/test_direct_sql_chat.py` 在该工作区为 20 passed。这些测试用替换后的模型 Runner 注入 SQL，经过 `/api/chat/stream`。它们不覆盖真实模型对自然语言的理解。

2026-11-21，真实订单库路径为 `/home/zzf/data-agent-demo-data/jaffle_shop_duckdb/jaffle_shop.duckdb`。连接配置在本工作区 `.env`，不写入本页。

2026-11-21 下午，上面的 `991f24d` 指针已被同分支 `97bb211`（Recognize a joined table alias before authorizing the equijoin）取代。工作区在该提交之上仍有未提交改动，涉及提示、方言示例和对话测试。客户题为什么停在单表，见 [客户–订单授权上下文](customer-order-context.md)。

## 已知缺口

2026-11-21 早些时候的真实模型试跑（见会话 [订单问数试跑](9525a96a-987a-4918-9520-2b1718dde683)）里，比较两个月的金额没有返回结果，按客户查询被拒绝，去年同期的中文解释曾说成当天日期。这些问法不在下面这次验收题里。

同日的校验规则是：分组和筛选限于订单日期与订单状态，不允许在 SQL 里做 `date_trunc` 分组或指标相减。客户表等值内连接已授权，`customer_id` 不是可选维度。

## 验收

2026-11-21，[实测订单问数链路](784f82a3-3550-4d7d-817e-b935faa4cd8c) 用真实模型调用 `/api/chat/stream`，六题的 `done` 都是 `completed`，`source` 为 DuckDB，行与独立查库一致。区间 2018-01-01 至 2018-04-09：99 笔、金额 1672.0；按状态五行为已下单 13/284.0、退货中 2/38.0、已完成 67/1103.0、已退货 4/49.0、已发货 13/198.0；已完成筛选为 67 笔、1103.0；四种支付为信用卡 871.0、优惠券 185.0、银行转账 411.0、礼品卡 205.0。2017-01-01 至 2017-04-09 与 2019-01-01 的笔数为 0，接口因 `HAVING COUNT(*) > 0` 返回空结果。

同一天，第 4 题第一次中文解释写成了 2025年11月21日，同一套代码重试后才复述 2017-01-01 到 2017-04-09。解释日期还不稳定。#19 因此仍不关闭。

同一天，模型曾提交 `WITH filtered_orders`，被拒绝为「订单查询只支持单表汇总或已授权的等值关联」。随后收紧了方言规则和提示。经典示例从 `WITH weekly_sales` 改成虚构表上的单条 `SELECT`，避免示例演示校验器会拒绝的写法。改动尚未提交。同日 `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_direct_sql_chat.py` 为 20 passed。

2026-11-21，问「总金额、总笔数和平均订单金额」时，模型提交的 `SUM(amount) / COUNT(*)` 未对上已发布指标，整句被拒绝。见 [平均订单金额被整句拒绝](average-order-amount.md)。

2026-11-21 晚间，上面「改动尚未提交」的说法被 `data-agent-direct-sql@c9bbcc6` 取代。授权放宽之后，用真实模型重跑了同一六题。六题 `done` 都是 `completed`，`source` 为 DuckDB，行与放宽前的独立查库一致：99 笔、1672.0；按状态五行与上一段相同；已完成为 67 笔、1103.0；四种支付为 871.0、185.0、411.0、205.0；2019-01-01 为空。第 4 题 SQL 为 2017-01-01 至 2017-04-09，结果为空。该题中文解释在同日多次把日期说成 2025-11-21 或再减一年的 2016 区间；提示改为原样复述服务已经平移的日期后，解释写成 2017-01-01 至 2017-04-09。这六题的 SQL 都没有访问支付表。

## 2026-11-21 本仓库收尾

上文把订单 Direct SQL 的工程位置写在 `/home/zzf/data-agent-direct-sql`。该说法被本仓库 `main` 在 `f6043af` 之上的收尾取代：订单主题空间连接 DuckDB 时，默认路径在本仓库，为 Semantic Model → Semantic Context → 模型生成原生 SQL → SQL Validator → DuckDB 只读执行。DuckDB 是订单的开发、运行和验收数据库。SQLite 与 PostgreSQL 保留兼容。Query IR 和 Compiler 保留，订单 DuckDB 默认路径不退回结构化查询。

同日注入 SQL 的三道客户题结果见 [客户–订单授权上下文](customer-order-context.md)。真实模型本轮没有重跑。
