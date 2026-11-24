from datetime import date
import json
from pathlib import Path
import sqlite3

import pytest

from app.analysis.analyzer import (
    AnalyzerConfig,
    AnomalyFinding,
    CalendarFinding,
    MetricFinding,
    MonthlyFinding,
    ProductMixFinding,
    analyze_evidence,
    drilldown_candidates,
)
from app.analysis.evidence import Evidence
from app.analysis.models import AnalysisFilter, AnalysisPlan, AnalysisScope, AnalysisTask, InterpretationFocus
from app.analysis.query_executor import compile_task_query, execute_task_query
from app.analysis.report import available_interpretation_focuses, compose_report, validate_report
from app.query import DataSource, TimeRange, load_catalog


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / 'backend/app/evaluation/analysis_golden.json').read_text())
CONFIG = AnalyzerConfig()
METRICS = ['sales_amount', 'order_count', 'average_order_value']
TASKS = [
    ('range', 'profile_data_range', []),
    ('daily', 'daily_trend', ['stat_date']),
    ('stores', 'dimension_breakdown', ['store']),
    ('cities', 'dimension_breakdown', ['city']),
    ('products', 'dimension_breakdown', ['product']),
]


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'analysis.sqlite'
    with sqlite3.connect(path) as db:
        db.execute(
            'CREATE TABLE sales_summary (stat_date TEXT, city TEXT, store_id TEXT, product TEXT, '
            'sales_amount INTEGER, order_count INTEGER)'
        )
        db.executemany('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', GOLDEN['rows'])
    return DataSource(engine='sqlite', location=str(path))


def _task(identifier, task_type, dimensions, metrics=None, requires=()):
    return AnalysisTask(
        task_id=identifier,
        task_type=task_type,
        title='查询销售 ADS',
        objective='计算汇总指标',
        metric_ids=metrics if metrics is not None else METRICS if dimensions else [],
        dimension_ids=dimensions,
        depends_on=list(requires),
        rationale='观察分布',
    )


def _plan(case, tasks):
    return AnalysisPlan(
        analysis_id='synthetic',
        goal='分析销售额',
        scope=AnalysisScope(
            space_id='sales',
            time_range=TimeRange.model_validate(case['time_range']),
            filters=[AnalysisFilter(dimension_id='store', value=store) for store in case['store_ids']],
        ),
        hypotheses=[],
        tasks=tasks,
        dependencies=[],
        stop_conditions=[],
    )


def _execute(task, plan, source, *, store=None, limit=500):
    catalog = load_catalog(ROOT, 'sales')
    query = compile_task_query(task, plan, catalog, source, limit, store_value=store)
    return execute_task_query(
        query,
        task=task,
        plan=plan,
        source=source,
        snapshot_id='sales-test',
        store_id=store,
        source_label='合成测试数据',
    )


def _findings(evidence):
    return [finding for item in evidence for finding in analyze_evidence(item, CONFIG)]


def _result(task_type, dimension, values):
    return Evidence(
        evidence_id='synthetic:one',
        analysis_id='synthetic',
        task_id='one',
        task_type=task_type,
        metric_ids=['sales_amount'],
        dimension_ids=[dimension],
        time_range=TimeRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
        sql='SELECT store_id, SUM(sales_amount) FROM sales_summary GROUP BY store_id',
        columns=[dimension, 'sales_amount'],
        rows=[[f'SYN-{index}', value] for index, value in enumerate(values)],
        row_count=len(values),
        elapsed_ms=1,
        source='合成测试数据',
        snapshot_id='sales-test',
        dataset_id='sales_summary',
    )


