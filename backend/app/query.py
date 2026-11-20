from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
import time
from typing import Literal
from urllib.parse import quote

import yaml
from pydantic import BaseModel, Field, model_validator
from sqlglot import exp, parse, parse_one


class Filter(BaseModel):
    field: str
    operator: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte']
    value: str


class TimeRange(BaseModel):
    start: date
    end: date

    @model_validator(mode='after')
    def validate_range(self):
        if self.start > self.end:
            raise ValueError('开始日期必须早于结束日期')
        return self


class OrderBy(BaseModel):
    field: str
    direction: Literal['asc', 'desc'] = 'asc'


class DataSource(BaseModel):
    engine: Literal['sqlite', 'postgresql', 'duckdb']
    location: str


def locate_source(sqlite_path: Path, database_url: str | None, duckdb_path: str | None) -> DataSource:
    if database_url:
        return DataSource(engine='postgresql', location=database_url)
    if duckdb_path:
        return DataSource(engine='duckdb', location=duckdb_path)
    return DataSource(engine='sqlite', location=str(sqlite_path))


def load_catalog(root: Path, space_name: str) -> dict:
    if space_name not in {'sales', 'orders'}:
        raise ValueError('未知主题空间')
    path = root / 'backend' / 'configs' / f'{space_name}_space.yaml'
    return yaml.safe_load(path.read_text())['space']


def space_instructions(catalog: dict) -> str:
    metrics = '、'.join(item['name'] for item in catalog['metrics'].values())
    dimensions = '、'.join(item['name'] for item in catalog['dimensions'].values())
    return (
        f"你是{catalog['name']}助手。只能回答该主题空间内的问题。"
        "先读取目录，再通过结构化工具查询，禁止回答未经查询的数值。"
        f"支持的指标：{metrics}。支持的维度：{dimensions}。"
        "无指定时间时查询今年截至今天。"
        "追问去年同期时，使用最近一次成功查询的指标、维度和过滤条件，仅调整时间。"
        "遇到写入、未授权表、未知指标或其他不支持的请求请拒绝并说明。"
        "按查询结果用中文简短回答。若无行则说明无数据。指标为零时明确显示为0，不能说成缺失或无数据。"
        f"{catalog.get('instructions', '')}"
    )


class Query(BaseModel):
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time_range: TimeRange | None = None
    order_by: OrderBy | None = None
    limit: int = Field(default=500, ge=1, le=500)


