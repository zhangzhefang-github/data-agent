import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app.query import Query, execute_query


def test_city_totals_include_current_year_only(tmp_path: Path):
    path = tmp_path / "sales.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sales_summary (stat_date TEXT, city TEXT, sales_amount INTEGER)")
        db.executemany("INSERT INTO sales_summary VALUES (?, ?, ?)", [
            ("2026-01-01", "北京", 100),
            ("2026-06-01", "北京", 25),
            ("2026-03-01", "上海", 0),
            ("2025-12-31", "北京", 1000),
        ])
    result = execute_query(path, Query(metrics=["sales_amount"], dimensions=["city"]), date(2026, 6, 1))
    assert result["rows"] == [["上海", 0], ["北京", 125]]


def test_unknown_metric_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        execute_query(tmp_path / "sales.db", Query(metrics=["password"], dimensions=["city"]), date(2026, 6, 1))
