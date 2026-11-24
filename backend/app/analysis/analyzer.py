"""Compute sales findings from successful, bounded query evidence."""

import math
from datetime import date, timedelta
from statistics import median, quantiles
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.analysis.evidence import Evidence


Number = int | float
ADDITIVE_METRICS = ('sales_amount', 'order_count')


class AnalyzerConfig(BaseModel):
    min_daily_samples: int = Field(default=14, ge=4)
    min_group_samples: int = Field(default=5, ge=4)
    mad_z_threshold: float = Field(default=3.5, gt=0, allow_inf_nan=False)
    iqr_multiplier: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    min_relative_deviation: float = Field(default=0.20, ge=0, allow_inf_nan=False)
    min_absolute_deviation: float = Field(default=0.01, gt=0, allow_inf_nan=False)
    amount_tolerance: float = Field(default=0.005, ge=0, allow_inf_nan=False)
    ratio_tolerance: float = Field(default=1e-8, ge=0, allow_inf_nan=False)


class ProfileFinding(BaseModel):
    kind: Literal['data_profile'] = 'data_profile'
    evidence_id: str
    first_business_date: date | None
    last_business_date: date | None
    summary_row_count: int


class MetricFinding(BaseModel):
    kind: Literal['metric_summary'] = 'metric_summary'
    evidence_id: str
    metric_id: str
    dimension_id: str
    status: Literal['sufficient', 'insufficient_sample', 'partial_evidence']
    sample_size: int
    minimum_sample: int
    total: Number | None
    mean: float | None
    median: Number | None
    mad: Number | None
    q1: float | None
    q3: float | None
    iqr: float | None


class AnomalyFinding(BaseModel):
    kind: Literal['statistical_anomaly'] = 'statistical_anomaly'
    evidence_id: str
    metric_id: str
    dimension_id: str
    label: str
    value: Number
    median: Number
    direction: Literal['high', 'low']
    modified_z: float | None
    lower_fence: float | None
    upper_fence: float | None


class CalendarFinding(BaseModel):
    kind: Literal['calendar_coverage'] = 'calendar_coverage'
    evidence_id: str
    first_business_date: date
    last_business_date: date
    observed_days: int
    missing_dates: list[date]


class MonthlyPoint(BaseModel):
    month: str
    observed_days: int
    metrics: dict[str, Number | None]


class MonthlyFinding(BaseModel):
    kind: Literal['monthly_trend'] = 'monthly_trend'
    evidence_id: str
    months: list[MonthlyPoint]


class ProductMixFinding(BaseModel):
    kind: Literal['business_pattern_difference'] = 'business_pattern_difference'
    evidence_id: str
    store_id: str
    label: str
    value: Number
    share: float | None


class QualityFinding(BaseModel):
    kind: Literal['data_quality'] = 'data_quality'
    evidence_id: str
    invalid_rows: int


class LimitFinding(BaseModel):
    kind: Literal['partial_evidence'] = 'partial_evidence'
    evidence_id: str
    reason: Literal['result_limit'] = 'result_limit'


Finding = Annotated[
    ProfileFinding | MetricFinding | AnomalyFinding | CalendarFinding | MonthlyFinding
    | ProductMixFinding | QualityFinding | LimitFinding,
    Field(discriminator='kind'),
]


def numeric_value(value: object) -> Number | None:
    if type(value) is int or type(value) is float and math.isfinite(value):
        return value
    return None


def sum_values(values: list[Number]) -> Number:
    if all(type(value) is int for value in values):
        return sum(values)
    return math.fsum(values)


def _business_date(value: object) -> date | None:
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None
        if day.isoformat() == value:
            return day
    return None


def _profile(evidence: Evidence) -> list[Finding]:
    if len(evidence.rows) != 1 or evidence.columns != [
        'first_business_date', 'last_business_date', 'summary_row_count',
    ] or len(evidence.rows[0]) != 3:
        return [QualityFinding(evidence_id=evidence.evidence_id, invalid_rows=max(1, len(evidence.rows)))]
    first_cell, last_cell, count = evidence.rows[0]
    first, last = _business_date(first_cell), _business_date(last_cell)
    empty = type(count) is int and count == 0 and first_cell is None and last_cell is None
    covered = type(count) is int and count > 0 and first is not None and last is not None and first <= last
    if not (empty or covered):
        return [QualityFinding(evidence_id=evidence.evidence_id, invalid_rows=1)]
    return [ProfileFinding(
        evidence_id=evidence.evidence_id,
        first_business_date=first,
        last_business_date=last,
        summary_row_count=count,
    )]


