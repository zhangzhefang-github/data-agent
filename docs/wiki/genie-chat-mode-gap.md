# Genie Chat mode 对标

2026-11-22，对照 Databricks on AWS 的 Genie Agents（原 Genie Spaces）Chat mode，范围为业务人员在已治理的数据上用自然语言问数，以及分析师配置、评测和分享该问数入口的流程。多步分析属于 Agent mode。[模式说明](https://docs.databricks.com/aws/en/genie-agents/concepts#choose-the-right-mode) · [问数体验](https://docs.databricks.com/aws/en/genie-agents/talk-to-genie)

## 2026-11-22 对照记录

- `data-agent@f9bc191` 已有流式问数、会话历史、结果表、SQL、手动切换图表和正确／错误反馈。订单 DuckDB 能根据完整空间授权回答「能查什么」，续问使用最近一次成功的查询。Genie Chat mode 还提供可点击常用问题、空间上下文浏览、自动图表和带原因的反馈。[问数界面](../../frontend/src/main.tsx) · [结果界面](../../frontend/src/ResultView.tsx) · [问数接口](../../backend/app/main.py) · [Genie 问数体验](https://docs.databricks.com/aws/en/genie-agents/talk-to-genie)
- `data-agent@f9bc191` 每个进程从环境变量选择一个 Space，授权及示例由 YAML 管理，语义注册表在内存中校验并发布。分析师还没有编辑、审核及回滚空间版本的界面，也没有受审核的自然语言问题与可信 SQL 配对执行的流程。Genie 可由分析师配置示例与可信资产，实际使用受信参数化查询或函数时可显示经验证答案。[问数接口](../../backend/app/main.py) · [空间配置](../../backend/configs/semantic/spaces.yaml) · [语义注册表](../../backend/app/semantic/registry.py) · [Genie 可信资产](https://docs.databricks.com/aws/en/genie-agents/tune-quality#trusted-assets)
- `data-agent@f9bc191` 每条回答存一条可覆盖的二元反馈。2026-11-22 工作区另有未提交的订单 Golden Dataset 和 CLI，支持以真实模型生成结果、按期望结果评分。Genie 可复核反馈，并用参考 SQL 的结果集评测回答。[反馈与历史存储](../../backend/app/conversations.py) · [评测运行器](../../backend/app/evaluation/runner.py) · [订单评测数据](../../backend/app/evaluation/orders_golden.json) · [Genie 评测](https://docs.databricks.com/aws/en/genie-agents/monitor#benchmarks)
- `data-agent@f9bc191` 的 Space 授权限定模型可用的语义资产。会话和反馈接口共用本地存储，记录没有用户或 Space 归属。Genie 分开检查 Agent 的权限与终端用户在 Unity Catalog 的数据权限，并把查询归到实际用户。[空间授权与语义上下文](space-authorization-context.md) · [ADR 0006](../adr/0006-space-authorization-bounds-retrieval.md) · [反馈与历史存储](../../backend/app/conversations.py) · [Genie 分享](https://docs.databricks.com/aws/en/genie-agents/set-up#share-a-genie-agent)
- 2026-11-22 核对 `data-agent@f9bc191`，订单空间仅授权订单与客户集市和两者间的等值内连接。平均订单金额、收入、按月、客户档案、姓名和支付明细仍未进入授权或默认执行路径。本地 DuckDB 已有 `payments` 视图，单表开放决定与实现草稿记在另一工作区的历史记录中。[空间清单](../../backend/configs/semantic/spaces.yaml#L16) · [补齐 Wren 问数](wren-parity-goal.md) · [支付明细与 Fanout](payment-fanout.md)
- 2026-11-22，用户贴出 Genie 的产品回复：对方只限制只读，以及只能查询工作空间架构里列出的表；WITH、子查询、多表 JOIN、GROUP BY 和窗口函数都支持，支付字段也可以连接交易表。`data-agent@f9bc191` 额外禁止这些句法，并把支付与订单的连接关掉。下一步补的是这道句法差距，订单指标被支付行放大时仍拒绝。见 [Genie 的 SQL 形状](genie-sql-shape.md)。同日稍后，用户确定用 WITH 处理 Fanout，不规定唯一的连接模板。
- 2026-11-22，用本地 Jaffle Shop DuckDB 对该工作区运行后端测试，141 项通过，1 项失败。源数据剖析测试预期八个对象，库里多出 `payments` 视图。销售确定性评测 11/11 通过。订单 CLI 的授权范围题通过，另外 12 题因未配置 API 密钥跳过。本轮没有调用真实模型。[源数据剖析测试](../../backend/tests/test_source_profile.py#L25) · [评测运行器](../../backend/app/evaluation/runner.py)

## 2026-11-23 问数过程

2026-11-23，用户指出提问时页面只有「查询中」，Genie 在「Analyzing the data」下列出分析步骤。决定见 [问数过程](query-process.md)：展示真实阶段，不另开叙述模型，也不做 Agent mode 的「跳过分析」。

## 2026-11-22 分析建议

1. 把 Wren 对照订单题作为第一项质量里程碑。用真实模型比较实际结果集，覆盖续问、应拒绝的访问和「能查什么」，留存可复查成绩。将拒绝题的通过条件收紧为明确的授权拒绝，避免工作区评测把一般请求失败算作通过。[补齐 Wren 问数](wren-parity-goal.md) · [拒绝题评分](../../backend/app/evaluation/runner.py#L236)
2. 让分析师编辑并审核 Space 版本、发布通过同一份空间授权校验的可信 SQL，将反馈关联到查询和语义快照，并把复核过的问题收入评测集。
3. 在多人分享前，补齐用户身份、Space 与数据权限，以及按用户和 Space 隔离的历史与反馈。多步 Agent mode 和 Databricks 平台级集成延后评估。

## 2026-11-24 销售探索分析与 Genie Agent mode

以 `data-agent@92b03047be5bebaf4a267d65eb04fc06bd00fb19` 为盘点基线，该提交的销售和订单聊天只保存最后一次成功查询。2026-11-24 工作区新增销售探索分析，以独立模式提供结构化计划、实际任务状态、多个只读 SQL 结果、门店产品下钻和经证据校验的报告。报告含图表、查询证据及可查看的 SQL，任务和证据可随会话重开。历史问数过程见原有产品决定。[ADR 0009](../adr/0009-analysis-mode-orchestration.md) · [问数过程](query-process.md)

2026-11-24 验证时，销售探索分析首版仍在本地工作区，尚未合入 `main`。前端构建通过，探索分析专项测试 66 项全部通过。聊天集成测试用合成销售数据验证了两种查询范围。日期覆盖检查统计整个销售 ADS，趋势和维度查询保留用户指定的日期、城市和产品筛选。测试使用替代规划器，真实模型端到端验收待做。[聊天集成测试](../../backend/tests/test_analysis_chat.py#L161) · [任务编排测试](../../backend/tests/test_analysis_orchestrator.py) · [统计与报告测试](../../backend/tests/test_analysis_statistics.py)

- **研究灵活度**。Genie 可根据每步结果调整研究计划，分析中可追问澄清，报告完成后可继续细化。当前销售探索分析采用有限任务枚举和单层门店产品下钻，且仅覆盖 `sales_summary` 的汇总口径。[Genie Agent mode](https://docs.databricks.com/aws/en/genie-agents/concepts#agent-mode) · [Genie 对话](https://docs.databricks.com/aws/en/genie-agents/talk-to-genie#use-agent-mode)
- **数据与工具来源**。Genie 可用 Unity Catalog volumes 的非结构化文件（公共预览）以及 MCP 连接的数据源和工具。当前探索分析的证据来源为销售 ADS。[Genie 数据来源](https://docs.databricks.com/aws/en/genie-agents/concepts#what-data-can-i-use)
- **交互和分享**。Genie 提供 `Answer now`，可以在探索过程中结束并用已有材料作答，也支持导出带图表和引证的 PDF，以及提交报告给有权限的作者复核。当前分析以执行预算停止，通过本地会话查看报告和证据。[Genie 对话](https://docs.databricks.com/aws/en/genie-agents/talk-to-genie#use-agent-mode)
- **治理**。Genie 分开管理计算访问和按用户身份执行的 Unity Catalog 数据权限，支持逐用户的行过滤、列掩码和审计归属。当前销售分析以 YAML 的空间授权和本地证据记录为边界，分析师审核发布、用户与空间隔离、共享及用户级治理仍待建设。[Genie 数据访问](https://docs.databricks.com/aws/en/genie-agents/concepts#how-data-access-works)
