# 销售探索分析采用有预算的多步编排

2026-11-24 本地工作区决定。在 `data-agent@92b03047be5bebaf4a267d65eb04fc06bd00fb19`，销售问数工具可以被调用多次，但回合只返回并保存最后一次成功结果。订单 Direct SQL 在首次成功后复用该结果。销售异常诊断需要保留多次查询、依据实际结果下钻，并让报告逐项引用证据。因此在聊天入口增加独立的探索分析分支，由确定性代码管理执行状态和证据。

## 工作模式与执行边界

`/api/chat/stream` 接受 `auto`、`query`、`analysis`。`auto` 仅在销售主题空间检测到明确的异常诊断或原因调查意图时进入探索分析，显式选项优先。普通问数继续沿用 [ADR 0001](0001-structured-sales-query-boundary.md) 的销售 Query IR、[ADR 0003](0003-direct-sql-duckdb-first.md) 和 [ADR 0007](0007-readonly-sql-with-fanout.md) 的订单 DuckDB Direct SQL，以及 [ADR 0008](0008-order-query-clarification.md) 的订单澄清流程。订单显式选择探索分析时收到范围提示。普通问数的进度仍遵循[问数过程](../wiki/query-process.md)。

探索分析冻结请求的销售 YAML 授权配置。执行器从该配置的 `sales_summary` 表及字段、指标和维度映射生成参数化 Direct SQL，按同一份配置核对 SQL AST 的单条 `SELECT`、表、列和参数数量，再交给现有 `fetch_rows` 只读数据源传输层。日期、筛选值、候选门店和行数限制均绑定为参数。全源范围检查用 `MIN`、`MAX` 和 `COUNT(*)` 识别业务日期覆盖和汇总记录数。其余查询使用计划验证后的日期范围和筛选。这样可直接表达范围剖析和证据驱动的门店筛选，并把 [ADR 0001](0001-structured-sales-query-boundary.md) 的 Query IR 执行边界保留在普通问数。销售分析这层校验独立于订单 SQL Validator。[销售空间配置](../../backend/configs/sales_space.yaml) · [分析查询执行器](../../backend/app/analysis/query_executor.py)

## 规划、执行与解释

- 每个请求复用一个无工具 Agent，使用现有 `openai-agents==0.25.0`、模型配置和结构化输出。Planner 根据用户问题、业务日期和授权语义目录生成 `AnalysisPlan`。计划包含目标、范围、待检验假设、任务、依赖和停止条件，任务只能选择范围检查、每日趋势、单维度分布和门店产品下钻模板。Planner 只处理逻辑指标和维度。请求末尾可由同一 Agent 从证据支持的日期覆盖、门店产品结构、时间趋势中选择一个报告重点，数字、结论和原因措辞由确定性报告生成。解释超时或选项无效时使用已验证的基础报告。[Planner](../../backend/app/analysis/planner.py)
- Plan Validator 核对授权指标、维度、筛选和日期约束，验证任务形状、唯一性、DAG 边一致性和循环，并拒绝重复任务或超预算计划。Orchestrator 顺序执行拓扑排序后的固定任务。依赖失败时跳过下游，独立任务可继续。固定任务完成后，根据门店分布证据中的销售额统计候选实例化一层产品下钻。具体门店必须在查询结果中实际出现。[计划校验](../../backend/app/analysis/plan_validator.py) · [任务编排](../../backend/app/analysis/orchestrator.py)
- 标准库拓扑排序、现有 SDK 和请求内状态足以支撑当前任务图。由模型反复调用工具会把执行顺序和停止条件交给模型，单 Agent 的两个结构化阶段把授权、调度和证据检查留在服务内。多 Agent 会增加状态交接和排错成本，LangGraph 的图运行时和检查点在顺序执行、单层下钻的范围内也会增加维护成本。需要断点续跑或更深的动态任务图时再评估编排机制。

## 预算、证据与报告

- `SALES_ANALYSIS_BUDGET` 接受 JSON 配置。默认最多 8 次 SQL 尝试、8 项计划任务、3 层下钻预算、3 个候选门店、每任务 2 次暂时性失败重试、45 秒分析时限、5 秒可选解释时限、每次最多 500 行、分析范围最多 500 个自然日。当前只有门店到产品这一层。重试同一条已校验的 SQL，并计入总次数。服务按预算和任务规则停止探索，保留已取得的证据和未完成事项。[预算定义](../../backend/app/analysis/models.py)
- `SALES_ANALYSIS_THRESHOLDS` 接受 JSON 配置。每日序列默认至少 14 个样本，维度分布至少 5 个样本，修正 Z 分数阈值为 3.5，IQR 倍数为 1.5，候选还须达到相对中位数 20% 与绝对差值 0.01 中较大的差值门槛。Analyzer 从证据计算统计量、月度聚合和日期缺口，并标记样本不足、截断和数据质量问题。统计候选需要营业日历、同类门店或活动记录等额外证据才能判断经营表现和归因。[统计规则](../../backend/app/analysis/analyzer.py)
- 每次成功查询都保存独立的 Evidence，记录 `evidence_id`、`analysis_id`、任务 ID 与类型、指标和维度、日期与筛选、SQL 和参数、结果列与行、结果行数和可能截断标记、完成状态、耗时、数据源、数据集和空间配置快照标识。Report Validator 从证据重算发现及报告，核对引用、分类和数字，核对同口径销售额及订单量、月度与日明细、下钻与父级门店的合计。证据齐备时，按汇总净销售额和订单量校验客单价。验证失败时隐藏结论并保留成功查询证据。[证据结构](../../backend/app/analysis/evidence.py) · [报告校验](../../backend/app/analysis/report.py)
- 聊天以 SSE 发布计划、任务状态、实际下钻及报告验证状态。助手消息的可空 `analysis` JSON 保存计划、任务、发现、证据及验证结果，重开会话可查看。普通问数仍通过 `result`、`process`、`outcome` 和 `last_query` 表示既有结果及续问。[聊天流](../../backend/app/main.py) · [会话存储](../../backend/app/conversations.py)

## 后续演进

第一阶段限于销售 ADS 的业务日期、城市、门店、产品和授权指标。销售 ADS 无法观察加盟商、交易价格、商品件数、交易时间或日内采集情况。扩展到其他主题空间、更多下钻层级、跨轮细化或持久化执行状态时，需要逐项定义数据授权、停止策略和可验证的聚合口径。跨查询当前以相同授权配置和口径对账，数据源发生变化时还需评估一致读快照。
