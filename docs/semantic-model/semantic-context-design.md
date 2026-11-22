# Semantic Context 设计

2026-11-21，下面「订单 Space 不包含客户、也不授权 JOIN」的描述已被 [ADR 0004](../adr/0004-customer-order-equijoin.md) 取代。检索与授权的边界见 [ADR 0006](../adr/0006-space-authorization-bounds-retrieval.md)：空间授权是硬边界，短目录只缩小提示，校验使用完整授权。规范语义模型是唯一业务口径，Wiki 不是运行时口径。2026-11-22，[ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 规定空间列出的表可以在一条只读查询里用 WITH 连接；未进入空间清单的表仍然不能出现在 SQL 投影里。

`build_semantic_context(domain_id, space_id, user_question=None)` 从已发布模型构建结构化、可序列化的上下文。Domain 决定语义来源，Space 提供显式许可，问题仅在该许可范围内选择条目。

| 投影 | 内容 | 消费方 |
| --- | --- | --- |
| 业务语义 | 获准业务对象、指标定义、兼容维度、日期角色、同义词和业务过滤规则 | Agent 和问数工具 |
| SQL 生成元数据 | 需要的逻辑数据集、物理对象与字段映射、获准可信 JOIN 和 SQL 示例 | 当前 Compiler 适配层和未来 SQL 路径 |
| 内部校验元数据 | Space 授权、字段用途、关系证据、粒度和风险规则 | 确定性校验与执行侧 |

业务和 SQL 投影可序列化。内部校验元数据保存在 Context 对象中，但不属于序列化输出。数据源路径、连接串和凭据由执行环境保管。

稳定 ID、名称、同义词和兼容别名决定条目选择。明确匹配时补齐指标的时间维度依赖，问题为空或没有匹配时返回该主题空间的获准语义上下文。排序依照 Space 声明稳定。未知 Domain、未知 Space 或跨 Domain 组合会被拒绝。

## 订单 Space

订单 Space 开放 Order、六个订单指标、`orders.order_date` 和 `orders.status`。状态投影包含已下单、已发货、已完成、退货中和已退货对应的物理值。`order_rows` 是 `orders.order_count` 的现有接口别名。客户、支付、raw/staging、姓名和技术主键留在模型治理范围，未进入订单 Context。数据集关系需要同时被 Space 授权且为 `confirmed` 才会出现在 SQL 投影。当前订单 Space 未授权 JOIN。

示例问题「今年各订单状态的订单金额和订单笔数」会提供 Order、`orders.order_amount`、`orders.order_count`、日期和状态，以及 `order_mart` 的物理映射。后续查询入口通过同一 Space 配置消费此投影。
