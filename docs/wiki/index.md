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

## 工程

- [问数回合收尾](turn-outcome.md)：2026-11-23，统一问数终态、收紧澄清、把上一轮已验证结果送进下一轮提示；不新增意图路由。盘点针对 `implement/orders-chat-34@4115fe0` 及当日未提交改动。2026-11-23 推送 `895353f` 时这页留在本地，因为盘点的不是本仓库 `main`。
- [问数过程](query-process.md)：2026-11-23，提问时展示理解问题、查询标题和整理说明。标题来自当次 SQL 或结构化查询，不另写过程说明。提交为 `data-agent@895353f`。
- [订单问数真实评测基线](orders-live-evaluation-baseline.md) 记录 2026-11-22 的 60 题成绩、安全门失败和 2026-11-23 的 SQL 校验回放。2026-11-23 推送 `895353f` 时留在本地，回放还没有可定位的补丁或产物。
- [观测选型：Langfuse](observability-langfuse.md)：2026-11-22，判断接 Langfuse 自托比 LangSmith 更合适。两家都能挂上 `openai-agents`，不要照抄会换掉 OpenAI 上传的快速开始。用户尚未收成 ADR。2026-11-23 推送 `895353f` 时这页和调研笔记留在本地。

## 产品对标

- [Genie Chat mode 对标](genie-chat-mode-gap.md) 记录 2026-11-22 的问数产品差距，以及 [11-24 销售探索分析的本地验证与对标](genie-chat-mode-gap.md#2026-11-24-销售探索分析与-genie-agent-mode)。设计见 [ADR 0009](../adr/0009-analysis-mode-orchestration.md)。
- [Genie 的 SQL 形状](genie-sql-shape.md)：2026-11-22，Genie 只限制只读和空间内的表。WITH 用来在一条查询里把 Fanout 汇总到正确粒度，比固定一种 JOIN 更灵活；放大后的订单指标仍然拒绝。决定见 [ADR 0007](../adr/0007-readonly-sql-with-fanout.md)。

## 2026-11-23 评审待办

- [ ] 提交观测文档前，写清自托模式如何独立启用 trace，并配置 Langfuse 为唯一导出目的地。[观测选型](observability-langfuse.md#2026-11-22-的判断)
- [ ] 提交观测文档前，更正关闭敏感数据后的 Responses token 用量结论，区分 SDK 留存与文档引用版本的第三方处理器实际导出。[调研笔记](../research/langsmith-vs-langfuse.md#自托与数据出境)
- [x] 2026-11-23 推送 `895353f` 时，本机符号链接 `external/ossie` 和 `docs/wren-jaffle/` 下的三个条目未进入提交。下次提交前仍要排除它们。
- [ ] 给评测基线和 SQL 回放补充可定位的运行提交及补丁或产物引用。证据缺失时注明可复现范围。[评测基线](orders-live-evaluation-baseline.md#2026-11-23-回放)
