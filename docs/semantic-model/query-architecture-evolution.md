# 查询架构演进

[ADR 0002](../adr/0002-canonical-semantic-model.md) 由规范语义模型定义订单业务口径、授权和物理映射。[ADR 0003](../adr/0003-direct-sql-duckdb-first.md) 将订单主题空间的 DuckDB 默认查询路径切换到 Direct SQL。销售主题空间的结构化路径由 [ADR 0001](../adr/0001-structured-sales-query-boundary.md) 定义。

| 路径 | 流程 | Phase 1 适用范围 |
| --- | --- | --- |
| Direct SQL | 问题 → 语义和数据库上下文 → Agent 生成 SQL → SQL AST 校验 → DuckDB 只读执行 | 订单主题空间连接 DuckDB 时的默认路径 |
| 结构化查询 | 问题 → 语义上下文 → Query IR → 语义校验 → Compiler → 只读执行 | 销售主题空间及使用其他数据库的订单查询 |

订单主题空间的两种执行路径使用同一份规范语义模型和授权。授权范围包括六个订单指标、订单日期和订单状态。业务日期按 Asia/Shanghai 解释，未指定日期时查询今年截至今天。

## 订单路径兼容

订单笔数的规范 ID 为 `orders.order_count`，结果列和 SQL 展示别名保留 `order_rows`。历史查询的指标和排序字段可用规范 ID 或兼容 ID。状态筛选支持授权字典中的中文业务值和物理值，Direct SQL 结果以中文显示订单状态。

旧会话直接展示已保存的结果。续问时，兼容层读取旧 Query IR 的条件并按当前主题空间授权重验。Direct SQL 会话保存最近一次成功执行的 SQL。「去年同期」将已保存的日期向前移一年，保留原筛选、指标和分组，随后经过同一校验入口执行。2 月 29 日在上一年没有对应日期时改用 2 月 28 日。查询失败时保留此前成功的会话上下文。

## Direct SQL 的执行边界

Agent 取得实际 DuckDB 版本、获准的表和字段，以及已执行验证的 SQL 示例。SQLGlot 校验单条 SELECT，只允许访问 `main.orders` 和获准字段。程序校验指标表达式、日期和状态筛选、分组及排序，并拒绝跨表访问和外部数据源。

DuckDB 以只读方式连接，关闭外部访问，执行限时 5 秒，最多返回 500 行。可修复的 SQL 错误最多重试两次，每次重新校验。权限拒绝会结束查询。跨表 JOIN 与 Fanout 校验留待对应业务能力上线时验收。

## 验证

在仓库根目录运行以下命令。

```sh
(cd backend && python -m pytest -q tests/test_direct_sql_chat.py)
(cd backend && python -m app.evaluation.runner)
```

订单 Direct SQL 回归通过现有聊天接口运行，用替换的模型 Runner 提交 SQL，由真实的 DuckDB 测试数据库执行。用例覆盖订单笔数、金额、空结果、权限拒绝、重试和旧会话续问。销售 Golden Dataset 继续验证结构化查询路径。

本地 Jaffle Shop 的只读复核使用 2018-01-01 至 2018-04-09，预期为 99 笔订单、金额 1672。该复核验证数据库结果。实际模型能否从自然语言生成相应 SQL 需要另做端到端验证。
