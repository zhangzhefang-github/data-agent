# Canonical Semantic Model v1

Canonical Semantic Model 以版本化 YAML 定义业务语义，独立于 Agent、Query IR、Compiler 和数据库连接。Pydantic Loader 负责结构与版本，Validator 负责引用、粒度、关系、表达式与物理映射，Registry 仅发布没有 Hard Error 的模型。

| 层 | 对象 | 作用 |
| --- | --- | --- |
| 业务概念 | Domain、BusinessObject、BusinessAttribute、BusinessRelationship | 记录 Customer、Order、Payment 及业务关系 |
| 分析语义 | Metric、Dimension、MetricExpression、MetricFilter | 记录指标口径、维度和兼容性 |
| 逻辑数据 | Dataset、DatasetField、DatasetRelationship | 记录粒度、键、字段、关系及 Fanout 风险 |
| 物理映射 | DataSource、PhysicalMapping | 记录表、视图和字段映射 |

Jaffle Shop 模型具有 8 个真实数据集、Customer、Order、Payment 三个业务对象和六个已发布订单指标。`order_mart` 的数据集与订单指标基础粒度均为一笔订单，按状态分组时查询输出粒度为每个状态一行。`orders.order_count` 是所有状态的订单笔数规范 ID。Customer → Order 和 Order → Payment 是 proposed 业务关系，五条数据集关系是基于只读快照的 profiled 关系。

指标表达式使用结构化聚合节点，支持 SUM、COUNT、COUNT_DISTINCT、AVG、MIN、MAX、指标引用和比例。比例声明分子、分母及分母为零时的行为。模型可表达的聚合范围高于当前确定性 Compiler 支持的子集。

每个诊断包含对象、字段、代码、原因和修复建议。Hard Error 会阻止发布。真实物理 Catalog 由外部只读接口作为显式输入传入校验器，连接配置不属于模型。

`profiled` 只记录只读数据探查证据。查询计划还需要点名一条获授权的 `confirmed` 数据集关系。`validate_dataset_join` 拒绝未声明、未授权、同优先级歧义及未处理 Fanout 的关联。存在 Fanout 风险时，来源必须先聚合回其声明粒度。2026-11-22，[ADR 0007](../adr/0007-readonly-sql-with-fanout.md) 规定这步预聚合写在同一条只读查询的 WITH 或子查询里，连接本身不必先从 SQL 里拿掉。
