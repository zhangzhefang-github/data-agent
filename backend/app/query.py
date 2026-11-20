from datetime import date
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

from pydantic import BaseModel, Field
from sqlglot import exp, parse


class Query(BaseModel):
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)


def execute_query(path: Path, query: Query, today: date) -> dict:
    if query.metrics != ["sales_amount"] or query.dimensions != ["city"]:
        raise ValueError("不支持的指标或维度")
    sql = (
        "SELECT city, SUM(sales_amount) AS sales_amount FROM sales_summary "
        "WHERE stat_date >= ? AND stat_date <= ? GROUP BY city ORDER BY city LIMIT 500"
    )
    statements = parse(sql, read="sqlite")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("只允许单条 SELECT 查询")
    tree = statements[0]
    if any(table.name != "sales_summary" for table in tree.find_all(exp.Table)):
        raise ValueError("查询包含未授权的数据表")
    allowed_columns = {"city", "sales_amount", "stat_date"}
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
