# 查询主路径采用 Direct SQL，优先迁移订单主题空间

Agent 直接生成目标数据库的 SQL，规范语义模型提供已授权的业务口径、粒度和物理映射，确定性程序负责校验和只读执行。原有 Query IR 和 Compiler 保留为历史实现及评测基线。Phase 1 首先迁移订单主题空间的 DuckDB 查询，销售主题空间及其他数据库继续按现有配置运行，后续分批迁移。

Phase 1 仅授权订单主题空间的 `orders` 数据集及其获准字段，校验器拒绝跨表查询。切换时清空已有会话，订单 DuckDB 的续问从新保存的 Direct SQL 会话开始。「去年同期」基于最近一次成功执行的 SQL，按当前主题空间重新校验后执行。切换验收覆盖固定结果、权限拒绝、修复后重新校验和 Direct SQL 会话续问。

后续按业务需求开放具体的跨表查询。每条 JOIN 在开放前需完成显式授权和 Fanout 校验，验证指标基础粒度与查询输出粒度下的结果正确性。

本决策在订单主题空间的 DuckDB 查询范围内更新 [ADR 0002](0002-canonical-semantic-model.md) 关于 Query IR 和 Compiler 承载执行路径的安排，并更新[查询架构演进](../semantic-model/query-architecture-evolution.md)中将 Direct SQL 执行留到后续阶段的安排。[ADR 0001](0001-structured-sales-query-boundary.md) 继续适用于销售主题空间。订单主题空间沿用 ADR 0002 定义的业务口径、授权来源和结果兼容约定。

2026-11-21，上面「Phase 1 拒绝跨表」只描述当时的授权范围。[ADR 0004](0004-customer-order-equijoin.md) 按本决定的逐条开放，向订单主题空间开放客户–订单这一条不会放大行数的等值内连接。带 Fanout 的支付关系仍不开放。订单主题空间连接 DuckDB 时默认仍是 Direct SQL：已发布语义模型投影出语义上下文，模型生成原生 SQL，校验器核对后再只读执行。Query IR 和 Compiler 保留，供销售主题空间以及未切到 DuckDB 的订单查询使用，不作为订单 DuckDB 的默认路径。SQLite 和 PostgreSQL 继续保留。
