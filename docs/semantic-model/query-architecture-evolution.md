# 查询架构演进

[ADR 0001](../adr/0001-structured-sales-query-boundary.md) 保留结构化 Query IR 与确定性 Compiler 作为当前执行路径。[ADR 0002](../adr/0002-canonical-semantic-model.md) 将业务口径、授权和映射提升到 Canonical Semantic Model。

| 路径 | 流程 | Phase 1 状态 |
| --- | --- | --- |
| 结构化查询 | 问题 → Semantic Context → Query IR → 语义校验 → 确定性 Compiler → 只读执行 | 已接入订单 Space |
| 灵活 SQL | 问题 → Semantic Context → LLM SQL → SQL AST 校验 → 语义校验 → 执行保护 | 仅定义边界 |

两条路径共享同一份模型、指标、维度、物理映射、关系证据和 Space 授权。Query IR 不再承载业务定义，也不是唯一查询表达方式。

## 订单路径兼容

订单 Space 的 Context 生成兼容 Query IR 目录。它只投影已经授权的六个订单指标、订单日期和订单状态。当前 Compiler 使用该投影的一数据集子集，并继续生成对 `orders` 表的参数化只读 SQL。

规范 ID `orders.order_count` 与旧输入 ID `order_rows` 都可作为指标和排序字段。适配层在校验、执行和保存会话前归一到 `order_rows`，因此重复传入两个 ID 会被拒绝。结果列、展示 SQL 别名和历史 Query IR 一直保留 `order_rows`。销售 Space 的 `order_count` 仍表示有效订单量，未通过此适配层改变。

固定的 2026 业务日期下，订单 Space 的默认「今年」查询为空。2018-01-01 至 2018-04-09 的真实数据按状态共 99 笔订单、金额 1672。状态过滤接受 Space 定义的中文业务值和物理值，并将多个等值状态过滤编译为同一字段的 `IN` 条件。日期过滤与时间范围取交集。

## 灵活 SQL 的确定性边界

未来 SQL 路径使用 SQLGlot 限制单条只读 SELECT，检查方言、表和字段白名单，拒绝写入、DDL 与多语句。语义校验还须验证已定义指标、维度映射、授权的可信 JOIN、路径歧义、粒度与 Fanout。执行层负责只读连接、超时、返回行数、用户权限与数据范围。

Phase 1 没有实现 LLM SQL 生成、完整 AST 语义校验或自我修正循环。
