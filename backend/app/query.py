from datetime import date
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


class Query(BaseModel):
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time_range: TimeRange | None = None
    order_by: OrderBy | None = None
    limit: int = Field(default=500, ge=1, le=500)


def execute_query(path: Path, query: Query, today: date, catalog: dict | None = None) -> dict:
    if catalog is None:
        config = Path(__file__).resolve().parents[1] / 'configs' / 'sales_space.yaml'
        catalog = yaml.safe_load(config.read_text())['space']
    if len(set(query.metrics)) != len(query.metrics) or len(set(query.dimensions)) != len(query.dimensions):
        raise ValueError('指标和维度不能重复')
    try:
        metrics = {name: catalog['metrics'][name] for name in query.metrics}
        dimensions = {name: catalog['dimensions'][name] for name in query.dimensions}
        filter_dimensions = {item.field: catalog['dimensions'][item.field] for item in query.filters}
        first_model = next(iter(metrics.values()))['model']
        if any(item['model'] != first_model for item in [*metrics.values(), *dimensions.values(), *filter_dimensions.values()]):
            raise ValueError('指标和维度不属于同一数据模型')
        model = catalog['models'][first_model]
        table = model['table']
        date_column = model['date_column']
        allowed_columns = set(model['columns'])
        identifiers = [table, date_column, *allowed_columns, *query.metrics, *query.dimensions]
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
            if not isinstance(expression, exp.Sum) or not isinstance(expression.this, exp.Column) or expression.this.table:
                raise ValueError('指标表达式必须是单列求和')
            name = expression.this.name
            if name not in allowed_columns:
                raise ValueError('物理映射包含未授权的字段')
            return f'SUM("{name}")'

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
    sql = f'SELECT {", ".join(selections)} FROM "{table}" WHERE {" AND ".join(conditions)}'
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
    if any(item.name != table for item in tree.find_all(exp.Table)):
        raise ValueError('查询包含未授权的数据表')
    if any(item.name not in allowed_columns for item in tree.find_all(exp.Column)):
        raise ValueError('查询包含未授权的字段')

    start = time.monotonic()
    uri = f'file:{quote(str(path.resolve()))}?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        rows = db.execute(sql, parameters).fetchall()
    return {
        'columns': [*query.dimensions, *query.metrics],
        'rows': [list(row) for row in rows],
        'sql': sql,
        'source': 'SQLite 示例数据',
        'elapsed_ms': round((time.monotonic() - start) * 1000, 2),
    }
