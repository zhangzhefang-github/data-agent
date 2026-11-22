# Wiki

本目录记录跨会话仍有效的决定和事实。代码现状以日期和提交为准，不在这里描述当前实现。

## 目标

- [订单主题 Direct SQL](direct-sql-orders.md)：自然语言对话查询订单 DuckDB 的验收范围和已知工程位置。
- [客户–订单授权上下文](customer-order-context.md)：2026-11-21 晚间，`data-agent-direct-sql@c9bbcc6` 起客户集市和客户标识进入授权；姓名和支付关系仍不进入。同日收尾时，本仓库在 `f6043af` 之上把这条授权收进订单 DuckDB 的默认 Direct SQL；注入 SQL 为 99 笔、1672.0、62 行和客户标识 51，真实模型本轮未重跑。
- [支付明细与 Fanout](payment-fanout.md)：2026-11-21 晚间，支付明细以带订单日期的集市单表查询写入 direct-sql 工作区；与订单的连接仍关闭。2026-11-22，真实模型未连接支付明细并返回订单金额 1672.0，评测仍判失败，执行器不改。
- [补齐 Wren 问数](wren-parity-goal.md)：2026-11-21 晚间，下一窗口要补平均、收入、按月、客户档案、姓名、支付明细；raw 和 staging 仍不进默认清单。
- [平均订单金额被整句拒绝](average-order-amount.md)：2026-11-21，总金额、总笔数加平均金额的问法因未发布的除法被整句拒绝。
- [规范语义模型 Phase 1 验收](phase1-semantic-acceptance.md)：2026-11-21 晚间，`gpt-4.1-mini` 自己编写的七题 SQL 与真实订单库一致，未授权问法被拒绝。
- [空间授权与语义上下文](space-authorization-context.md)：2026-11-21，在 `5600d5c` 的审计之后决定空间授权是硬边界，检索只缩小提示，校验仍用完整授权。

## 产品对标

- [Genie Chat mode 对标](genie-chat-mode-gap.md) 记录 2026-11-22 的产品差距和建议顺序。
- [Genie 的 SQL 形状](genie-sql-shape.md)：2026-11-22，Genie 只限制只读和空间内的表。WITH 用来在一条查询里把 Fanout 汇总到正确粒度，比固定一种 JOIN 更灵活；放大后的订单指标仍然拒绝。决定见 [ADR 0007](../adr/0007-readonly-sql-with-fanout.md)。