def _metrics(
    evidence: Evidence,
    dimension_id: str,
    metric_id: str,
    points: dict[str, Number],
    partial: bool,
    config: AnalyzerConfig,
) -> tuple[MetricFinding, list[AnomalyFinding]]:
    values = list(points.values())
    count = len(values)
    minimum = config.min_daily_samples if dimension_id == 'stat_date' else config.min_group_samples
    center = median(values) if values else None
    mad = median(abs(value - center) for value in values) if center is not None else None
    q1, q3 = quantiles(values, n=4, method='inclusive')[::2] if count >= 2 else (None, None)
    iqr = q3 - q1 if q1 is not None and q3 is not None else None
    total = sum_values(values)
    status = 'partial_evidence' if partial else 'insufficient_sample' if count < minimum else 'sufficient'
    summary = MetricFinding(
        evidence_id=evidence.evidence_id,
        metric_id=metric_id,
        dimension_id=dimension_id,
        status=status,
        sample_size=count,
        minimum_sample=minimum,
        total=total if metric_id in ADDITIVE_METRICS and not partial else None,
        mean=total / count if count else None,
        median=center,
        mad=mad,
        q1=q1,
        q3=q3,
        iqr=iqr,
    )
    if status != 'sufficient' or center is None:
        return summary, []
    floor = max(config.min_absolute_deviation, abs(center) * config.min_relative_deviation)
    lower = q1 - config.iqr_multiplier * iqr if iqr else None
    upper = q3 + config.iqr_multiplier * iqr if iqr else None
    candidates = []
    for label, value in points.items():
        delta = value - center
        if abs(delta) < floor:
            continue
        # 0.67448975 is the normal distribution's 75th percentile.
        modified_z = 0.6744897501960817 * delta / mad if mad else None
        if modified_z is not None and abs(modified_z) <= config.mad_z_threshold:
            continue
        if lower is not None and upper is not None and lower <= value <= upper:
            continue
        candidates.append(AnomalyFinding(
            evidence_id=evidence.evidence_id,
            metric_id=metric_id,
            dimension_id=dimension_id,
            label=label,
            value=value,
            median=center,
            direction='high' if delta > 0 else 'low',
            modified_z=modified_z,
            lower_fence=lower,
            upper_fence=upper,
        ))
    return summary, candidates


def _daily_findings(
    evidence: Evidence,
    days: list[date],
    values: dict[str, dict[str, Number]],
    incomplete_metrics: set[str],
) -> list[Finding]:
    if not days:
        return []
    start, end = min(days), max(days)
    observed = set(days)
    missing_dates = [
        day for offset in range((end - start).days + 1)
        if (day := start + timedelta(days=offset)) not in observed
    ]
    months: dict[str, list[str]] = {}
    for day in sorted(days):
        months.setdefault(day.isoformat()[:7], []).append(day.isoformat())
    points = []
    for month, labels in months.items():
        metrics: dict[str, Number | None] = {}
        for metric in ADDITIVE_METRICS:
            if metric in values and metric not in incomplete_metrics:
                metrics[metric] = sum_values([values[metric][label] for label in labels])
        if 'average_order_value' in values and all(metric in metrics for metric in ADDITIVE_METRICS):
            amount, orders = metrics['sales_amount'], metrics['order_count']
            metrics['average_order_value'] = amount / orders if orders else None
        if metrics:
            points.append(MonthlyPoint(month=month, observed_days=len(labels), metrics=metrics))
    findings: list[Finding] = [CalendarFinding(
        evidence_id=evidence.evidence_id,
        first_business_date=start,
        last_business_date=end,
        observed_days=len(days),
        missing_dates=missing_dates,
    )]
    if points:
        findings.append(MonthlyFinding(evidence_id=evidence.evidence_id, months=points))
    return findings


