# Genie 的 SQL 形状

2026-11-22，用户贴出 Databricks Genie 的产品回复，并说明本仓库接下来也要支持同样的能力。对方更正的是：Genie 并不「只接受单条简单 SELECT，且禁止 WITH、子查询、支付表与订单表连接」。

## Genie 写明的两条限制

2026-11-22 的回复只保留两条：

- 只读。可以执行 SELECT。INSERT、UPDATE、DELETE、CREATE、ALTER、DROP 以及改表结构的语句拒绝。
- 只能查询当前工作空间架构里列出的表。回复中的例子是 `sales_transactions`、`sales_customers`、`sales_franchises`、`sales_suppliers`、`media_customer_reviews`。这些名字属于 Genie 那个工作空间，不是本仓库的 Jaffle 表。

同一条回复写明一条查询可以使用 WITH（CTE）、子查询、多表 JOIN、GROUP BY 和窗口函数。支付方式字段可以和交易表连接。

## 本仓库多出来的第三道限制

2026-11-22，对照 `data-agent@f9bc191`，本仓库在只读和表清单之外还有句法限制。`_validate_order_sql` 只接受一条 `SELECT`，语句上允许的部分限于选择列表、FROM、JOIN、WHERE、GROUP BY、ORDER BY、LIMIT、HAVING；其余形状拒绝为「订单查询只支持单表汇总或已授权的等值关联」。方言规则和提示写明不要使用 WITH、子查询、DISTINCT、QUALIFY、`date_trunc`。连接只放行空间已点名的等值内连接。支付表与订单表的连接按 [ADR 0005](../adr/0005-payment-mart-single-table.md) 关闭。

这道限制的起因见 [订单主题 Direct SQL](direct-sql-orders.md)：2026-11-21 真实模型提交 `WITH filtered_orders` 后被拒绝，随后把方言示例和提示收紧成校验器当时允许的单条 SELECT。

## 2026-11-22 的下一步

用户把接下来要支持的能力定为 Genie 的那两条限制，而不是维持第三道句法限制。

保留：

- 一条只读语句。写入和改结构仍然拒绝。
- 未进入该空间架构的表仍然不可访问。raw 和 staging 不因为这次放开进入默认清单。

要补上：

- 一条查询里的 WITH、子查询、多表 JOIN、GROUP BY、窗口函数。
- 空间里列出的表可以连接，包括支付与订单。不再把「禁止 WITH / 子查询 / 支付表连接订单表」当作产品限制。

工作假设，同日写入、若用户否定再改：订单笔数和订单金额的指标基础粒度仍是一笔订单一行。支付明细是一笔支付一行，直接连接会把订单行放大。2018-01-01 至 2018-04-09 的订单是 99 笔、1672.0，支付是 113 笔、1672.0。允许写出这句 JOIN，但订单笔数或订单金额若在未先按订单汇总的支付连接上聚合，仍然拒绝。先按 `order_id` 汇总再连接的写法要能通过。见 [支付明细与 Fanout](payment-fanout.md)。此段里「只放行先汇总再连接这一种形状」同日被下一段取代。

2026-11-22 稍后，用户指出 WITH 可以解决 Fanout，而且更灵活。Fanout 在同一条只读查询里用 CTE 处理：多的一侧先汇总到少的一侧的粒度，再连接。不规定唯一的 JOIN 模板，也不再把「两条 SELECT 串行」当作首选。订单笔数和订单金额仍按一笔订单一行；裸连接后直接汇总订单金额会放大（同日记录直接连接后为 2083），这种结果仍然拒绝。模型可以用不同的 CTE 得到按订单、按支付方式或其他已授权粒度的结果。同日写入 [ADR 0007](../adr/0007-readonly-sql-with-fanout.md)，并发布为工单 [#26](https://github.com/zhangzhefang-github/data-agent/issues/26)。同日工作区按此改了校验器：先按订单汇总 `payments` 再连接得到 99 笔、1672.0 和支付 113 笔、1672.0；未汇总的订单金额被拒绝。后端测试 145 项通过。改动尚未提交，`data-agent@f9bc191` 仍是改动前的提交。

DISTINCT、QUALIFY 随这条查询形状一起放开。按月仍然是已发布的日历口径，不再靠方言禁止一切 `date_trunc`。
