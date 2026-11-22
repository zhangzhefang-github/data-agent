# 查询架构演进

[ADR 0002](../adr/0002-canonical-semantic-model.md) 由规范语义模型定义订单业务口径、授权和物理映射。[ADR 0003](../adr/0003-direct-sql-duckdb-first.md) 将订单主题空间的 DuckDB 默认查询路径切换到 Direct SQL。查询形状以 [ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 为准：一条只读语句可以包含 WITH、子查询、空间内的 JOIN、GROUP BY 和窗口函数；Fanout 在这条查询里先汇总再连接。销售主题空间的结构化路径由 [ADR 0001](../adr/0001-structured-sales-query-boundary.md) 定义。

| 路径 | 流程 | Phase 1 适用范围 |
| --- | --- | --- |
| Direct SQL | 问题 → 语义和数据库上下文 → Agent 生成 SQL → SQL AST 校验 → DuckDB 只读执行 | 订单主题空间连接 DuckDB 时的默认路径 |
| 结构化查询 | 问题 → 语义上下文 → Query IR → 语义校验 → Compiler → 只读执行 | 销售主题空间及使用其他数据库的订单查询 |

订单主题空间的两种执行路径使用同一份规范语义模型和授权。授权范围包括六个订单指标、订单日期、订单状态和客户标识，以及 [ADR 0004](../adr/0004-customer-order-equijoin.md) 授权的客户–订单等值内连接。这条连接不会放大订单行。支付明细、客户姓名、raw 和 staging 不在授权内。业务日期按 Asia/Shanghai 解释。用户说出时间时写入该条件；没有说出时间时不补日期，只使用方言配置的默认行数上限。

## 订单结果与会话

订单笔数的规范 ID 为 `orders.order_count`，结果列和 SQL 展示别名使用 `order_rows`。查询结果返回列、行、SQL、数据源和耗时。状态筛选支持授权字典中的中文业务值和物理值，Direct SQL 结果以中文显示订单状态。

切换时清空已有会话。Direct SQL 会话保存最近一次成功执行的 SQL。「去年同期」将已保存的查询日期向前移一年，沿用状态筛选、指标和分组，随后经过同一校验入口执行。2 月 29 日在上一年没有对应日期时改用 2 月 28 日。查询失败时保留此前成功的会话上下文。

## Direct SQL 的执行边界

Agent 取得实际 DuckDB 版本、获准的表和字段，以及已执行验证的 SQL 示例。SQLGlot 校验单条 SELECT。程序校验指标表达式、授权维度的筛选和分组、排序，以及已授权的等值内连接。未授权表、支付明细关联和外部数据源会被拒绝。语义校验器会拒绝未预聚合的 Fanout。执行器不执行未授权连接。带 Fanout 的支付关系仍不向问数开放。

2026-11-22，上一段是 `data-agent@f9bc191` 的执行边界。[ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 决定：只读，且只能访问该空间列出的表。WITH、子查询、空间内多表 JOIN、GROUP BY 和窗口函数可以出现在同一条语句里。订单笔数和订单金额在未先按订单汇总的支付连接上聚合，仍然拒绝。不规定唯一 JOIN 模板。同日工作区按此改了校验器和 `payments` 视图的授权，后端测试 145 项通过，改动尚未提交。见 [Genie 的 SQL 形状](../wiki/genie-sql-shape.md)。实现工单是 [#26](https://github.com/zhangzhefang-github/data-agent/issues/26)。

2026-11-21，订单 DuckDB 路径上的提示、授权表和连接键来自同一次已发布语义上下文，不再读取 `orders_space.yaml`。客户标识可以在订单表上分组，也可以出现在已授权连接的客户表一侧。客户记录是否存在以这条等值内连接为准，客户标识非空不能代替。订单 Direct SQL 不再把按问题裁剪的目录工具交给模型。业务关系 `customer_to_order` 用 `dataset_relationship_ids` 指向对应的数据集 JOIN；空间未点名的 JOIN 不会因此进入问数。

DuckDB 以只读方式连接，关闭外部访问，执行限时 5 秒，最多返回 500 行。可修复的 SQL 错误最多重试两次，每次重新校验。权限拒绝会结束查询。

2026-11-21，[ADR 0006](../adr/0006-space-authorization-bounds-retrieval.md) 把空间授权和检索分开。订单 DuckDB 的一次请求只构建一份语义上下文快照，提示和校验都读这份快照。订单空间仍把完整授权放进提示。授权资产超过小空间规模时，才先生成短目录，再展开被定位的资产。已授权但未展开的数据集可以有限次补进同一份快照并重新生成；未授权数据集仍然拒绝。补全不能绕过 Fanout、粒度和只读校验。规范语义模型继续是业务口径来源，Wiki 不参与运行时检索。

## 验证

在仓库根目录运行以下命令。

```sh
(cd backend && python -m pytest -q tests/test_direct_sql_chat.py)
PYTHONPATH=backend python -m app.evaluation.runner --orders
```

订单 Direct SQL 的执行器回归通过现有聊天接口运行，用替换的模型 Runner 提交 SQL，由真实的 DuckDB 测试数据库执行。用例覆盖订单笔数、金额、空结果、权限拒绝、重试和 Direct SQL 会话续问。

当前订单验收是 `--orders`。它读取 `backend/app/evaluation/orders_golden.json`，对本地 Jaffle Shop 调用真实模型。2018-01-01 至 2018-04-09 的问数只比对结果行：订单笔数 99、金额 1672.0，五种状态、四种支付金额、按客户标识 62 行且合计仍是 99 笔和 1672.0，有客户记录的订单仍是 99 笔和 1672.0，金额最高的客户标识是 51、金额 99.0。按月、NPS、未发布的平均订单金额、raw 和 staging 不能返回结果行。支付明细连接这题在 2026-11-22 返回了未连接的订单金额 1672.0，评测仍判失败；结论见[支付明细与 Fanout](../wiki/payment-fanout.md)。随后在同一会话问「去年同期」，核对 SQL 使用 2017-01-01 至 2017-04-09，结果为空。销售 Golden Dataset 仍只验证原来的结构化路径，运行 `python -m app.evaluation.runner`，不作为这轮订单验收。