@pytest.mark.parametrize('case', GOLDEN['cases'], ids=lambda case: case['id'])
def test_golden_cases_compute_findings_from_real_sql(case, source):
    tasks = [_task(*item) for item in TASKS]
    plan = _plan(case, tasks)
    evidence = [_execute(task, plan, source) for task in tasks]
    findings = _findings(evidence)
    expected = case['expected']
    calendar = next(item for item in findings if isinstance(item, CalendarFinding))
    assert calendar.observed_days == expected['observed_days']
    assert [day.isoformat() for day in calendar.missing_dates] == expected['missing_dates']
    trend = next(item for item in findings if isinstance(item, MonthlyFinding))
    assert sum(point.metrics['sales_amount'] for point in trend.months) == expected['sales_amount_total']
    assert sum(point.metrics['order_count'] for point in trend.months) == expected['order_count_total']
    for point in trend.months:
        assert point.metrics['average_order_value'] == point.metrics['sales_amount'] / point.metrics['order_count']

    def sales_summary(dimension):
        return next(
            item for item in findings if isinstance(item, MetricFinding) and item.metric_id == 'sales_amount'
            and item.dimension_id == dimension
        )

    assert sales_summary('stat_date').status == expected['daily_status']
    assert sales_summary('store').status == expected['store_status']
    assert sales_summary('stat_date').mean == expected['sales_amount_total'] / expected['observed_days']
    assert sales_summary('store').total == expected['sales_amount_total']
    assert drilldown_candidates(findings) == expected['store_sales_candidates']
    assert [
        item.label for item in findings if isinstance(item, AnomalyFinding) and item.dimension_id == 'stat_date'
        and item.metric_id == 'sales_amount'
    ] == expected['daily_sales_candidates']

    for store in drilldown_candidates(findings):
        drilldown = _task('drilldown', 'store_product_drilldown', ['product'], requires=['stores'])
        evidence.append(_execute(drilldown, plan, source, store=store))
        findings.extend(analyze_evidence(evidence[-1], CONFIG))
        mix = next(item for item in findings if isinstance(item, ProductMixFinding))
        assert mix.store_id == store
        assert mix.label == '茶'
        assert mix.share > 0.75
    report = compose_report(plan, evidence, findings, [])
    assert validate_report(report, plan, evidence, findings, [], CONFIG).valid
    assert '分析范围' in report.text
    assert ('无法判断统计异常' in report.text) == (case['id'] == 'insufficient_sample')


def test_product_drilldown_summary_names_its_store(source):
    tasks = [_task('products', 'dimension_breakdown', ['product']),
             _task('drilldown', 'store_product_drilldown', ['product'], requires=['stores'])]
    plan = _plan(GOLDEN['cases'][0], tasks)
    evidence = [_execute(tasks[0], plan, source), _execute(tasks[1], plan, source, store='SYN-06')]
    report = compose_report(plan, evidence, _findings(evidence), [])
    assert '门店「SYN-06」的产品分组' in report.text


def test_mad_iqr_thresholds_and_constant_baseline():
    evidence = _result('dimension_breakdown', 'store', [8, 9, 10, 10, 11, 12, 30])
    findings = analyze_evidence(evidence, CONFIG)
    summary = next(item for item in findings if isinstance(item, MetricFinding))
    assert summary.median == 10
    assert summary.mad == 1
    assert (summary.q1, summary.q3, summary.iqr) == (9.5, 11.5, 2)
    candidate, = [item for item in findings if isinstance(item, AnomalyFinding)]
    assert candidate.modified_z == pytest.approx(13.489795)
    assert (candidate.lower_fence, candidate.upper_fence) == (6.5, 14.5)
    assert candidate.label == 'SYN-6'
    assert candidate.direction == 'high'
    strict = AnalyzerConfig(mad_z_threshold=15)
    assert not drilldown_candidates(analyze_evidence(evidence, strict))
    strict = AnalyzerConfig(iqr_multiplier=10)
    assert not drilldown_candidates(analyze_evidence(evidence, strict))
    flat = _result('dimension_breakdown', 'store', [8, 8, 8, 8, 8, 1])
    anomaly, = [item for item in analyze_evidence(flat, CONFIG) if isinstance(item, AnomalyFinding)]
    assert anomaly.direction == 'low'
    assert anomaly.modified_z is None
    assert anomaly.upper_fence is None
    strict = AnalyzerConfig(min_relative_deviation=1)
    assert not drilldown_candidates(analyze_evidence(flat, strict))


def test_limit_hit_suppresses_statistics_and_drilldowns(source):
    task = _task('stores', 'dimension_breakdown', ['store'])
    plan = _plan(GOLDEN['cases'][0], [task])
    item = _execute(task, plan, source, limit=5)
    assert item.row_count == 5
    findings = analyze_evidence(item, CONFIG)
    assert [entry.kind for entry in findings] == ['partial_evidence']
    assert drilldown_candidates(findings) == []
    report = compose_report(plan, [item], findings, [])
    assert '达到行数上限' in report.text
    assert validate_report(report, plan, [item], findings, [], CONFIG).valid


