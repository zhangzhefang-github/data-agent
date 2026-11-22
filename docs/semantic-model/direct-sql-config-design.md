# Direct SQL 配置设计

Direct SQL 的执行器不写死表名。表会变，配置清单跟着变。方言、空间授权、语义模型分成三份，每次请求拼成提示。

| 配置 | 变更多少 | 内容 |
| --- | --- | --- |
| 方言 `backend/configs/sql_dialects/duckdb.yaml` | 数据库版本不变就不改 | 版本前缀、该版本的 SQL 写法、一条经典示例 |
| 空间 `backend/configs/semantic/spaces.yaml` | 加表、加关系时改 | 这个空间授权哪些数据集、哪些关系 |
| 语义模型 `backend/configs/semantic/jaffle_shop.yaml` | 已有 | 全部数据集、物理表、指标、维度、关系证据。2026-11-21 起，业务关系可以用 `dataset_relationship_ids` 指向一条或多条数据集 JOIN |

2026-11-21 起，订单主题空间的运行时目录由已发布规范语义模型投影，不再读取 `orders_space.yaml`。销售主题空间仍读取 `sales_space.yaml`。Direct SQL 的表清单、连接和提示使用同一次订单空间投影。

2026-11-21，在 `5600d5c` 的审计里，上面「同一次投影」还没有落到一次请求的同一个对象：提示会多次构建上下文，执行器使用进程启动时的目录。同日按 [ADR 0006](../adr/0006-space-authorization-bounds-retrieval.md) 改为一次请求一份快照。订单空间仍渲染完整授权。校验读取这份快照的完整授权，不读取检索子集。

## 方言

方言不包含业务表名，也不包含库里实际有哪些日期和多少行。示例用虚构表，写成订单校验器允许模仿的单条 `SELECT`：日期用 `DATE` 字面量，筛选写在 `WHERE`，按列汇总。示例不写表关联，也不使用 `WITH`、子查询、`DISTINCT` 或 `date_trunc`。2026-11-21 的真实模型曾照抄旧的 `WITH` 示例，被校验器拒绝。

```yaml
engine: duckdb
version: "v1."
defaults:
  limit: 500
rules:
  - 用户说出时间时，只把日期列与 DATE 'YYYY-MM-DD' 字面量比较或写成 BETWEEN，不要使用 date_trunc。
  - 用户没有说出时间时，不添加日期条件。
  - 未写 LIMIT 时使用默认行数上限。
  - 只提交一条只读 SELECT。可以用 WITH、子查询、JOIN、GROUP BY 和窗口函数。订单笔数和订单金额要在一笔订单一行上计算。
examples:
  - sql: >
      SELECT
        customer_id,
        SUM(total_price) AS weekly_total
      FROM main.sales_transactions
      WHERE sold_at BETWEEN DATE '2024-01-01' AND DATE '2024-01-07'
      GROUP BY customer_id
```

示例只示范用户说出时间时的日期字面量。用户没说时间时，SQL 不补日期，只套用 `defaults.limit`。连接时执行 `SELECT version()`，版本前缀与 `version` 一致才把真实版本号写入提示。执行器不再在缺少日期时改写成今年至今。

2026-11-22，规则里的「可以用 WITH、子查询、JOIN、GROUP BY 和窗口函数」按 [ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 写入方言。经典示例仍是一条不含 WITH 的虚构 SELECT，示例表名不授予访问权限。写入、改结构和未列出的表仍然拒绝。同日校验器已按此执行，改动尚未提交。见 [Genie 的 SQL 形状](../wiki/genie-sql-shape.md)。

## 空间授权

空间从语义模型里挑选数据集和关系。加上一张表，就是往清单里加一项，不改执行器。2026-11-22 之前，关系清单为空表示不允许跨表。`data-agent@f9bc191` 仍是这个行为。[ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 改为：空间列出的表可以在一条只读查询里连接；关系清单用来标明 Fanout 和汇总键，不再充当 JOIN 白名单。

当前发布清单只有订单集市 `orders`。表和字段上的口径写在各自的元数据里，不写进方言 `rules`，也不堆成一份随表增长的规则清单。

| 口径 | 落点 |
| --- | --- |
| `amount` 是订单金额，别名 `order_amount`；四种支付列之和等于 `amount`，不要再相加 | 指标元数据 |
| 订单笔数按每一行计一笔，包含全部状态，别名 `order_rows` | 指标元数据 |
| 状态中文名和 `placed` 等物理值 | 维度 `dimension_values` |
| 公历季度、Asia/Shanghai、没有二月起算的财年 | 空间的日历，全空间一份 |

客户表和支付表还没进入授权清单，元数据里不写「必须带上客户」或支付表关联。

```yaml
spaces:
  - id: orders
    dataset_ids:
      - order_mart
    relationship_ids: []
    dimension_values:
      orders.status:
        已下单: placed
        已发货: shipped
        已完成: completed
        退货中: return_pending
        已退货: returned
```

扩展客户时增加已探查的关系，不把 raw、staging 放进清单：

```yaml
dataset_ids:
  - order_mart
  - customer_mart
relationship_ids:
  - order_mart_customer_mart
```

支付关系带 Fanout 风险。授权时必须声明先按订单汇总，执行器才允许对应的子查询：

```yaml
dataset_ids:
  - order_mart
  - customer_mart
  - payment_mart
relationship_ids:
  - order_mart_customer_mart
  - payment_to_order
fanout_rules:
  payment_to_order:
    aggregate_to: order_id
    measures: [amount]
```

`payment_mart` 和 `payment_to_order` 目前尚未发布。未写入 `dataset_ids` 的表，模型不可访问。

## 一次请求交给模型的内容

启动时核对三件事：方言版本前缀合法，空间引用的数据集和关系都在已发布模型中，带 Fanout 的关系写明了汇总键。核对失败则拒绝启动。

每次请求的提示由四块组成：

1. 方言规则和经典示例。
2. 空间投影：授权表和列、指标表达式与别名、状态字典、已授权关系。未授权的关系不出现。
3. 这些表的列类型，来自只读 `DESCRIBE`，只保留授权列。
4. 业务日期。续问已有确定 SQL 时，只放那条 SQL。

提示中不放行数、最小最大日期、样例查询结果、连接串、raw 表、staging 表，以及粒度、主键、Fanout 证据。这些留在校验器。

当前清单只有 `order_mart` 时，模型只能看到 `orders` 及其获准列。经典示例里的表不会变成访问权限。是否关联多张表，由模型根据本次授权的关系决定。

## 执行器

2026-11-22 之前，执行器读取空间的 `dataset_ids` 和 `relationship_ids`。SQL 中的表必须落在授权数据集的物理表上。关系不在清单中，则拒绝 `JOIN`。关系标了 Fanout，则子表必须先按声明的键汇总。写入、第二条语句、未授权列仍然拒绝。这是 `data-agent@f9bc191` 的行为。同日工作区已改为下一段，改动尚未提交。

[ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 把 JOIN 的门槛改成表是否在空间清单里，不再要求每条连接都先写入 `relationship_ids`。带 Fanout 的一侧要在同一条查询的 WITH 或子查询里先回到指标基础粒度。订单笔数和订单金额停在支付行上汇总时拒绝。写入、第二条语句、未授权列和未列出的表仍然拒绝。加表仍然只改空间清单。
