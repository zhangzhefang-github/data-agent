"""Compose and verify Chinese reports against the stored query evidence."""

from math import isclose
from typing import Literal

from pydantic import BaseModel

from app.analysis.analyzer import (
    ADDITIVE_METRICS,
    AnalyzerConfig,
    AnomalyFinding,
    CalendarFinding,
    Finding,
    LimitFinding,
    MetricFinding,
    MonthlyFinding,
    ProductMixFinding,
    ProfileFinding,
    QualityFinding,
    analyze_evidence,
    numeric_value,
    sum_values,
)
from app.analysis.evidence import Evidence
from app.analysis.models import AnalysisFilter, AnalysisPlan, InterpretationFocus

_METRICS = {'sales_amount': '净销售额', 'order_count': '订单量', 'average_order_value': '客单价'}
_DIMENSIONS = {'stat_date': '业务日期', 'city': '城市', 'store': '门店', 'product': '产品'}
_MAX_DETAIL_LINES = 12


class ReportSection(BaseModel):
    key: str
    title: str
    text: str
    evidence_ids: list[str]


class AnalysisReport(BaseModel):
    text: str
    sections: list[ReportSection]
    evidence_ids: list[str]
    focus: InterpretationFocus | None = None


class ReportValidation(BaseModel):
    status: Literal['passed', 'failed']
    errors: list[str]

    @property
    def valid(self) -> bool:
        return self.status == 'passed'


def _section(key: str, title: str, lines: list[str], evidence_ids=()) -> ReportSection:
    return ReportSection(
        key=key, title=title, text='\n'.join(lines), evidence_ids=list(dict.fromkeys(evidence_ids)),
    )


def _display(value: int | float, metric: str) -> str:
    if metric == 'order_count' and value == int(value):
        return f'{int(value):,} 单'
    if type(value) is int:
        return f'{value:,}.00 元'
    return f'{value:,.2f} 元' if metric != 'order_count' else f'{value:,.2f} 单'


def _name(value: str) -> str:
    return ' '.join(value.split())


def _scope_section(plan: AnalysisPlan, profiles: list[ProfileFinding]) -> ReportSection:
    requested = plan.scope.time_range
    dates = f'{requested.start} 至 {requested.end}' if requested else '未设定业务日期'
    filters = '、'.join(
        f'{_DIMENSIONS[item.dimension_id]}「{_name(item.value)}」' for item in plan.scope.filters
    )
    lines = [f'销售主题空间。分析业务日期为 {dates}。']
    if filters:
        lines.append(f'筛选条件为 {filters}。')
    for profile in profiles:
        if profile.first_business_date is None:
            lines.append('销售 ADS 没有汇总记录。')
        else:
            lines.append(
                f'销售 ADS 可用业务日期为 {profile.first_business_date} 至 {profile.last_business_date}，'
                f'全表共 {profile.summary_row_count:,} 条汇总记录。'
            )
    return _section('scope', '分析范围', lines, (item.evidence_id for item in profiles))


def _summary_section(
    summaries: list[MetricFinding],
    calendars: list[CalendarFinding],
    monthly: list[MonthlyFinding],
    evidence: list[Evidence],
) -> ReportSection:
    lines = []
    refs = []
    stores = {item.evidence_id: item.store_id for item in evidence if item.store_id}
    for coverage in calendars:
        lines.append(
            f'查询返回 {coverage.observed_days} 个业务日，最早 {coverage.first_business_date}，'
            f'最晚 {coverage.last_business_date}。'
        )
        refs.append(coverage.evidence_id)
    for item in summaries:
        if item.status == 'partial_evidence' or not item.sample_size:
            continue
        metric = _METRICS[item.metric_id]
        dimension = _DIMENSIONS[item.dimension_id]
        scope = f'门店「{_name(stores[item.evidence_id])}」的' if item.evidence_id in stores else ''
        details = [f'{scope}{dimension}分组的{metric}中位数为 {_display(item.median, item.metric_id)}']
        if item.total is not None:
            details.append(f'合计 {_display(item.total, item.metric_id)}')
        if item.dimension_id == 'stat_date' and item.metric_id in ADDITIVE_METRICS:
            details.append(f'已覆盖业务日日均 {_display(item.mean, item.metric_id)}')
        lines.append('，'.join(details) + '。')
        refs.append(item.evidence_id)
    month_lines = []
    for item in monthly:
        for point in item.months:
            metrics = '，'.join(
                f'{_METRICS[metric]} {_display(value, metric)}'
                for metric, value in point.metrics.items() if value is not None
            )
            if metrics:
                month_lines.append(f'{point.month} 有 {point.observed_days} 个业务日的结果，{metrics}。')
                refs.append(item.evidence_id)
    lines.extend(month_lines[:_MAX_DETAIL_LINES])
    if len(month_lines) > _MAX_DETAIL_LINES:
        lines.append(f'其他 {len(month_lines) - _MAX_DETAIL_LINES} 项月度汇总见结构化发现。')
    return _section(
        'trend', '总体趋势', lines or ['本轮缺少可汇总的查询结果。'], refs,
    )


