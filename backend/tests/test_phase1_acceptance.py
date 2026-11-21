"""用真实 Jaffle Shop 核对订单指标，并确认未授权 SQL 不会执行。"""

import os
from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.direct_sql import SqlRejected, execute_order_sql
from app.query import load_catalog, question_catalog
from app.sql_prompt import authorized_joins

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 21)
STATUS_NAMES = {
    "placed": "已下单",
    "shipped": "已发货",
    "completed": "已完成",
    "return_pending": "退货中",
    "returned": "已退货",
}


@pytest.fixture
def jaffle_shop_path() -> Path:
    configured_path = os.environ.get("SALES_DUCKDB_PATH")
    if not configured_path:
        pytest.skip("SALES_DUCKDB_PATH is required for the real Jaffle Shop acceptance test")
    path = Path(configured_path)
    if not path.is_file():
        pytest.skip("SALES_DUCKDB_PATH does not point to a Jaffle Shop DuckDB file")
    return path


def _run(path: Path, sql: str) -> dict:
    return execute_order_sql(path, sql, load_catalog(ROOT, "orders"), TODAY, authorized_joins(ROOT))


def _independent(path: Path, sql: str) -> list[list]:
    with duckdb.connect(str(path), read_only=True) as connection:
        return [list(row) for row in connection.execute(sql).fetchall()]


def test_payment_question_catalog_includes_four_published_amounts():
    catalog = question_catalog(ROOT, "orders", "分别统计四种支付金额")

    assert {
        "credit_card_amount",
        "coupon_amount",
        "bank_transfer_amount",
        "gift_card_amount",
    } <= set(catalog["metrics"])
    assert "nps" not in catalog["metrics"]


def test_direct_sql_matches_independent_totals_status_and_payment_amounts(jaffle_shop_path: Path):
    before = jaffle_shop_path.read_bytes()
    totals = _run(
        jaffle_shop_path,
        "SELECT SUM(amount) AS order_amount, COUNT(*) AS order_rows FROM orders",
    )
    by_status = _run(
        jaffle_shop_path,
        "SELECT status, SUM(amount) AS order_amount, COUNT(*) AS order_rows FROM orders GROUP BY status",
    )
    payments = _run(
        jaffle_shop_path,
        """
        SELECT SUM(credit_card_amount) AS credit_card_amount,
               SUM(coupon_amount) AS coupon_amount,
               SUM(bank_transfer_amount) AS bank_transfer_amount,
               SUM(gift_card_amount) AS gift_card_amount
        FROM orders
        """,
    )
    by_customer = _run(
        jaffle_shop_path,
        """
        SELECT customer_id, COUNT(*) AS order_rows, SUM(amount) AS order_amount
        FROM orders GROUP BY customer_id
        """,
    )

    assert totals["rows"] == _independent(
        jaffle_shop_path, "SELECT SUM(amount), COUNT(*) FROM orders"
    )
    assert totals["rows"] == [[1672.0, 99]]
    assert sorted(by_status["rows"]) == sorted(
        [STATUS_NAMES[status], amount, count]
        for status, amount, count in _independent(
            jaffle_shop_path,
            "SELECT status, SUM(amount), COUNT(*) FROM orders GROUP BY status",
        )
    )
    assert payments["rows"] == _independent(
        jaffle_shop_path,
        """
        SELECT SUM(amount) FILTER (WHERE payment_method = 'credit_card'),
               SUM(amount) FILTER (WHERE payment_method = 'coupon'),
               SUM(amount) FILTER (WHERE payment_method = 'bank_transfer'),
               SUM(amount) FILTER (WHERE payment_method = 'gift_card')
        FROM stg_payments
        """,
    )
    assert payments["rows"] == [[871.0, 185.0, 411.0, 205.0]]
    assert len(by_customer["rows"]) == 62
    assert sum(row[1] for row in by_customer["rows"]) == 99
    assert sum(row[2] for row in by_customer["rows"]) == 1672.0
    assert jaffle_shop_path.read_bytes() == before


def test_direct_sql_rejects_month_nps_and_payment_fanout(jaffle_shop_path: Path):
    before = jaffle_shop_path.read_bytes()
    month = "SELECT date_trunc('month', order_date) AS month, SUM(amount) AS order_amount FROM orders GROUP BY 1"
    nps = "SELECT SUM(nps) AS nps FROM orders"
    fanout = (
        "SELECT SUM(orders.amount) AS order_amount FROM orders "
        "JOIN stg_payments ON orders.order_id = stg_payments.order_id"
    )

    with pytest.raises(SqlRejected, match="未授权的指标或维度"):
        _run(jaffle_shop_path, month)
    with pytest.raises(SqlRejected, match="未授权的指标或维度"):
        _run(jaffle_shop_path, nps)
    with pytest.raises(SqlRejected, match="未授权的表关联"):
        _run(jaffle_shop_path, fanout)

    base, inflated = _independent(
        jaffle_shop_path,
        """
        SELECT
          (SELECT SUM(amount) FROM orders),
          (SELECT SUM(orders.amount) FROM orders JOIN stg_payments ON orders.order_id = stg_payments.order_id)
        """,
    )[0]
    assert base == 1672.0
    assert inflated == 2083.0
    assert jaffle_shop_path.read_bytes() == before
