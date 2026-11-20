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


def test_empty_result(tmp_path: Path):
    path = tmp_path / "sales.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sales_summary (stat_date TEXT, city TEXT, sales_amount INTEGER)")
    result = execute_query(path, Query(metrics=["sales_amount"], dimensions=["city"]), date(2026, 6, 1))
    assert result["rows"] == []


def test_semantic_mapping_drives_physical_sql(tmp_path: Path):
    path = tmp_path / "sales.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE daily_sales (business_day TEXT, region TEXT, net INTEGER)")
        db.execute("INSERT INTO daily_sales VALUES ('2026-01-01', '北京', 25)")
    catalog = {
        "models": {"sales_summary": {"table": "daily_sales", "columns": ["business_day", "region", "net"], "date_column": "business_day"}},
        "metrics": {"sales_amount": {"model": "sales_summary", "expression": "SUM(net)"}},
        "dimensions": {"city": {"model": "sales_summary", "column": "region"}},
    }
    result = execute_query(path, Query(metrics=["sales_amount"], dimensions=["city"]), date(2026, 6, 1), catalog)
    assert result["rows"] == [["北京", 25]]
    assert "daily_sales" in result["sql"]


def test_invalid_physical_mapping_is_rejected(tmp_path: Path):
    catalog = {
        "models": {"sales_summary": {"table": "sales_summary; DROP TABLE sales_summary", "columns": ["city", "stat_date", "sales_amount"], "date_column": "stat_date"}},
        "metrics": {"sales_amount": {"model": "sales_summary", "expression": "SUM(sales_amount)"}},
        "dimensions": {"city": {"model": "sales_summary", "column": "city"}},
    }
    with pytest.raises(ValueError):
        execute_query(tmp_path / "sales.db", Query(metrics=["sales_amount"], dimensions=["city"]), date(2026, 6, 1), catalog)


def test_query_connection_does_not_create_missing_database(tmp_path: Path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        execute_query(path, Query(metrics=["sales_amount"], dimensions=["city"]), date(2026, 6, 1))
    assert not path.exists()


def test_unlisted_metric_column_rejected(tmp_path: Path):
    catalog = {
        "models": {"sales_summary": {"table": "sales_summary", "columns": ["city", "stat_date"], "date_column": "stat_date"}},
        "metrics": {"sales_amount": {"model": "sales_summary", "expression": "SUM(private_data)"}},
        "dimensions": {"city": {"model": "sales_summary", "column": "city"}},
    }
    with pytest.raises(ValueError):
        execute_query(tmp_path / "sales.db", Query(metrics=["sales_amount"], dimensions=["city"]), date(2026, 6, 1), catalog)
