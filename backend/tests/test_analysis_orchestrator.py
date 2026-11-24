import asyncio
from datetime import date
import json
from pathlib import Path
import sqlite3
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agents import AgentOutputSchema
import duckdb
import pytest

from app.analysis import orchestrator, planner
from app.analysis.analyzer import AnomalyFinding
from app.analysis.models import (
    AnalysisBudget, AnalysisDependency, AnalysisFilter, AnalysisInterpretation, AnalysisPlan, AnalysisPlanDraft,
    AnalysisScope, AnalysisTask, InterpretationFocus, TaskStatus,
)
from app.analysis.plan_validator import PlanRejected, validate_plan
from app.analysis.query_executor import compile_task_query
from app.query import DataSource, TimeRange, fetch_rows, load_catalog


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / 'backend/app/evaluation/analysis_golden.json').read_text())
TODAY = date.fromisoformat(GOLDEN['clock'])
METRICS = ['sales_amount', 'order_count', 'average_order_value']
QUESTION = '请问销售额是否有异常？'


def make_task(task_id, task_type, dimensions, dependencies=(), metrics=METRICS):
    return AnalysisTask(
        task_id=task_id, task_type=task_type, title=task_id, objective='查询授权数据',
        metric_ids=metrics, dimension_ids=dimensions, depends_on=list(dependencies), rationale='提供分析证据',
    )


def make_plan(tasks=None, *, scope=None):
    if tasks is None:
        tasks = [
            make_task('drilldown', 'store_product_drilldown', ['product'], ['stores']),
            make_task('stores', 'dimension_breakdown', ['store'], ['trend']),
            make_task('profile', 'profile_data_range', [], metrics=[]),
            make_task('trend', 'daily_trend', ['stat_date'], ['profile']),
            make_task('cities', 'dimension_breakdown', ['city']),
            make_task('products', 'dimension_breakdown', ['product']),
        ]
    return AnalysisPlan(
        analysis_id='orchestrator-test', goal=QUESTION, scope=scope or AnalysisScope(space_id='sales'),
        tasks=tasks, hypotheses=[],
        dependencies=[
            AnalysisDependency(task_id=task.task_id, depends_on=parent)
            for task in tasks for parent in task.depends_on
        ],
        stop_conditions=['达到执行预算'],
    )


@pytest.fixture
def catalog():
    return load_catalog(ROOT, 'sales')


@pytest.fixture(params=['text', 'integer'])
def golden_source(tmp_path, request):
    path = tmp_path / 'synthetic-sales.db'
    rows = GOLDEN['rows']
    if request.param == 'integer':
        rows = [[*row[:2], int(row[2].split('-')[1]), *row[3:]] for row in rows]
    store_type = 'INTEGER' if request.param == 'integer' else 'TEXT'
    with sqlite3.connect(path) as db:
        db.execute(
            f'CREATE TABLE sales_summary (stat_date TEXT, city TEXT, store_id {store_type}, product TEXT,'
            'sales_amount INTEGER, order_count INTEGER)'
        )
        db.executemany('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', rows)
    return DataSource(engine='sqlite', location=str(path))


def run(catalog, source, plan=None, *, question=QUESTION, **options):
    return asyncio.run(orchestrator.run_analysis(
        question=question, catalog=catalog, source=source, today=TODAY,
        planner=AsyncMock(return_value=plan or make_plan()), **options,
    ))


def test_sales_anomaly_requires_profile_trend_and_breakdown(catalog):
    daily = make_task('daily', 'daily_trend', ['stat_date'])
    with pytest.raises(PlanRejected, match='必需'):
        validate_plan(make_plan([daily]), catalog, TODAY, AnalysisBudget(), question=QUESTION)


def test_sales_anomaly_requires_sales_amount_in_trend_and_breakdown(catalog):
    profile = make_task('profile', 'profile_data_range', [], metrics=[])
    daily = make_task('daily', 'daily_trend', ['stat_date'], metrics=['order_count'])
    city = make_task('city', 'dimension_breakdown', ['city'], metrics=['order_count'])
    with pytest.raises(PlanRejected, match='销售额'):
        validate_plan(make_plan([profile, daily, city]), catalog, TODAY, AnalysisBudget(), question=QUESTION)