def execute_query(path: Path, query: Query, today: date, catalog: dict | None = None, database_url: str | None = None, source: DataSource | None = None) -> dict:
    if source is None:
        source = DataSource(engine='postgresql', location=database_url) if database_url else DataSource(engine='sqlite', location=str(path))
    if catalog is None:
        config = Path(__file__).resolve().parents[1] / 'configs' / 'sales_space.yaml'
        catalog = yaml.safe_load(config.read_text())['space']
    if len(set(query.metrics)) != len(query.metrics) or len(set(query.dimensions)) != len(query.dimensions):
        raise ValueError('指标和维度不能重复')
    try:
        metrics = {name: catalog['metrics'][name] for name in query.metrics}
        dimensions = {name: catalog['dimensions'][name] for name in query.dimensions}
        filter_dimensions = {item.field: catalog['dimensions'][item.field] for item in query.filters}
        for metric in metrics.values():
            allowed_filters = metric.get('allowed_filters')
            if allowed_filters is not None and any(item.field not in allowed_filters for item in query.filters):
                raise ValueError('指标不支持该过滤字段')
        first_model = next(iter(metrics.values()))['model']
        if any(item['model'] != first_model for item in [*metrics.values(), *dimensions.values(), *filter_dimensions.values()]):
            raise ValueError('指标和维度不属于同一数据模型')
        model = catalog['models'][first_model]
        table = model['table']
        schema = model.get('schema')
        date_column = model['date_column']
        allowed_columns = set(model['columns'])
        identifiers = [table, date_column, *allowed_columns, *query.metrics, *query.dimensions]
        if schema is not None:
            identifiers.append(schema)
        if not all(isinstance(item, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item) for item in identifiers):
            raise ValueError('物理映射包含无效标识符')
        if date_column not in allowed_columns:
            raise ValueError('物理映射包含未授权的字段')
        def column(item):
            name = item['column']
            if name not in allowed_columns:
                raise ValueError('物理映射包含未授权的字段')
            return f'"{name}"'

        dimension_sql = {name: column(item) for name, item in dimensions.items()}
        filter_sql = {name: column(item) for name, item in filter_dimensions.items()}
        def metric_expression(name):
            item = catalog['metrics'][name]
            expression = parse_one(item['expression'], read='sqlite')
            if isinstance(expression, exp.Count) and isinstance(expression.this, exp.Star):
                return 'COUNT(*)'
            if not isinstance(expression, exp.Sum) or not isinstance(expression.this, exp.Column) or expression.this.table:
                raise ValueError('指标表达式必须是单列求和或 COUNT(*)')
            column_name = expression.this.name
            if column_name not in allowed_columns:
                raise ValueError('物理映射包含未授权的字段')
            return f'SUM("{column_name}")'

        metric_sql = {}
        for name, item in metrics.items():
            if 'numerator' in item and 'denominator' in item:
                numerator = catalog['metrics'][item['numerator']]
                denominator = catalog['metrics'][item['denominator']]
                if numerator['model'] != first_model or denominator['model'] != first_model:
                    raise ValueError('指标不属于同一数据模型')
                metric_sql[name] = f'({metric_expression(item["numerator"])} * 1.0 / NULLIF({metric_expression(item["denominator"])}, 0))'
            else:
                metric_sql[name] = metric_expression(name)
    except (KeyError, TypeError, StopIteration) as exc:
        raise ValueError('不支持的指标、维度或语义配置不完整') from exc

    selections = [f'{value} AS "{name}"' for name, value in [*dimension_sql.items(), *metric_sql.items()]]
    start_date = query.time_range.start if query.time_range else today.replace(month=1, day=1)
    end_date = query.time_range.end if query.time_range else today
    conditions = [f'"{date_column}" >= ?', f'"{date_column}" <= ?']
    parameters = [start_date.isoformat(), end_date.isoformat()]
    operators = {'eq': '=', 'ne': '!=', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
    for item in query.filters:
        conditions.append(f'{filter_sql[item.field]} {operators[item.operator]} ?')
        parameters.append(item.value)
    if source.engine == 'postgresql' and not schema:
        raise ValueError('PostgreSQL 数据模型必须指定授权 schema')
    table_reference = f'"{schema}"."{table}"' if schema else f'"{table}"'
    sql = f'SELECT {", ".join(selections)} FROM {table_reference} WHERE {" AND ".join(conditions)}'
    if dimension_sql:
        sql += f' GROUP BY {", ".join(dimension_sql.values())}'
    else:
        sql += ' HAVING COUNT(*) > 0'
    if query.order_by:
        selectable = {**dimension_sql, **metric_sql}
        if query.order_by.field not in selectable:
            raise ValueError('排序字段必须是已选择的指标或维度')
        sql += f' ORDER BY {selectable[query.order_by.field]} {query.order_by.direction.upper()}'
        if 'store' in dimension_sql and query.order_by.field != 'store':
            sql += f', {dimension_sql["store"]} ASC'
    elif dimension_sql:
        sql += f' ORDER BY {", ".join(dimension_sql.values())}'
    sql += ' LIMIT ?'
    parameters.append(query.limit)
    statements = parse(sql, read='sqlite')
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError('只允许单条 SELECT 查询')
    tree = statements[0]
    if any(item.name != table or (item.db or None) != schema for item in tree.find_all(exp.Table)):
        raise ValueError('查询包含未授权的数据表')
    if any(item.name not in allowed_columns for item in tree.find_all(exp.Column)):
        raise ValueError('查询包含未授权的字段')

    start = time.monotonic()
    rows = fetch_rows(source, sql, parameters)
    labels = {'postgresql': 'PostgreSQL ADS', 'duckdb': 'DuckDB', 'sqlite': 'SQLite 示例数据'}
    return {
        'columns': [*query.dimensions, *query.metrics],
        'rows': [[value.isoformat() if isinstance(value, date) else float(value) if isinstance(value, Decimal) else value for value in row] for row in rows],
        'sql': sql,
        'source': labels[source.engine],
        'elapsed_ms': round((time.monotonic() - start) * 1000, 2),
    }


def fetch_rows(source: DataSource, sql: str, parameters: list):
    if source.engine == 'postgresql':
        from urllib.parse import urlsplit
        import psycopg

        if urlsplit(source.location).scheme not in ('postgres', 'postgresql'):
            raise ValueError('只支持 PostgreSQL 连接地址')
        with psycopg.connect(source.location, connect_timeout=5) as db:
            with db.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute(sql.replace('?', '%s'), parameters)
                return cursor.fetchall()
    if source.engine == 'duckdb':
        import duckdb

        import threading

        with duckdb.connect(source.location, read_only=True) as db:
            timer = threading.Timer(5, db.interrupt)
            timer.start()
            try:
                return db.execute(sql, parameters).fetchall()
            finally:
                timer.cancel()
    uri = f'file:{quote(str(Path(source.location).resolve()))}?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return db.execute(sql, parameters).fetchall()
