# 平均订单金额被整句拒绝

2026-11-21，用户问「所有订单的总金额、总笔数和平均订单金额分别是多少？」。界面只显示「查询包含未授权的指标或维度」，总金额和总笔数也没有结果。

同日运行日志里的 SQL 是：

```sql
SELECT
  SUM(amount) AS order_amount,
  COUNT(*) AS order_rows,
  SUM(amount) / COUNT(*) AS avg_order_amount
FROM main.orders
```

对照 `data-agent-direct-sql@c9bbcc6` 的 `_validate_order_sql`：前两列对得上已发布的 `order_amount`（`SUM(amount)`）和 `order_rows`（`COUNT(*)`）。第三列是除法，对不上任何已发布指标，于是抛出 `SqlRejected`。`SqlRejected` 不进入修复，整次请求以 `query_failed` 结束。去掉第三列后，同一函数返回列 `order_amount`、`order_rows`。

同日订单空间发布的指标只有 `order_amount`、`order_rows` 以及四种支付金额。规范模型的表达式种类已经包含 `avg` 和 `ratio`，但这条 Direct SQL 路径只把 `sum` 和 `count` 写进提示，并只按这两种形状核对选择列。词汇表里的「客单价」属于销售主题空间，不是这条订单口径。

## 优化方向

2026-11-21，判断是：不要在校验器里放行任意除法。要让这题出数，应发布「平均订单金额」，定义为订单金额除以订单笔数，分母为零时为空，并让提示和校验只接受这条已发布表达式及其结果别名。在该指标发布之前，未授权的选择列应点名表达式并允许修复，使已授权的总金额和总笔数仍能返回。

2026-11-21 稍后，上面这段被同一天的架构判断取代，不删原文。缝在已发布指标，不在 SQL 形状。`ratio` 已在规范模型的表达式种类里，提示、Query IR 适配和 Direct SQL 核对却各自只实现 `sum` 和 `count`，对不上的种类被静默丢掉。模型于是自己编译除法，校验器无法证明它等于某条已发布口径，按 [查询架构演进](../semantic-model/query-architecture-evolution.md) 路径 B 整句拒绝。未授权表达式和越权表、写入不是同一种失败：前者应回到空间投影里的指标清单并允许修复，后者保持终局拒绝。平均订单金额若要出数，作为非可加的 `ratio` 发布，分子 `orders.order_amount`、分母 `orders.order_count`，提示和校验共用同一份表达式投影。空间引用了投影器不会生成的表达式种类时，启动应失败，而不是从提示里拿掉。不要把这个口径叫客单价。
