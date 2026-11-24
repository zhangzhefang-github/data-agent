"""Compile authorized sales analysis tasks into bounded Direct SQL."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import math
import re
from time import monotonic

from sqlglot import exp, parse, parse_one

from app.analysis.evidence import Evidence
from app.analysis.models import AnalysisFilter, AnalysisPlan, AnalysisTask, TaskType
from app.query import DataSource, TimeRange, fetch_rows, physical_value


_DATASET_ID = 'sales_summary'
_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_PROFILE_COLUMNS = ['first_business_date', 'last_business_date', 'summary_row_count']
_SOURCE_LABELS = {'sqlite': 'SQLite 示例数据', 'duckdb': 'DuckDB', 'postgresql': 'PostgreSQL ADS'}


@dataclass(frozen=True)
class TaskQuery:
    sql: str
    parameters: list[str | int]
    columns: list[str]
    filters: list[AnalysisFilter]
    time_range: TimeRange | None
    limit: int


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError('销售数据集包含无效标识符。')
    return f'"{value}"'


def _column(item: dict, allowed: set[str]) -> str:
    if item.get('model') != _DATASET_ID or item.get('column') not in allowed:
        raise ValueError('查询使用了未授权字段。')
    return _identifier(item['column'])


def _aggregate(identifier: str, catalog: dict, allowed: set[str]) -> str:
    metric = catalog['metrics'][identifier]
    if metric.get('model') != _DATASET_ID:
        raise ValueError('查询使用了未授权指标。')
    if 'expression' not in metric:
        raise ValueError('派生指标必须使用已授权的基础汇总指标。')
    expression = parse_one(metric['expression'], read='sqlite')
    if isinstance(expression, exp.Count) and isinstance(expression.this, exp.Star):
        return 'COUNT(*)'
    if (
        not isinstance(expression, exp.Sum)
        or not isinstance(expression.this, exp.Column)
        or expression.this.table
        or expression.this.name not in allowed
    ):
        raise ValueError('指标表达式包含未授权的汇总或字段。')
    return f'SUM({_identifier(expression.this.name)})'


def _metric(identifier: str, catalog: dict, allowed: set[str]) -> str:
    metric = catalog['metrics'][identifier]
    if metric.get('model') != _DATASET_ID:
        raise ValueError('查询使用了未授权指标。')
    if 'numerator' in metric and 'denominator' in metric:
        numerator = _aggregate(metric['numerator'], catalog, allowed)
        denominator = _aggregate(metric['denominator'], catalog, allowed)
        return f'({numerator} * 1.0 / NULLIF({denominator}, 0))'
    return _aggregate(identifier, catalog, allowed)


def _validate_sql(sql: str, table: str, schema: str | None, columns: set[str], parameter_count: int) -> None:
    statements = parse(sql, read='sqlite')
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError('探索分析只允许单条 SELECT。')
    select = statements[0]
    tables = [(item.name, item.db or None, item.catalog) for item in select.find_all(exp.Table)]
    if tables != [(table, schema, '')] or select.find(exp.Join) or select.find(exp.Subquery):
        raise ValueError('查询使用了未授权数据集。')
    if any(item.name not in columns or item.table or item.db or item.catalog for item in select.find_all(exp.Column)):
        raise ValueError('查询使用了未授权字段。')
    if len(list(select.find_all(exp.Placeholder))) != parameter_count:
        raise ValueError('查询参数与执行计划不一致。')


def compile_task_query(
    task: AnalysisTask, plan: AnalysisPlan, catalog: dict, source: DataSource, limit: int, *,
    store_value: str | int | None = None,
) -> TaskQuery:
    if catalog.get('id') != 'sales' or plan.scope.space_id != 'sales':
        raise ValueError('当前主题空间未授权销售探索分析。')
    dataset = catalog['models'][_DATASET_ID]
    table, schema = dataset['table'], dataset.get('schema')
    allowed = set(dataset['columns'])
    if source.engine == 'postgresql' and not schema:
        raise ValueError('PostgreSQL 数据模型必须指定授权 schema。')
    table_sql = f'{_identifier(schema)}.{_identifier(table)}' if schema else _identifier(table)
    date_column = _column(catalog['dimensions']['stat_date'], allowed)
    if catalog['dimensions']['stat_date']['column'] != dataset['date_column']:
        raise ValueError('业务日期字段与授权映射不一致。')
    if task.task_type == TaskType.PROFILE_DATA_RANGE:
        aggregates = [f'MIN({date_column})', f'MAX({date_column})', 'COUNT(*)']
        selections = ', '.join(f'{value} AS {_identifier(name)}' for name, value in zip(_PROFILE_COLUMNS, aggregates))
        sql = f'SELECT {selections} FROM {table_sql}'
        _validate_sql(sql, table, schema, allowed, 0)
        return TaskQuery(sql, [], _PROFILE_COLUMNS, [], None, 1)

    span = plan.scope.time_range
    if span is None or not 1 <= limit <= 500:
        raise ValueError('执行分析查询需要明确的日期范围及结果上限。')
    dimensions = {identifier: _column(catalog['dimensions'][identifier], allowed) for identifier in task.dimension_ids}
    metrics = {identifier: _metric(identifier, catalog, allowed) for identifier in task.metric_ids}
    columns = {**dimensions, **metrics}
    if not dimensions or not metrics:
        raise ValueError('分析查询需要已授权的维度与指标。')

    filters = plan.scope.filters.copy()
    parameters: list[str | int] = [span.start.isoformat(), span.end.isoformat()]
    conditions = [f'{date_column} >= ?', f'{date_column} <= ?']
    grouped_filters: dict[str, list[str]] = {}
    for item in filters:
        dimension = catalog['dimensions'][item.dimension_id]
        grouped_filters.setdefault(item.dimension_id, []).append(physical_value(dimension, item.value))
    for dimension_id, values in grouped_filters.items():
        column = _column(catalog['dimensions'][dimension_id], allowed)
        if len(values) == 1:
            conditions.append(f'{column} = ?')
        else:
            placeholders = ', '.join('?' for _ in values)
            conditions.append(f'{column} IN ({placeholders})')
        parameters.extend(values)
    if task.task_type == TaskType.STORE_PRODUCT_DRILLDOWN:
        if store_value is None:
            raise ValueError('下钻需要由查询证据提供门店标识。')
        store_column = _column(catalog['dimensions']['store'], allowed)
        conditions.append(f'{store_column} = ?')
        parameters.append(store_value)
        filters = [item for item in filters if item.dimension_id != 'store']
        filters.append(AnalysisFilter(dimension_id='store', value=str(store_value)))
    parameters.append(limit)
    selections = ', '.join(f'{value} AS {_identifier(identifier)}' for identifier, value in columns.items())
    group = ', '.join(dimensions.values())
    sql = (
        f'SELECT {selections} FROM {table_sql} WHERE {" AND ".join(conditions)}'
        f' GROUP BY {group} ORDER BY {group} LIMIT ?'
    )
    _validate_sql(sql, table, schema, allowed, len(parameters))
    return TaskQuery(sql, parameters, list(columns), filters, span, limit)


def _result_value(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def execute_task_query(
    query: TaskQuery, *, task: AnalysisTask, plan: AnalysisPlan, source: DataSource, snapshot_id: str,
    store_id: str | None = None, source_label: str | None = None, deadline: float | None = None,
) -> Evidence:
    start = monotonic()
    rows = [[_result_value(value) for value in row]
            for row in fetch_rows(source, query.sql, query.parameters, deadline=deadline)]
    elapsed_ms = round((monotonic() - start) * 1000, 2)
    return Evidence(
        evidence_id=f'{plan.analysis_id}:{task.task_id}',
        analysis_id=plan.analysis_id,
        task_id=task.task_id,
        task_type=task.task_type,
        metric_ids=task.metric_ids,
        dimension_ids=task.dimension_ids,
        time_range=query.time_range,
        filters=query.filters,
        sql=query.sql,
        parameters=query.parameters,
        columns=query.columns,
        rows=rows,
        row_count=len(rows),
        result_limit=query.limit,
        possible_truncation=task.task_type != TaskType.PROFILE_DATA_RANGE and len(rows) == query.limit,
        elapsed_ms=elapsed_ms,
        source=source_label or _SOURCE_LABELS[source.engine],
        snapshot_id=snapshot_id,
        dataset_id=_DATASET_ID,
        store_id=store_id,
    )