def test_planner_uses_installed_sdk_strict_plan_schema(monkeypatch, catalog):
    expected = make_plan()
    draft = AnalysisPlanDraft.model_validate(expected.model_dump(exclude={'dependencies'}))
    mocked_run = AsyncMock(return_value=SimpleNamespace(final_output=draft))
    monkeypatch.setattr(planner.Runner, 'run', mocked_run)
    time_range = TimeRange(start=date(2026, 10, 23), end=date(2026, 11, 8))
    actual = asyncio.run(planner.plan_analysis(QUESTION, catalog, TODAY, time_range))
    assert actual.goal == expected.goal
    assert actual.dependencies == expected.dependencies
    agent, model_input = mocked_run.await_args.args
    assert agent.output_type is AnalysisPlanDraft and not agent.tools and not agent.handoffs
    schema = AgentOutputSchema(agent.output_type).json_schema()
    assert {'scope', 'stop_conditions'} <= set(schema['required'])
    assert 'dependencies' not in schema['properties']
    assert not schema['$defs']['AnalysisTask']['additionalProperties']
    data = json.loads(model_input)
    assert data['requested_time_range'] == time_range.model_dump(mode='json')
    assert data['business_date'] == TODAY.isoformat()
    assert 'models' not in data and 'column' not in data['dimensions']['store']


@pytest.mark.parametrize('mutations', [{'metric_ids': ['transaction_price']}, {'dimension_ids': ['franchise_id']}])
def test_rejects_unauthorized_plan_assets(catalog, mutations):
    trend = make_task('daily', 'daily_trend', ['stat_date']).model_copy(update=mutations)
    with pytest.raises(PlanRejected, match='未授权'):
        validate_plan(make_plan([trend]), catalog, TODAY, AnalysisBudget(), question=QUESTION)


def test_omitted_dependency_list_still_runs(catalog, golden_source):
    tasks = [
        make_task('profile', 'profile_data_range', [], metrics=[]),
        make_task('cities', 'dimension_breakdown', ['city'], ['profile'], metrics=['sales_amount']),
    ]
    plan = make_plan(tasks).model_copy(update={'dependencies': []})
    result = run(catalog, golden_source, plan, question='查询今年各城市销售额')
    assert result.reason != '分析计划的依赖清单与任务依赖不一致。'
    assert result.status == 'completed'


def test_conflicting_dependency_list_is_still_rejected(catalog, golden_source):
    tasks = [
        make_task('profile', 'profile_data_range', [], metrics=[]),
        make_task('cities', 'dimension_breakdown', ['city'], ['profile'], metrics=['sales_amount']),
    ]
    plan = make_plan(tasks).model_copy(update={
        'dependencies': [AnalysisDependency(task_id='cities', depends_on='profile-other')],
    })
    result = run(catalog, golden_source, plan, question='查询今年各城市销售额')
    assert result.reason == '分析计划的依赖清单与任务依赖不一致。'
    assert result.status == 'failed'


def test_rejects_cyclic_and_redundant_analysis_queries(catalog):
    daily = make_task('daily', 'daily_trend', ['stat_date'], ['store'])
    store = make_task('store', 'dimension_breakdown', ['store'], ['daily'])
    with pytest.raises(PlanRejected, match='循环'):
        validate_plan(make_plan([daily, store]), catalog, TODAY, AnalysisBudget(), question=QUESTION)
    duplicate = make_task('duplicate', 'daily_trend', ['stat_date'], metrics=['order_count'])
    with pytest.raises(PlanRejected, match='重复查询'):
        validate_plan(
            make_plan([daily.model_copy(update={'depends_on': []}), duplicate]),
            catalog, TODAY, AnalysisBudget(), question=QUESTION,
        )