def _anomaly_section(
    candidates: list[AnomalyFinding], summaries: list[MetricFinding], evidence: list[Evidence],
) -> ReportSection:
    tested = [item for item in summaries if item.status == 'sufficient']
    candidate_count = len(candidates)
    if not tested:
        return _section('anomalies', '异常候选', ['证据不足，无法判断统计异常。'],
                        (item.evidence_id for item in summaries))
    lines = [f'在 {len(tested)} 组满足样本条件的指标分布中发现 {candidate_count} 项统计异常候选。']
    stores = {item.evidence_id: item.store_id for item in evidence if item.store_id}
    for item in candidates[:_MAX_DETAIL_LINES]:
        direction = '高于' if item.direction == 'high' else '低于'
        scope = f'门店「{_name(stores[item.evidence_id])}」的' if item.evidence_id in stores else ''
        lines.append(
            f'{scope}{_DIMENSIONS[item.dimension_id]}「{_name(item.label)}」的{_METRICS[item.metric_id]}'
            f'为 {_display(item.value, item.metric_id)}，{direction}分组中位数 '
            f'{_display(item.median, item.metric_id)}。'
        )
    if candidate_count > _MAX_DETAIL_LINES:
        lines.append(f'其他 {candidate_count - _MAX_DETAIL_LINES} 项候选见结构化发现和对应证据。')
    return _section('anomalies', '异常候选', lines, (item.evidence_id for item in tested))


def _performance_section(
    candidates: list[AnomalyFinding],
    product_mixes: list[ProductMixFinding],
) -> ReportSection:
    lines = []
    for item in product_mixes:
        proportion = f'，占该门店净销售额的 {item.share:.1%}' if item.share is not None else ''
        lines.append(
            f'门店「{_name(item.store_id)}」下钻显示，净销售额最高的产品包括「{_name(item.label)}」，'
            f'金额为 {_display(item.value, "sales_amount")}{proportion}。'
        )
    if candidates:
        lines.append('候选反映与当前分布的偏离，销售 ADS 提供的证据不足以判断经营表现异常。')
    if not lines:
        lines.append('现有证据无法判断经营表现异常。')
    return _section(
        'performance', '异常表现', lines, (item.evidence_id for item in [*candidates, *product_mixes]),
    )


def _limits_section(findings: list[Finding], incomplete: list[str]) -> ReportSection:
    lines = [
        '销售 ADS 按业务日期、门店和产品汇总。交易时点、交易价格、商品件数、加盟商和采集时段不在当前数据范围。'
    ]
    refs = []
    for item in findings:
        if isinstance(item, CalendarFinding) and item.missing_dates:
            sample = '、'.join(str(day) for day in item.missing_dates[:_MAX_DETAIL_LINES])
            suffix = '等' if len(item.missing_dates) > _MAX_DETAIL_LINES else ''
            lines.append(
                f'已返回业务日期的首尾之间有 {len(item.missing_dates)} 个日期没有分组，'
                f'包括 {sample}{suffix}。日期缺口需要结合营业日历及采集记录核查。'
            )
        elif isinstance(item, MetricFinding) and item.status == 'insufficient_sample':
            lines.append(
                f'{_DIMENSIONS[item.dimension_id]}的{_METRICS[item.metric_id]}'
                f'有 {item.sample_size} 个可用样本，异常检测至少需要 {item.minimum_sample} 个。'
            )
        elif isinstance(item, QualityFinding):
            lines.append(f'有 {item.invalid_rows} 条结果的标识或指标不可用，已限制相关统计。')
        elif isinstance(item, LimitFinding):
            lines.append('查询结果达到行数上限，无法确认分组完整性，已停止根据该结果判定异常。')
        else:
            continue
        refs.append(item.evidence_id)
    if incomplete:
        lines.append('部分探索任务未完成。详情见任务状态和停止原因。')
    return _section('limits', '数据限制', lines, refs)


