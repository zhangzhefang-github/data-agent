from typing import Literal

from pydantic import BaseModel, Field

from app.analysis.models import AnalysisFilter, TaskType
from app.query import TimeRange


class Evidence(BaseModel):
    evidence_id: str
    analysis_id: str
    task_id: str
    task_type: TaskType
    metric_ids: list[str]
    dimension_ids: list[str]
    time_range: TimeRange | None
    filters: list[AnalysisFilter] = Field(default_factory=list)
    sql: str
    parameters: list[str | int] = Field(default_factory=list)
    columns: list[str]
    rows: list[list[str | int | float | None]]
    row_count: int
    result_limit: int = 500
    possible_truncation: bool = False
    status: Literal['COMPLETED'] = 'COMPLETED'
    elapsed_ms: float
    source: str
    snapshot_id: str
    dataset_id: str
    store_id: str | None = None
