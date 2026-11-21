# 查询主路径采用 Direct SQL，优先迁移订单主题空间

Agent 直接生成目标数据库的 SQL，规范语义模型提供已授权的业务口径、粒度和物理映射，确定性程序负责校验和只读执行。原有 Query IR 和 Compiler 保留为历史实现及评测基线。Phase 1 首先迁移订单主题空间的 DuckDB 查询，销售主题空间及其他数据库继续按现有配置运行，后续分批迁移。

Phase 1 仅授权订单主题空间的 `orders` 数据集及其获准字段，校验器拒绝跨表查询。切换默认路径前必须通过固定结果、权限拒绝、修复后重新校验和历史追问的回归验收。已存结果沿用原样展示。处理「去年同期」等追问时，从旧 Query IR 读取查询条件作为会话上下文，按当前主题空间重新校验并走 Direct SQL。

后续按业务需求开放具体的跨表查询。每条 JOIN 在开放前需完成显式授权和 Fanout 校验，验证指标基础粒度与查询输出粒度下的结果正确性。

本决策在订单主题空间的 DuckDB 查询范围内更新 [ADR 0002](0002-canonical-semantic-model.md) 关于 Query IR 和 Compiler 承载执行路径的安排，并更新[查询架构演进](../semantic-model/query-architecture-evolution.md)中将 Direct SQL 执行留到后续阶段的安排。[ADR 0001](0001-structured-sales-query-boundary.md) 继续适用于销售主题空间。订单主题空间沿用 ADR 0002 定义的业务口径、授权来源和结果兼容约定。