def available_interpretation_focuses(findings: list[Finding]) -> list[InterpretationFocus]:
    choices = []
    if any(
        isinstance(item, (LimitFinding, QualityFinding))
        or isinstance(item, CalendarFinding) and item.missing_dates
        or isinstance(item, MetricFinding) and item.status == 'insufficient_sample'
        for item in findings
    ):
        choices.append(InterpretationFocus.COVERAGE)
    if any(isinstance(item, ProductMixFinding) for item in findings):
        choices.append(InterpretationFocus.STORE_PRODUCT)
    if any(
        isinstance(item, MetricFinding) and item.dimension_id == 'stat_date'
        and item.sample_size and item.status != 'partial_evidence'
        for item in findings
    ):
        choices.append(InterpretationFocus.TREND)
    return choices


def compose_report(
    plan: AnalysisPlan,
    evidence: list[Evidence],
    findings: list[Finding],
    incomplete: list[str],
    *,
    focus: InterpretationFocus | None = None,
) -> AnalysisReport:
    profiles = [item for item in findings if isinstance(item, ProfileFinding)]
    summaries = [item for item in findings if isinstance(item, MetricFinding)]
    calendars = [item for item in findings if isinstance(item, CalendarFinding)]
    monthly = [item for item in findings if isinstance(item, MonthlyFinding)]
    candidates = [item for item in findings if isinstance(item, AnomalyFinding)]
    product_mixes = [item for item in findings if isinstance(item, ProductMixFinding)]
    all_ids = [item.evidence_id for item in evidence]
    selected_focus = focus if focus in available_interpretation_focuses(findings) else None
    follow_up = {
        InterpretationFocus.COVERAGE: '核对所选时间范围、营业日历和 ADS 数据覆盖，补足可比较的样本。',
        InterpretationFocus.STORE_PRODUCT: '核对候选门店的产品结构，并与同一业务日期内的同类门店比较。',
        InterpretationFocus.TREND: '对齐营业日历和日期覆盖后复核日趋势，再核对同期活动记录。',
    }.get(
        selected_focus,
        '按相同业务日期比较同类门店，核对产品结构及活动安排。对日期缺口可核对源系统批次和营业记录。',
    )
    sections = [
        _scope_section(plan, profiles),
        _summary_section(summaries, calendars, monthly, evidence),
        _anomaly_section(candidates, summaries, evidence),
        _performance_section(candidates, product_mixes),
        _section(
            'evidence', '支撑证据',
            ['各次成功查询的结果、数据来源和 SQL 可在证据列表中查阅。']
            if evidence else ['本轮没有成功查询证据。'],
            all_ids,
        ),
        _section(
            'uncertainty', '可能原因及不确定性',
            ['建议结合促销或营业日历、门店规模和产品结构解释观测差异。'
             '当前查询未能验证这些解释。']
            if candidates else ['现有证据不足以归因。'],
            (item.evidence_id for item in candidates),
        ),
        _section('follow_up', '后续核查建议', [follow_up]),
        _limits_section(findings, incomplete),
    ]
    focused_section = {
        InterpretationFocus.COVERAGE: 'limits',
        InterpretationFocus.STORE_PRODUCT: 'performance',
        InterpretationFocus.TREND: 'trend',
    }.get(selected_focus)
    if focused_section:
        index = next(index for index, section in enumerate(sections) if section.key == focused_section)
        sections.insert(1, sections.pop(index))
    return AnalysisReport(
        sections=sections, evidence_ids=all_ids, focus=selected_focus,
        text='\n\n'.join(f'{section.title}\n{section.text}' for section in sections),
    )


def _same_value(left: int | float, right: int | float, metric: str, config: AnalyzerConfig) -> bool:
    if metric == 'order_count' or type(left) is int and type(right) is int:
        return left == right
    if metric == 'average_order_value':
        return isclose(left, right, abs_tol=config.ratio_tolerance, rel_tol=0)
    return isclose(left, right, abs_tol=config.amount_tolerance, rel_tol=0)


def _scope_key(evidence: Evidence, *, filters: list[AnalysisFilter] | None = None) -> tuple:
    scope_filters = evidence.filters if filters is None else filters
    time_range = evidence.time_range
    return (
        evidence.source, evidence.snapshot_id, evidence.dataset_id,
        time_range.start if time_range else None, time_range.end if time_range else None,
        frozenset((item.dimension_id, item.value) for item in scope_filters),
    )


