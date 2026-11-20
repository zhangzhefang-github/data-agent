# Jaffle Shop 数据源探查

`profile_jaffle_shop` 以 DuckDB 只读连接探查当前项目配置的 `jaffle_shop.duckdb`。测试通过 `SALES_DUCKDB_PATH` 指向真实文件，探查不创建、写入或修改数据库。

## Catalog

| 对象 | 类型 | 行数 | 候选键 |
| --- | --- | ---: | --- |
| `customers` | table | 100 | `customer_id` |
| `orders` | table | 99 | `order_id` |
| `raw_customers` | table | 100 | `id` |
| `raw_orders` | table | 99 | `id` |
| `raw_payments` | table | 113 | `id` |
| `stg_customers` | view | 100 | `customer_id` |
| `stg_orders` | view | 99 | `order_id` |
| `stg_payments` | view | 113 | `payment_id` |

所有候选键在此快照均无 NULL 与重复。Catalog 输出每个对象的完整字段名、DuckDB 字段类型和可空标记，供后续语义模型的物理映射校验使用。

## 已探查的数据集关系

| 关系 | 子到父匹配率 | 外键 NULL | 孤儿 | 多子记录父键 | 未被引用父记录 | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `orders.customer_id` → `customers.customer_id` | 100% | 0 | 0 | 29 | 38 | profiled |
| `stg_orders.customer_id` → `stg_customers.customer_id` | 100% | 0 | 0 | 29 | 38 | profiled |
| `raw_orders.user_id` → `raw_customers.id` | 100% | 0 | 0 | 29 | 38 | profiled |
| `stg_payments.order_id` → `stg_orders.order_id` | 100% | 0 | 0 | 13 | 0 | profiled |
| `raw_payments.order_id` → `raw_orders.id` | 100% | 0 | 0 | 13 | 0 | profiled |

Customer → Order 与 Order → Payment 仍是 `proposed` 业务关系。数据匹配证据不构成业务确认。

## 血缘和验收基线

- `raw_customers` → `stg_customers` 将 `id` 改名为 `customer_id`。
- `raw_orders` → `stg_orders` 将 `id` 改名为 `order_id`，将 `user_id` 改名为 `customer_id`。
- `raw_payments` → `stg_payments` 将 `id` 改名为 `payment_id`，并将 `amount` 转换为 `amount / 100`。

订单日期范围为 2018-01-01 至 2018-04-09。状态的快照计数为 completed 67、placed 13、return_pending 2、returned 4、shipped 13。按支付方式汇总后，订单上的四类金额与 `stg_payments` 一致：credit_card 871、coupon 185、bank_transfer 411、gift_card 205，合计 1672。同一订单内把同一支付方式的支付加总后，这四类金额也与订单对应列相符。单笔支付明细并不逐行等于订单上的对应金额：4 笔订单各有两笔信用卡支付，这 8 行的单笔金额与订单信用卡金额不同。

直接关联订单与支付明细时，99 笔订单扩展为 113 行，订单金额从 1672 重复累计为 2083。先按 `order_id` 聚合支付后，关联维持 99 行，订单与支付金额均为 1672。该结果是订单与支付分析中的 Fanout 验收基线。

客户快照有 100 名客户，62 名有订单，38 名无订单。无订单客户的 `number_of_orders` 为 NULL。订单数、首末订单日期和生命周期价值在这份快照中可与订单表重算结果对账，指标发布口径仍由后续语义模型治理。
