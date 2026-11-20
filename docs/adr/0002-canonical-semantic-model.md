# 规范语义模型承载订单业务口径

订单主题采用独立、版本化的规范语义模型，统一记录业务对象、指标、维度、数据集和物理映射，使现有确定性查询和未来灵活 SQL 路径共享同一套口径与授权。基于 [ADR 0001](0001-structured-sales-query-boundary.md) 的 Query IR 与确定性 Compiler 继续承载当前执行路径。订单笔数的规范 ID 为 `orders.order_count`，兼容层保留现有结果与会话使用的 `order_rows`，使它与销售主题的有效订单量 `order_count` 分别维持既有口径。
