from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.query import TimeRange


SALES_METRICS = frozenset({'sales_amount', 'order_count', 'average_order_value'})
SALES_DIMENSIONS = frozenset({'stat_date', 'store', 'product', 'city'})


class TaskType(StrEnum):
    PROFILE_DATA_RANGE = 'profile_data_range'
    DAILY_TREND = 'daily_trend'
    DIMENSION_BREAKDOWN = 'dimension_breakdown'
    STORE_PRODUCT_DRILLDOWN = 'store_product_drilldown'


class TaskStatus(StrEnum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'


class InterpretationFocus(StrEnum):
    COVERAGE = 'coverage'
    STORE_PRODUCT = 'store_product'
    TREND = 'trend'


class PlanModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AnalysisFilter(PlanModel):
    dimension_id: str
    value: str


class AnalysisScope(PlanModel):
    space_id: str
    time_range: TimeRange | None = None
    filters: list[AnalysisFilter] = Field(default_factory=list)
    scope_issue: Literal['unsupported', 'clarification_required'] | None = None


class AnalysisHypothesis(PlanModel):
    hypothesis_id: str
    statement: str


class AnalysisTask(PlanModel):
    task_id: str
    task_type: TaskType
    title: str
    objective: str
    metric_ids: list[str]
    dimension_ids: list[str]
    depends_on: list[str]
    rationale: str


class AnalysisDependency(PlanModel):
    task_id: str
    depends_on: str


class AnalysisPlanDraft(PlanModel):
    analysis_id: str
    goal: str
    scope: AnalysisScope
    hypotheses: list[AnalysisHypothesis]
    tasks: list[AnalysisTask]
    stop_conditions: list[str]


class AnalysisPlan(AnalysisPlanDraft):
    dependencies: list[AnalysisDependency]


class AnalysisInterpretation(PlanModel):
    focus: InterpretationFocus | None


class AnalysisBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_queries: int = Field(default=8, ge=1)
    max_tasks: int = Field(default=8, ge=1)
    max_drilldown_depth: int = Field(default=3, ge=0)
    max_diagnostic_stores: int = Field(default=3, ge=0)
    max_repairs: int = Field(default=2, ge=0)
    timeout_seconds: float = Field(default=45, gt=0, allow_inf_nan=False)
    interpretation_timeout_seconds: float = Field(default=5, ge=0, allow_inf_nan=False)
    result_limit: int = Field(default=500, ge=1, le=500)
    max_range_days: int = Field(default=500, ge=1)


class TaskRun(BaseModel):
    task_id: str
    task_type: TaskType
    title: str
    status: TaskStatus = TaskStatus.PENDING
    depends_on: list[str]
    attempts: int = 0
    depth: int = 0
    store_id: str | None = None
    evidence_id: str | None = None
    reason: str | None = None