def test_rejects_dropped_or_modified_dates(catalog):
    requested = TimeRange(start=date(2026, 11, 1), end=date(2026, 11, 8))
    with pytest.raises(PlanRejected, match='起止日期'):
        validate_plan(make_plan(), catalog, TODAY, AnalysisBudget(), question='本月销售额是否有异常？')
    scope = AnalysisScope(space_id='sales', time_range=TimeRange(start=date(2026, 1, 1), end=TODAY))
    with pytest.raises(PlanRejected, match='改变'):
        validate_plan(
            make_plan(scope=scope), catalog, TODAY, AnalysisBudget(), question=QUESTION, requested_time_range=requested,
        )
    scope = AnalysisScope(space_id='sales', time_range=requested)
    assert validate_plan(
        make_plan(scope=scope), catalog, TODAY, AnalysisBudget(),
        question='2026-11-01 至 2026-11-08 销售额是否有异常？',
    ).plan.scope.time_range == requested
    with pytest.raises(PlanRejected, match='起止日期'):
        validate_plan(make_plan(scope=scope), catalog, TODAY, AnalysisBudget(), question=QUESTION)


@pytest.mark.parametrize('question', [
    '2026-11-08 当天销售额是否有异常？', '本月销售额是否有异常？',
    '上个月销售额是否有异常？', '最近 7 天销售额是否有异常？',
])
def test_model_cannot_expand_requested_dates(catalog, question):
    scope = AnalysisScope(space_id='sales', time_range=TimeRange(start=date(2026, 1, 1), end=TODAY))
    with pytest.raises(PlanRejected):
        validate_plan(make_plan(scope=scope), catalog, TODAY, AnalysisBudget(), question=question)


@pytest.mark.parametrize(('question', 'start', 'end'), [
    ('2026-11-08 当天销售额是否有异常？', '2026-11-08', '2026-11-08'),
    ('2026-11-01 至 2026-11-08 销售额是否有异常？', '2026-11-01', '2026-11-08'),
    ('本月销售额是否有异常？', '2026-11-01', '2026-11-24'),
    ('上个月销售额是否有异常？', '2026-10-01', '2026-10-31'),
    ('最近 7 天销售额是否有异常？', '2026-11-18', '2026-11-24'),
    ('今年销售额是否有异常？', '2026-01-01', '2026-11-24'),
])
def test_requested_dates_are_passed_to_planner(catalog, golden_source, question, start, end):
    calls = []
    async def capture(question, catalog, today, span):
        calls.append(span)
        return make_plan(scope=AnalysisScope(space_id='sales', time_range=span))
    result = asyncio.run(orchestrator.run_analysis(question=question, catalog=catalog,
        source=golden_source, today=TODAY, planner=capture))
    assert calls[0] == TimeRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
    assert result.plan.scope.time_range == calls[0]


@pytest.mark.parametrize('wrong_filters', [[], [AnalysisFilter(dimension_id='product', value='北京')]])
def test_beijing_sales_never_runs_with_dropped_or_wrong_filter(catalog, wrong_filters):
    scope = AnalysisScope(space_id='sales', filters=wrong_filters)
    with pytest.raises(PlanRejected, match='筛选'):
        validate_plan(make_plan(scope=scope), catalog, TODAY, AnalysisBudget(), question='北京销售额是否有异常？')


def test_direct_sql_binds_user_values_and_rejects_invalid_config(catalog):
    source = DataSource(engine='sqlite', location='unused')
    task = make_task('daily', 'daily_trend', ['stat_date'])
    value = "O'Reilly 茶"
    scope = AnalysisScope(space_id='sales', filters=[AnalysisFilter(dimension_id='product', value=value)])
    plan = validate_plan(
        make_plan([task], scope=scope), catalog, TODAY, AnalysisBudget(), question=f'查询产品「{value}」的销售额趋势',
    ).plan
    query = compile_task_query(task, plan, catalog, source, 500)
    assert '"product" = ?' in query.sql
    assert value not in query.sql and value in query.parameters
    catalog['models']['sales_summary']['table'] = 'invalid table'
    with pytest.raises(ValueError, match='标识符'):
        compile_task_query(task, plan, catalog, source, 500)