def test_zero_order_value_and_date_gaps_use_source_evidence():
    evidence = _result('daily_trend', 'stat_date', [])
    evidence.columns.extend(['order_count', 'average_order_value'])
    evidence.metric_ids.extend(['order_count', 'average_order_value'])
    evidence.rows = [['2026-01-30', 0, 0, None], ['2026-02-01', 60, 4, 15.0]]
    evidence.time_range = TimeRange(start=date(2026, 1, 1), end=date(2026, 2, 28))
    evidence.row_count = 2
    findings = analyze_evidence(evidence, CONFIG)
    monthly = next(item for item in findings if isinstance(item, MonthlyFinding))
    assert monthly.months[0].metrics == {'sales_amount': 0, 'order_count': 0, 'average_order_value': None}
    assert monthly.months[1].metrics == {'sales_amount': 60, 'order_count': 4, 'average_order_value': 15}
    calendar = next(item for item in findings if isinstance(item, CalendarFinding))
    assert calendar.missing_dates == [date(2026, 1, 31)]
    assert not any(isinstance(item, AnomalyFinding) for item in findings)


def test_report_detects_unverified_claims_wrong_numbers_and_missing_evidence(source):
    task = _task('stores', 'dimension_breakdown', ['store'])
    plan = _plan(GOLDEN['cases'][0], [task])
    evidence = [_execute(task, plan, source)]
    findings = _findings(evidence)
    report = compose_report(plan, evidence, findings, [])
    assert validate_report(report, plan, evidence, findings, [], CONFIG).valid

    for corrupted in [
        report.model_copy(update={'evidence_ids': ['missing']}),
        report.model_copy(update={'text': report.text + '\n门店停业已得到证实。'}),
        report.model_copy(update={'text': report.text.replace('项统计异常候选', '百项统计异常候选')}),
    ]:
        assert not validate_report(corrupted, plan, evidence, findings, [], CONFIG).valid

    candidate = next(item for item in findings if isinstance(item, AnomalyFinding))
    candidate.value += 100
    report = compose_report(plan, evidence, findings, [])
    assert not validate_report(report, plan, evidence, findings, [], CONFIG).valid


def test_report_detects_inconsistent_cross_query_totals_and_ratios(source):
    tasks = [_task('stores', 'dimension_breakdown', ['store']), _task('daily', 'daily_trend', ['stat_date'])]
    plan = _plan(GOLDEN['cases'][0], tasks)
    evidence = [_execute(task, plan, source) for task in tasks]
    evidence[1].rows[0][1] += 100
    findings = _findings(evidence)
    report = compose_report(plan, evidence, findings, [])
    validation = validate_report(report, plan, evidence, findings, [], CONFIG)
    assert not validation.valid
    assert any('同口径净销售额合计不一致' in error for error in validation.errors)
    assert any('客单价与净销售额和订单量不一致' in error for error in validation.errors)


def test_report_checks_drilldown_against_parent_with_multiple_store_filters(source):
    tasks = [
        _task('stores', 'dimension_breakdown', ['store'], ['sales_amount']),
        _task('drilldown', 'store_product_drilldown', ['product'], ['sales_amount'], requires=['stores']),
    ]
    plan = _plan(GOLDEN['cases'][2], tasks)
    evidence = [_execute(tasks[0], plan, source), _execute(tasks[1], plan, source, store='SYN-01')]
    findings = _findings(evidence)
    assert validate_report(compose_report(plan, evidence, findings, []), plan, evidence, findings, [], CONFIG).valid
    evidence[1].rows[0][1] += 100
    findings = _findings(evidence)
    validation = validate_report(compose_report(plan, evidence, findings, []), plan, evidence, findings, [], CONFIG)
    assert not validation.valid
    assert any('产品明细合计与门店证据' in error for error in validation.errors)


def test_report_focus_is_limited_to_grounded_sections(source):
    task = _task('daily', 'daily_trend', ['stat_date'])
    plan = _plan(GOLDEN['cases'][0], [task])
    evidence = [_execute(task, plan, source)]
    findings = _findings(evidence)
    assert available_interpretation_focuses(findings) == [
        InterpretationFocus.COVERAGE, InterpretationFocus.TREND,
    ]

    focus = InterpretationFocus.COVERAGE
    report = compose_report(plan, evidence, findings, [], focus=focus)
    assert report.focus == focus
    assert report.sections[1].key == 'limits'
    assert validate_report(report, plan, evidence, findings, [], CONFIG, focus=focus).valid

    unsupported = InterpretationFocus.STORE_PRODUCT
    fallback = compose_report(plan, evidence, findings, [], focus=unsupported)
    assert fallback.focus is None
    assert not validate_report(fallback, plan, evidence, findings, [], CONFIG, focus=unsupported).valid