def _result_values(
    evidence: Evidence, dimension_id: str,
) -> tuple[dict[str, dict[str, Number]], list[date], set[str], int, bool]:
    dimension_index = evidence.columns.index(dimension_id)
    indices = {metric: evidence.columns.index(metric) for metric in evidence.metric_ids}
    values: dict[str, dict[str, Number]] = {metric: {} for metric in indices}
    days: list[date] = []
    labels: set[str] = set()
    invalid_metrics: set[str] = set()
    invalid_rows = 0
    invalid_groups = False
    for row in evidence.rows:
        if len(row) != len(evidence.columns):
            invalid_rows += 1
            invalid_groups = True
            continue
        cell = row[dimension_index]
        day = _business_date(cell) if dimension_id == 'stat_date' else None
        time_range = evidence.time_range
        outside_scope = day is None or time_range is not None and not time_range.start <= day <= time_range.end
        valid_label = isinstance(cell, str) and bool(cell.strip()) or type(cell) is int
        if not valid_label or str(cell) in labels or dimension_id == 'stat_date' and outside_scope:
            invalid_rows += 1
            invalid_groups = True
            continue
        label = str(cell)
        labels.add(label)
        if day is not None:
            days.append(day)
        invalid_row = False
        for metric, index in indices.items():
            value = numeric_value(row[index])
            if metric == 'order_count' and value is not None and (value < 0 or value % 1 != 0):
                value = None
            if value is None:
                if metric != 'average_order_value' or row[index] is not None:
                    invalid_metrics.add(metric)
                    invalid_row = True
            else:
                values[metric][label] = value
        invalid_rows += int(invalid_row)
    return values, days, invalid_metrics, invalid_rows, invalid_groups


def analyze_evidence(evidence: Evidence, config: AnalyzerConfig) -> list[Finding]:
    if evidence.task_type == 'profile_data_range':
        return _profile(evidence)
    if evidence.possible_truncation:
        return [LimitFinding(evidence_id=evidence.evidence_id)]
    if len(evidence.dimension_ids) != 1:
        return [QualityFinding(evidence_id=evidence.evidence_id, invalid_rows=max(1, len(evidence.rows)))]
    dimension_id = evidence.dimension_ids[0]
    if evidence.columns != [dimension_id, *evidence.metric_ids]:
        return [QualityFinding(evidence_id=evidence.evidence_id, invalid_rows=max(1, len(evidence.rows)))]
    values, days, invalid_metrics, invalid_rows, invalid_groups = _result_values(evidence, dimension_id)
    findings: list[Finding] = []
    if invalid_rows:
        findings.append(QualityFinding(evidence_id=evidence.evidence_id, invalid_rows=invalid_rows))
    for metric, points in values.items():
        summary, candidates = _metrics(
            evidence, dimension_id, metric, points, invalid_groups or metric in invalid_metrics, config,
        )
        findings.extend([summary, *candidates])
    if dimension_id == 'stat_date' and not invalid_groups:
        findings.extend(_daily_findings(evidence, days, values, invalid_metrics))
    if (
        evidence.task_type == 'store_product_drilldown' and evidence.store_id
        and not invalid_groups and 'sales_amount' not in invalid_metrics
    ):
        product_sales = values.get('sales_amount', {})
        if product_sales:
            top_product = min(product_sales, key=lambda product: (-product_sales[product], product))
            total = sum_values(list(product_sales.values()))
            findings.append(ProductMixFinding(
                evidence_id=evidence.evidence_id,
                store_id=evidence.store_id,
                label=top_product,
                value=product_sales[top_product],
                share=product_sales[top_product] / total if total > 0 and min(product_sales.values()) >= 0 else None,
            ))
    return findings


def drilldown_candidates(findings: list[Finding]) -> list[str]:
    candidates = [
        finding for finding in findings
        if isinstance(finding, AnomalyFinding) and finding.dimension_id == 'store'
        and finding.metric_id == 'sales_amount'
    ]
    candidates.sort(key=lambda finding: (-abs(finding.value - finding.median), finding.label))
    return list(dict.fromkeys(finding.label for finding in candidates))