def test_executes_dag_and_only_observed_store_drilldown(catalog, golden_source):
    events = []
    result = run(catalog, golden_source, on_progress=events.append)
    assert result.status == 'completed'
    assert result.report_validation.valid and result.query_count == 6
    assert result.plan.scope.time_range == TimeRange(start=TODAY.replace(month=1, day=1), end=TODAY)
    assert len({item.snapshot_id for item in result.evidence}) == 1
    evidence = {item.task_id: item for item in result.evidence}
    assert evidence['profile'].rows[0][2] == len(GOLDEN['rows'])
    assert evidence['drilldown'].rows and evidence['drilldown'].task_type == 'store_product_drilldown'
    observed = next(row[0] for row in evidence['stores'].rows if str(row[0]) in {'SYN-06', '6'})
    assert evidence['drilldown'].parameters[-2] == observed
    assert evidence['drilldown'].store_id == str(observed)
    assert any(item.label == str(observed) for item in result.findings if isinstance(item, AnomalyFinding))
    started = [event['task']['task_id'] for event in events if event['kind'] == 'task_started']
    assert started.index('profile') < started.index('trend') < started.index('stores') < started.index('drilldown')
    assert [item['status'] for item in events if item['kind'] == 'report'] == ['RUNNING', 'COMPLETED']
    assert events[0]['kind'] == 'plan' and 'rationale' not in events[0]['plan']['tasks'][0]


def test_same_sdk_agent_selects_only_verified_report_focus(monkeypatch, catalog, golden_source):
    calls = []

    async def mocked_run(agent, input, **options):
        calls.append((agent, agent.output_type, json.loads(input), options['max_turns']))
        if agent.output_type is AnalysisPlanDraft:
            output = AnalysisPlanDraft.model_validate(make_plan().model_dump(exclude={'dependencies'}))
        else:
            output = AnalysisInterpretation(focus=InterpretationFocus.STORE_PRODUCT)
        return SimpleNamespace(final_output=output)

    monkeypatch.setattr(planner.Runner, 'run', mocked_run)
    result = asyncio.run(orchestrator.run_analysis(question=QUESTION, catalog=catalog, source=golden_source, today=TODAY))
    assert len(calls) == 2 and calls[0][0] is calls[1][0]
    assert [item[1] for item in calls] == [AnalysisPlanDraft, AnalysisInterpretation]
    assert [item[3] for item in calls] == [1, 1]
    assert 'store_product' in calls[1][2]['available_focuses']
    assert 'verified_finding_types' in calls[1][2] and 'rows' not in calls[1][2]
    assert result.interpretation_focus == InterpretationFocus.STORE_PRODUCT and result.report_validation.valid


def test_unapproved_focus_and_timeout_keep_verified_base_report(catalog, golden_source):
    profile = make_task('profile', 'profile_data_range', [], metrics=[])
    trend = make_task('daily', 'daily_trend', ['stat_date'], ['profile'])
    plan = make_plan([profile, trend])
    interpreter = AsyncMock(return_value=InterpretationFocus.STORE_PRODUCT)
    baseline = run(catalog, golden_source, plan, question='分析销售趋势')
    result = run(catalog, golden_source, plan, question='分析销售趋势', interpreter=interpreter)
    assert interpreter.await_count == 1
    assert result.interpretation_focus is None and result.report == baseline.report

    async def slow_interpretation(*args):
        await asyncio.sleep(1)
        return InterpretationFocus.TREND

    timed_out = run(catalog, golden_source, interpreter=slow_interpretation,
                    budget=AnalysisBudget(interpretation_timeout_seconds=0.01))
    assert timed_out.interpretation_focus is None
    assert timed_out.report_validation.valid and timed_out.status == 'completed'


def test_preserves_evidence_after_independent_failure_and_retries_transient_errors(monkeypatch, catalog, golden_source):
    execute = orchestrator.execute_task_query
    attempts = []

    def fail_selected(query, *, task, **options):
        attempts.append(task.task_id)
        if task.task_id == 'trend':
            raise sqlite3.OperationalError('local failure')
        if task.task_id == 'cities' and attempts.count('cities') == 1:
            raise TimeoutError('transient timeout')
        return execute(query, task=task, **options)

    monkeypatch.setattr(orchestrator, 'execute_task_query', fail_selected)
    result = run(catalog, golden_source)
    states = {entry.task_id: entry.status for entry in result.tasks}
    assert states['trend'] == TaskStatus.FAILED and states['stores'] == TaskStatus.SKIPPED
    assert states['drilldown'] == TaskStatus.SKIPPED and states['cities'] == TaskStatus.COMPLETED
    assert attempts.count('trend') == 1 and attempts.count('cities') == 2
    assert {entry.task_id for entry in result.evidence} == {'profile', 'cities', 'products'}
    assert result.status == 'partial' and result.report_validation.valid


