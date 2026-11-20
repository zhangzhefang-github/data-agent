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


def test_store_ranking_and_weighted_order_value(tmp_path: Path):
    path = tmp_path / 'sales.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sales_summary (stat_date TEXT, city TEXT, store_id TEXT, product TEXT, sales_amount INTEGER, order_count INTEGER)')
        db.executemany('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', [
            ('2026-01-01', '北京', 'B', 'A', 100, 1),
            ('2026-06-01', '北京', 'B', 'B', 100, 9),
            ('2026-03-01', '北京', 'A', 'A', 200, 2),
            ('2026-03-01', '北京', 'C', 'A', 0, 0),
            ('2026-06-02', '北京', 'A', 'A', 500, 1),
        ])
    result = execute_query(path, Query(metrics=['sales_amount', 'order_count', 'average_order_value'], dimensions=['store'], filters=[{'field':'city','operator':'eq','value':'北京'}], order_by={'field':'sales_amount','direction':'desc'}, limit=3), date(2026, 6, 1))
    assert result['rows'] == [['A', 200, 2, 100.0], ['B', 200, 10, 20.0], ['C', 0, 0, None]]


def test_filters_time_boundaries_and_rejects_unknown_fields(tmp_path: Path):
    path = tmp_path / 'sales.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sales_summary (stat_date TEXT, city TEXT, store_id TEXT, product TEXT, sales_amount INTEGER, order_count INTEGER)')
        db.executemany('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', [
            ('2025-12-31', '北京', 'A', 'A', 1000, 1),
            ('2026-01-01', '北京', 'A', 'A', 10, 1),
            ('2026-06-01', '北京', 'A', 'A', 30, 1),
            ('2026-06-01', '北京', 'A', 'B', 50, 1),
            ('2026-06-02', '北京', 'A', 'A', 1000, 1),
        ])
    result = execute_query(path, Query(metrics=['order_count'], dimensions=['city'], filters=[{'field':'product','operator':'eq','value':'A'}], time_range={'start':'2026-01-01','end':'2026-06-01'}), date(2026, 6, 1))
    assert result['rows'] == [['北京', 2]]
    for query in [
        Query(metrics=['sales_amount'], dimensions=['private']),
        Query(metrics=['sales_amount'], filters=[{'field':'password','operator':'eq','value':'x'}]),
        Query(metrics=['sales_amount'], order_by={'field':'password','direction':'asc'}),
    ]:
        with pytest.raises(ValueError):
            execute_query(path, query, date(2026, 6, 1))
