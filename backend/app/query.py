from datetime import date
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote

import yaml
from pydantic import BaseModel, Field
from sqlglot import exp, parse, parse_one


class Query(BaseModel):
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)


def execute_query(path: Path, query: Query, today: date, catalog: dict | None = None) -> dict:
    if catalog is None:
        config = Path(__file__).resolve().parents[1] / "configs" / "sales_space.yaml"
        catalog = yaml.safe_load(config.read_text())["space"]
    if query.metrics != ["sales_amount"] or query.dimensions != ["city"]:
        raise ValueError("不支持的指标或维度")
    try:
        metric = catalog["metrics"][query.metrics[0]]
        dimension = catalog["dimensions"][query.dimensions[0]]
        if metric["model"] != dimension["model"]:
            raise ValueError("指标和维度不属于同一数据模型")
        model = catalog["models"][metric["model"]]
        table = model["table"]
        city_column = dimension["column"]
        date_column = model["date_column"]
        aggregate = parse_one(metric["expression"], read="sqlite")
        if not isinstance(aggregate, exp.Sum) or not isinstance(aggregate.this, exp.Column):
            raise ValueError("指标表达式必须是单列求和")
        metric_column = aggregate.this.name
        allowed_columns = set(model["columns"])
        identifiers = [table, city_column, date_column, metric_column, *allowed_columns]
        if not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
            raise ValueError("物理映射包含无效标识符")
        if not {city_column, date_column, metric_column} <= allowed_columns:
            raise ValueError("物理映射包含未授权的字段")
    except (KeyError, TypeError) as exc:
        raise ValueError("语义配置不完整") from exc

    sql = (
        f'SELECT "{city_column}" AS city, SUM("{metric_column}") AS sales_amount '
        f'FROM "{table}" WHERE "{date_column}" >= ? AND "{date_column}" <= ? '
        f'GROUP BY "{city_column}" ORDER BY "{city_column}" LIMIT 500'
    )
    statements = parse(sql, read="sqlite")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("只允许单条 SELECT 查询")
    tree = statements[0]
    if any(item.name != table for item in tree.find_all(exp.Table)):
        raise ValueError("查询包含未授权的数据表")
    if any(column.name not in allowed_columns for column in tree.find_all(exp.Column)):
        raise ValueError("查询包含未授权的字段")

    start = time.monotonic()
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        rows = db.execute(sql, (today.replace(month=1, day=1).isoformat(), today.isoformat())).fetchall()
    return {
        "columns": ["city", "sales_amount"],
        "rows": [list(row) for row in rows],
        "sql": sql,
        "source": "SQLite 示例数据",
        "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
    }
