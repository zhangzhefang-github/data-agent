# Wiki

本目录记录跨会话仍有效的决定和事实。代码现状以日期和提交为准，不在这里描述当前实现。

## 目标

- [订单主题 Direct SQL](direct-sql-orders.md)：自然语言对话查询订单 DuckDB 的验收范围和已知工程位置。
- [客户–订单授权上下文](customer-order-context.md)：2026-11-21 晚间，`data-agent-direct-sql@c9bbcc6` 起客户集市和客户标识进入授权；姓名和支付关系仍不进入。同日收尾时，本仓库在 `f6043af` 之上把这条授权收进订单 DuckDB 的默认 Direct SQL；注入 SQL 为 99 笔、1672.0、62 行和客户标识 51，真实模型本轮未重跑。
- [支付明细与 Fanout](payment-fanout.md)：2026-11-21 晚间，支付明细先以带订单日期的集市单表查询开放；与订单的连接仍关闭。
- [平均订单金额被整句拒绝](average-order-amount.md)：2026-11-21，总金额、总笔数加平均金额的问法因未发布的除法被整句拒绝。
- [规范语义模型 Phase 1 验收](phase1-semantic-acceptance.md)：2026-11-21 晚间，`gpt-4.1-mini` 自己编写的七题 SQL 与真实订单库一致，未授权问法被拒绝。
