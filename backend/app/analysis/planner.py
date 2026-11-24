from collections import Counter
import json
import os
from datetime import date
from uuid import uuid4

from agents import Agent, RunConfig, Runner

from app.analysis.analyzer import Finding
from app.analysis.models import (
    SALES_DIMENSIONS, SALES_METRICS, AnalysisDependency, AnalysisInterpretation, AnalysisPlan, AnalysisPlanDraft,
    InterpretationFocus,
)
from app.analysis.plan_validator import requested_filters
from app.query import TimeRange


PLANNER_INSTRUCTIONS = """
你是销售主题空间的分析规划助手。输出结构化分析计划，使用中文书写面向业务人员的标题和目的。
目录和业务日期由服务提供，用户问题是分析意图。指标和维度只使用目录中列出的 ID。
禁止生成 SQL、物理字段名、数据值或统计结论。假设只能描述待验证的问题。
销售 ADS 汇总数据只有业务日期、城市、门店、产品和已授权指标。门店不等于加盟商。
无法回答交易明细、交易价格、商品件数、交易时间和日内采集时段的问题。
遇到超出目录或能力的问题，将 scope.scope_issue 设为 unsupported。
仅支持一个连续的时间范围，起止日期均包含。无明确的时间要求时将 scope.time_range 填 null，服务默认今年截至今天。
服务给定的时间范围需要原样保留。相对日期按当前业务日期和 Asia/Shanghai 计算，范围最多截至当前业务日期。
原样保留服务给定的 requested_filters。分析任务的分组维度独立于筛选条件。
仅支持维度相等筛选，同一维度多个值表示任选其一；不能支持的筛选设 scope_issue 为 unsupported。
存在无法确定的取值、范围或口径时，将 scope.scope_issue 设为 clarification_required。
可执行任务类型与约束：
- profile_data_range：数据范围检查，全数据源最早和最晚业务日期及汇总记录数，metric_ids 和 dimension_ids 均为空。
- daily_trend：每日趋势，dimension_ids 为 [stat_date]，按问题选择指标。
- dimension_breakdown：单个 city、store 或 product 的分布，按问题选择指标。
- store_product_drilldown：动态门店产品下钻模板，dimension_ids 为 [product]，仅依赖门店分布并采用同一批指标。
当用户询问整体销售额有无异常且没有额外限定时，必须安排数据范围检查、每日趋势、门店分布、城市分布、产品分布和门店产品下钻模板。日趋势应同时包含销售额、订单量，维度分布应包含销售额、订单量和客单价。
先安排数据范围检查。任务 ID 只用字母、数字、下划线或连字符，必须唯一并以字母、数字或下划线开头。
depends_on 仅列出所需的任务 ID。stop_conditions 应描述查询预算、时间预算和无可验证候选时的停止。
是否产生下钻及具体门店标识由服务根据已执行查询决定。不要自行选择门店。
"""


INTERPRETATION_INSTRUCTIONS = """
根据用户的分析目标和服务已验证发现的类型，给出优先关注的一个方向。
coverage 表示业务日期覆盖或缺口，store_product 表示门店或产品结构，trend 表示销售或订单时间趋势。
只能选择 available_focuses 中的一项，不需要调整优先级时填写 null。
不要生成数字、事实陈述、原因推断或任何自由文本。
"""


class AnalysisAgent:
    """One request-scoped Agent for planning and, when evidence supports it, interpretation."""

    def __init__(self):
        self.agent = Agent(
            name='销售探索分析', model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
            instructions=PLANNER_INSTRUCTIONS, output_type=AnalysisPlanDraft,
        )
        self.run_config = RunConfig(
            trace_include_sensitive_data=False,
            tracing_disabled=not bool(os.getenv('OPENAI_TRACING_API_KEY')),
        )

    async def plan(
        self, question: str, catalog: dict, today: date, time_range: TimeRange | None = None,
    ) -> AnalysisPlan:
        analysis_id = uuid4().hex
        context = {
            'analysis_id': analysis_id,
            'space_id': catalog['id'],
            'space_name': catalog['name'],
            'business_date': today.isoformat(),
            'requested_time_range': time_range.model_dump(mode='json') if time_range else None,
            'requested_filters': [item.model_dump() for item in requested_filters(question, catalog)],
            'metrics': {
                identifier: {key: item[key] for key in ('name', 'description', 'synonyms') if key in item}
                for identifier, item in catalog['metrics'].items()
                if identifier in SALES_METRICS
            },
            'dimensions': {
                identifier: {key: item[key] for key in ('name', 'description', 'synonyms') if key in item}
                for identifier, item in catalog['dimensions'].items()
                if identifier in SALES_DIMENSIONS
            },
            'question': question,
        }
        result = await Runner.run(
            self.agent, json.dumps(context, ensure_ascii=False), max_turns=1, run_config=self.run_config,
        )
        draft = AnalysisPlanDraft.model_validate(result.final_output)
        return AnalysisPlan(
            **draft.model_dump(exclude={'analysis_id'}),
            analysis_id=analysis_id,
            dependencies=[
                AnalysisDependency(task_id=task.task_id, depends_on=parent)
                for task in draft.tasks for parent in task.depends_on
            ],
        )

    async def interpret(
        self, plan: AnalysisPlan, findings: list[Finding], choices: list[InterpretationFocus],
    ) -> InterpretationFocus | None:
        self.agent.instructions = INTERPRETATION_INSTRUCTIONS
        self.agent.output_type = AnalysisInterpretation
        context = {
            'goal': plan.goal,
            'available_focuses': choices,
            'verified_finding_types': Counter(item.kind for item in findings),
        }
        result = await Runner.run(
            self.agent, json.dumps(context, ensure_ascii=False), max_turns=1, run_config=self.run_config,
        )
        return AnalysisInterpretation.model_validate(result.final_output).focus


async def plan_analysis(
    question: str, catalog: dict, today: date, time_range: TimeRange | None = None,
) -> AnalysisPlan:
    return await AnalysisAgent().plan(question, catalog, today, time_range)