def _reconcile(
    plan: AnalysisPlan, evidence: list[Evidence], findings: list[Finding], config: AnalyzerConfig,
) -> list[str]:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    totals = {
        (item.evidence_id, item.metric_id): item.total for item in findings
        if isinstance(item, MetricFinding) and item.total is not None
    }
    errors = []
    store_breakdowns = [
        item for item in evidence
        if item.dimension_ids == ['store'] and not item.possible_truncation
    ]
    scope_totals: dict[tuple, tuple[str, int | float]] = {}
    for (evidence_id, metric), total in totals.items():
        item = evidence_by_id[evidence_id]
        key = (*_scope_key(item), metric)
        reference_id, reference_total = scope_totals.setdefault(key, (evidence_id, total))
        if not _same_value(total, reference_total, metric, config):
            errors.append(f'{evidence_id} 与 {reference_id} 的同口径{_METRICS[metric]}合计不一致。')
        if not item.store_id or item.task_type != 'store_product_drilldown':
            continue
        parent_scope = _scope_key(item, filters=plan.scope.filters)
        for parent in store_breakdowns:
            if _scope_key(parent) != parent_scope or (parent.evidence_id, metric) not in totals:
                continue
            value_index = parent.columns.index(metric)
            label_index = parent.columns.index('store')
            parent_values = [
                numeric_value(row[value_index]) for row in parent.rows if str(row[label_index]) == item.store_id
            ]
            if len(parent_values) != 1 or parent_values[0] is None or not _same_value(
                total, parent_values[0], metric, config,
            ):
                errors.append(f'{evidence_id} 的产品明细合计与门店证据 {parent.evidence_id} 不一致。')
    for item in findings:
        if not isinstance(item, MonthlyFinding):
            continue
        for metric in ADDITIVE_METRICS:
            total = totals.get((item.evidence_id, metric))
            if total is not None and not _same_value(
                total, sum_values([point.metrics[metric] for point in item.months]), metric, config,
            ):
                errors.append(f'{item.evidence_id} 的月度{_METRICS[metric]}合计与日明细不一致。')
    for item in evidence:
        if not all(metric in item.columns for metric in _METRICS):
            continue
        amount_index, count_index, ratio_index = [item.columns.index(metric) for metric in _METRICS]
        for row in item.rows:
            if len(row) != len(item.columns):
                continue
            amount = numeric_value(row[amount_index])
            count = numeric_value(row[count_index])
            ratio = numeric_value(row[ratio_index])
            if amount is None or count is None:
                continue
            if (not count and row[ratio_index] is not None) or (
                count and (ratio is None or not _same_value(ratio, amount / count, 'average_order_value', config))
            ):
                errors.append(f'{item.evidence_id} 的客单价与净销售额和订单量不一致。')
                break
    return errors


def validate_report(
    report: AnalysisReport,
    plan: AnalysisPlan,
    evidence: list[Evidence],
    findings: list[Finding],
    incomplete: list[str],
    config: AnalyzerConfig,
    *,
    focus: InterpretationFocus | None = None,
) -> ReportValidation:
    errors = []
    evidence_ids = {item.evidence_id for item in evidence}
    if len(evidence_ids) != len(evidence) or any(
        item.analysis_id != plan.analysis_id or item.status != 'COMPLETED' for item in evidence
    ):
        errors.append('证据标识重复或未关联本轮的成功查询。')
    if any(not item.sql.strip() or item.row_count != len(item.rows) for item in evidence):
        errors.append('证据缺少 SQL 或结果行数不一致。')
    citations = set(report.evidence_ids)
    for section in report.sections:
        citations.update(section.evidence_ids)
    if not citations <= evidence_ids or any(item.evidence_id not in evidence_ids for item in findings):
        errors.append('报告或统计发现引用了不存在的证据。')
    verified_findings = [item for entry in evidence for item in analyze_evidence(entry, config)]
    if findings != verified_findings:
        errors.append('报告使用的统计发现与查询结果不一致。')
    if focus is not None and focus not in available_interpretation_focuses(verified_findings):
        errors.append('报告重点缺少已验证的证据。')
    if report != compose_report(plan, evidence, verified_findings, incomplete, focus=focus):
        errors.append('报告结论、分类数量或统计数字与证据生成的报告不一致。')
    errors.extend(_reconcile(plan, evidence, verified_findings, config))
    return ReportValidation(status='failed' if errors else 'passed', errors=errors)