def test_query_row_and_depth_budgets_stop_exploration(catalog, golden_source):
    limited = run(catalog, golden_source, budget=AnalysisBudget(max_queries=2))
    assert limited.query_count == 2 and limited.status == 'partial' and limited.report_validation.valid
    assert len(limited.evidence) == 2
    depth = run(catalog, golden_source, budget=AnalysisBudget(max_drilldown_depth=0))
    assert depth.query_count == 5 and depth.status == 'partial'
    assert any('下钻预算' in reason for reason in depth.incomplete)
    capped = run(catalog, golden_source, budget=AnalysisBudget(result_limit=2))
    assert capped.query_count == 5 and len(capped.evidence) == 5 and capped.status == 'partial'
    assert all(entry.row_count <= 2 for entry in capped.evidence)
    assert any(entry.possible_truncation for entry in capped.evidence)


def test_execution_deadline_stops_new_attempts_without_discarding_evidence(monkeypatch, catalog, golden_source):
    execute = orchestrator.execute_task_query
    unblock = Event()

    def wait_for_deadline(query, *, task, **options):
        if task.task_id == 'trend':
            unblock.wait(0.5)
        return execute(query, task=task, **options)

    monkeypatch.setattr(orchestrator, 'execute_task_query', wait_for_deadline)
    plan = make_plan([make_task('profile', 'profile_data_range', [], metrics=[]),
                      make_task('trend', 'daily_trend', ['stat_date'], ['profile']),
                      make_task('cities', 'dimension_breakdown', ['city'], ['trend'])])
    results = run(catalog, golden_source, plan, budget=AnalysisBudget(timeout_seconds=0.15))
    assert results.query_count == 2 and results.status == 'partial'
    assert [entry.task_id for entry in results.evidence] == ['profile']
    assert any('时间上限' in reason for reason in results.incomplete)


def test_direct_sql_reads_native_duckdb_dates(tmp_path, catalog):
    path = tmp_path / 'sales.duckdb'
    with duckdb.connect(str(path)) as db:
        db.execute('CREATE TABLE sales_summary (stat_date DATE, city VARCHAR, store_id INTEGER, product VARCHAR,'
                   ' sales_amount DECIMAL(12,2), order_count BIGINT)')
        db.execute('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', ['2026-11-08', '北京', 1, '茶', 10.25, 2])
    profile = make_task('profile', 'profile_data_range', [], metrics=[])
    daily = make_task('daily', 'daily_trend', ['stat_date'], ['profile'])
    result = run(catalog, DataSource(engine='duckdb', location=str(path)), make_plan([profile, daily]), question='分析销售趋势')
    assert result.status == 'completed' and result.report_validation.valid
    assert [item.rows for item in result.evidence] == [
        [['2026-11-08', '2026-11-08', 1]], [['2026-11-08', 10.25, 2, 5.125]],
    ]


@pytest.mark.parametrize('engine', ['sqlite', 'duckdb'])
def test_driver_interrupts_statement_at_analysis_deadline(tmp_path, engine):
    path = tmp_path / 'deadline.db'
    if engine == 'sqlite':
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE sample (value INTEGER)')
        slow = ('WITH RECURSIVE sample(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM sample WHERE n<1000000000) '
                'SELECT sum(n) FROM sample')
    else:
        with duckdb.connect(str(path)) as db:
            db.execute('CREATE TABLE sample (value INTEGER)')
        slow = 'SELECT sum(sin(i)) FROM range(1000000000) AS sample(i)'
    source = DataSource(engine=engine, location=str(path))
    start = monotonic()
    with pytest.raises(TimeoutError):
        fetch_rows(source, slow, [], deadline=start + 0.2)
    assert monotonic() - start < 2
    assert fetch_rows(source, 'SELECT 1', [], deadline=monotonic() + 2) == [(1,)]
