"""用真实 Jaffle Shop 核对订单指标，并确认未授权 SQL 不会执行。"""

import os
from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.direct_sql import SqlCorrection, SqlRejected, execute_order_sql, prior_order_sql
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


def test_chinese_payment_method_literals_return_rows_in_the_reviewed_window(jaffle_shop_path: Path):
    """用户按中文支付方式筛选时，应得到该区间的笔数和金额，而不是空结果。"""
    result = _run(
        jaffle_shop_path,
        """
        SELECT
          payments.payment_method AS payment_method,
          COUNT(*) AS payment_rows,
          SUM(payments.amount) AS payment_amount
        FROM main.payments AS payments
        WHERE
          payments.order_date BETWEEN CAST('2018-01-01' AS DATE) AND CAST('2018-04-09' AS DATE)
          AND payments.payment_method IN ('信用卡', '优惠券', '银行转账', '礼品卡')
        GROUP BY
          payments.payment_method
        ORDER BY
          payment_amount DESC
        LIMIT 500
        """,
    )

    assert result["rows"] == [
        ["信用卡", 55, 871.0],
        ["银行转账", 33, 411.0],
        ["礼品卡", 12, 205.0],
        ["优惠券", 13, 185.0],
    ]
    assert "信用卡" not in result["sql"]
    assert "credit_card" in result["sql"]


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


def test_customer_questions_match_the_reviewed_window(jaffle_shop_path: Path):
    window = "order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'"
    before = jaffle_shop_path.read_bytes()
    joined = _run(
        jaffle_shop_path,
        f"""
        SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount
        FROM orders
        INNER JOIN customers ON orders.customer_id = customers.customer_id
        WHERE {window}
        """,
    )
    grouped = _run(
        jaffle_shop_path,
        f"""
        SELECT customer_id, COUNT(*) AS order_rows
        FROM orders
        WHERE {window}
        GROUP BY customer_id
        """,
    )
    top = _run(
        jaffle_shop_path,
        f"""
        SELECT customer_id, SUM(amount) AS order_amount
        FROM orders
        WHERE {window}
        GROUP BY customer_id
        ORDER BY order_amount DESC
        LIMIT 1
        """,
    )

    assert joined["rows"] == [[99, 1672.0]]
    assert "JOIN" in joined["sql"].upper()
    assert len(grouped["rows"]) == 62
    assert sum(row[1] for row in grouped["rows"]) == 99
    assert top["rows"] == [[51, 99.0]]
    assert "first_name" not in top["sql"]
    assert "last_name" not in top["sql"]
    with pytest.raises(SqlCorrection, match="等值内连接"):
        _run(
            jaffle_shop_path,
            f"""
            SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount
            FROM orders
            WHERE customer_id IS NOT NULL AND {window}
            """,
        )
    assert jaffle_shop_path.read_bytes() == before


def test_reviewed_window_is_accepted_at_the_order_chat_entry(monkeypatch, tmp_path, jaffle_shop_path: Path):
    import json
    from types import SimpleNamespace

    from agents.tool_context import ToolContext
    from fastapi.testclient import TestClient

    from app import main

    before = jaffle_shop_path.read_bytes()
    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    monkeypatch.setattr(main, "CATALOG", load_catalog(ROOT, "orders"))
    monkeypatch.setattr(main, "DUCKDB_PATH", str(jaffle_shop_path))
    monkeypatch.setattr(main, "DATABASE_URL", None)
    questions = [
        (
            "有客户记录的订单笔数和订单金额是多少？请看 2018-01-01 至 2018-04-09。",
            """
            SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount
            FROM orders
            INNER JOIN customers ON orders.customer_id = customers.customer_id
            WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
            """,
            [[99, 1672.0]],
        ),
        (
            "每个客户各有多少笔订单？请看 2018-01-01 至 2018-04-09。",
            """
            SELECT customer_id, COUNT(*) AS order_rows
            FROM orders
            WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
            GROUP BY customer_id
            """,
            None,
        ),
        (
            "订单金额最高的客户是谁？请看 2018-01-01 至 2018-04-09。",
            """
            SELECT customer_id, SUM(amount) AS order_amount
            FROM orders
            WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
            GROUP BY customer_id
            ORDER BY order_amount DESC
            LIMIT 1
            """,
            [[51, 99.0]],
        ),
    ]

    def ask(client, sql, message):
        class Run:
            async def stream_events(self):
                arguments = json.dumps({"sql": sql})
                context = ToolContext(None, tool_name=tool.name, tool_call_id="1", tool_arguments=arguments)
                await tool.on_invoke_tool(context, arguments)
                yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(
                    type="response.output_text.delta", delta="完成",
                ))

        def runner(agent, prompt, **kwargs):
            nonlocal tool
            tool = next(item for item in agent.tools if item.name == "execute_direct_sql")
            assert [item.name for item in agent.tools] == ["execute_direct_sql"]
            assert "'id': 'customer'" in prompt
            assert "不会放大行数" in prompt
            assert "first_name" not in prompt
            assert "order_to_payment" not in prompt
            return Run()

        tool = None
        monkeypatch.setattr(main.Runner, "run_streamed", runner)
        response = client.post("/api/chat/stream", json={"message": message})
        assert response.status_code == 200
        return dict(
            (kind.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
            for part in response.text.strip().split("\n\n")
            for kind, data in [part.split("\n", 1)]
        )

    with TestClient(main.app) as client:
        joined, grouped, top = (ask(client, sql, message) for message, sql, _ in questions)
    assert joined["done"]["status"] == "completed"
    assert joined["result"]["rows"] == questions[0][2]
    assert "JOIN" in joined["result"]["sql"].upper()
    assert grouped["done"]["status"] == "completed"
    assert len(grouped["result"]["rows"]) == 62
    assert sum(row[1] for row in grouped["result"]["rows"]) == 99
    assert top["done"]["status"] == "completed"
    assert top["result"]["rows"] == questions[2][2]
    assert "first_name" not in top["result"]["sql"]
    assert jaffle_shop_path.read_bytes() == before


@pytest.mark.parametrize("order_count", ["COUNT(*)", "COUNT(DISTINCT o.order_id)"])
def test_with_preaggregated_payments_keeps_order_and_payment_totals(jaffle_shop_path: Path, order_count: str):
    sql = f"""
        WITH payment_summary AS (
            SELECT order_id, COUNT(*) AS payment_count, SUM(amount) AS payment_amount
            FROM main.payments
            WHERE order_date BETWEEN CAST('2018-01-01' AS DATE) AND CAST('2018-04-09' AS DATE)
            GROUP BY order_id
        )
        SELECT {order_count} AS order_rows,
               SUM(o.amount) AS order_amount,
               SUM(ps.payment_count) AS payment_rows,
               SUM(ps.payment_amount) AS payment_amount
        FROM main.orders AS o
        JOIN payment_summary AS ps ON o.order_id = ps.order_id
        WHERE o.order_date BETWEEN CAST('2018-01-01' AS DATE) AND CAST('2018-04-09' AS DATE)
        LIMIT 500
    """

    result = _run(jaffle_shop_path, sql)

    assert result["columns"] == ["order_rows", "order_amount", "payment_rows", "payment_amount"]
    assert result["rows"] == [[99, 1672.0, 113, 1672.0]]


def test_filtered_orders_cte_keeps_its_order_grain(jaffle_shop_path: Path):
    sql = """
        WITH january_orders AS (
            SELECT order_id, amount FROM orders
            WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-01-31'
        )
        SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount
        FROM january_orders
    """

    assert _run(jaffle_shop_path, sql)["rows"] == [[29, 496.0]]


@pytest.mark.parametrize("per_order_sql", [
    "SELECT o.order_id, o.amount FROM orders o JOIN customers c ON c.customer_id = o.customer_id",
    "SELECT order_id, SUM(amount) AS amount FROM orders GROUP BY order_id",
    "SELECT DISTINCT order_id, amount FROM orders",
])
def test_order_count_accepts_derived_rows_at_order_grain(jaffle_shop_path: Path, per_order_sql: str):
    sql = f"WITH per_order AS ({per_order_sql}) SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount FROM per_order"

    assert _run(jaffle_shop_path, sql)["rows"] == [[99, 1672.0]]


def test_scalar_subqueries_can_return_authorized_aggregates(jaffle_shop_path: Path):
    sql = """
        SELECT (SELECT COUNT(*) FROM orders) AS order_rows,
               (SELECT SUM(amount) FROM payments) AS payment_amount
    """

    assert _run(jaffle_shop_path, sql)["rows"] == [[99, 1672.0]]


def test_window_can_rank_customers_by_published_order_amount(jaffle_shop_path: Path):
    sql = """
        WITH totals AS (
            SELECT customer_id, SUM(amount) AS order_amount FROM orders GROUP BY customer_id
        ), ranked AS (
            SELECT customer_id, order_amount,
                   ROW_NUMBER() OVER (ORDER BY order_amount DESC, customer_id) AS position
            FROM totals
        )
        SELECT customer_id, order_amount FROM ranked WHERE position = 1
    """

    assert _run(jaffle_shop_path, sql)["rows"] == [[51, 99.0]]


def test_payment_rows_cannot_be_returned_as_order_rows(jaffle_shop_path: Path):
    with pytest.raises(SqlCorrection, match="payment_rows"):
        _run(jaffle_shop_path, "SELECT COUNT(*) AS order_rows FROM payments")


def test_payment_method_case_can_be_repaired_into_a_group_by():
    sql = """
        SELECT
          SUM(CASE WHEN payment_method = '信用卡' THEN amount ELSE 0 END) AS credit_card_amount
        FROM payments
    """
    with pytest.raises(SqlCorrection, match="payment_method"):
        _run(Path("/tmp/payment-method-case-not-executed.duckdb"), sql)


def test_payment_count_output_requires_published_alias(jaffle_shop_path: Path):
    with pytest.raises(SqlCorrection, match="payment_rows"):
        _run(jaffle_shop_path, "SELECT COUNT(*) AS payment_count FROM payments")
    with pytest.raises(SqlCorrection, match="payment_rows"):
        _run(jaffle_shop_path, """
            WITH paid AS (SELECT order_id, COUNT(*) AS payment_count FROM payments GROUP BY order_id)
            SELECT SUM(payment_count) AS payment_count FROM paid
        """)


@pytest.mark.parametrize("sql", [
    """
    SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount, AVG(amount) AS avg_order_amount
    FROM orders
    WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09' AND status = 'completed'
    """,
    """
    SELECT status, COUNT(*) AS order_rows, SUM(amount) AS order_amount, AVG(amount) AS avg_order_amount
    FROM orders GROUP BY status ORDER BY order_amount DESC
    """,
    "SELECT COUNT(*) AS order_rows, SUM(amount) / COUNT(*) AS avg_order_amount FROM orders",
])
def test_unpublished_average_keeps_authorized_metrics_repairable(sql: str):
    with pytest.raises(SqlCorrection, match="平均订单金额尚未发布"):
        _run(Path("/tmp/unpublished-average-not-executed.duckdb"), sql)


@pytest.mark.parametrize("sql", [
    "SELECT AVG(amount) AS avg_order_amount FROM orders",
    "SELECT SUM(amount) / COUNT(*) AS avg_order_amount FROM orders",
])
def test_unpublished_average_alone_names_the_metric(sql: str):
    with pytest.raises(SqlRejected, match="平均订单金额尚未发布，不能返回结果"):
        _run(Path("/tmp/unpublished-average-not-executed.duckdb"), sql)


CUSTOMER_SHARE = """
WITH orders_with_customer AS (
  SELECT o.order_id, o.customer_id, o.amount
  FROM main.orders AS o
  JOIN main.customers AS c ON o.customer_id = c.customer_id
  WHERE o.order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
),
summary_with_customer AS (
  SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount FROM orders_with_customer
),
summary_all_orders AS (
  SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount
  FROM main.orders
  WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
)
SELECT
  summary_with_customer.order_rows,
  summary_with_customer.order_amount,
  summary_all_orders.order_rows AS total_order_rows,
  summary_all_orders.order_amount AS total_order_amount,
  CASE WHEN summary_all_orders.order_rows = 0 THEN 0
       ELSE summary_with_customer.order_rows * 1.0 / summary_all_orders.order_rows END AS order_rows_ratio,
  CASE WHEN summary_all_orders.order_amount = 0 THEN 0
       ELSE summary_with_customer.order_amount / summary_all_orders.order_amount END AS order_amount_ratio
FROM summary_with_customer, summary_all_orders
"""


def test_customer_share_ratio_keeps_authorized_metrics_repairable():
    with pytest.raises(SqlCorrection, match="订单占比尚未发布"):
        _run(Path("/tmp/unpublished-share-not-executed.duckdb"), CUSTOMER_SHARE)


def test_inline_share_ratio_keeps_authorized_metrics_repairable():
    sql = """
    SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount,
           CASE WHEN COUNT(*) = 0 THEN 0 ELSE COUNT(*) * 1.0 / COUNT(*) END AS order_rows_ratio
    FROM orders
    """
    with pytest.raises(SqlCorrection, match="订单占比尚未发布"):
        _run(Path("/tmp/unpublished-share-not-executed.duckdb"), sql)


def test_share_ratio_alone_names_the_metric():
    with pytest.raises(SqlRejected, match="订单占比尚未发布，不能返回结果"):
        _run(Path("/tmp/unpublished-share-not-executed.duckdb"), "SELECT COUNT(*) * 1.0 / COUNT(*) AS order_rows_ratio FROM orders")


def test_non_ratio_case_stays_unauthorized():
    sql = "SELECT CASE WHEN COUNT(*) = 0 THEN 0 ELSE 1 END AS flag, COUNT(*) AS order_rows FROM orders"
    with pytest.raises(SqlRejected, match="未授权的指标或维度"):
        _run(Path("/tmp/unpublished-share-not-executed.duckdb"), sql)


def test_union_all_runs_as_one_readonly_query(tmp_path: Path):
    path = tmp_path / "orders.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("""
            CREATE TABLE orders (
                order_id INTEGER, order_date DATE, status VARCHAR, amount DOUBLE, customer_id INTEGER,
                credit_card_amount DOUBLE, coupon_amount DOUBLE, bank_transfer_amount DOUBLE, gift_card_amount DOUBLE
            )
        """)
        db.execute("CREATE TABLE customers (customer_id INTEGER)")
        db.execute("""
            CREATE TABLE payments (
                order_id INTEGER, payment_method VARCHAR, amount DOUBLE, order_date DATE
            )
        """)
        db.execute("""
            INSERT INTO payments VALUES
                (1, 'credit_card', 10, DATE '2018-01-02'),
                (1, 'coupon', 4, DATE '2018-01-02'),
                (2, 'bank_transfer', 7, DATE '2018-01-03')
        """)
    sql = """
        SELECT payment_method, COUNT(*) AS payment_rows, SUM(amount) AS payment_amount
        FROM payments
        GROUP BY payment_method
        UNION ALL
        SELECT payment_method, COUNT(*) AS payment_rows, SUM(amount) AS payment_amount
        FROM payments
        WHERE payment_method = 'credit_card'
        GROUP BY payment_method
    """

    result = _run(path, sql)

    assert result["columns"] == ["payment_method", "payment_rows", "payment_amount"]
    assert ["信用卡", 1, 10.0] in result["rows"]
    assert len(result["rows"]) == 4


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) AS order_rows FROM orders; SELECT COUNT(*) AS order_rows FROM orders",
    "SELECT COUNT(*) AS order_rows FROM orders EXCEPT SELECT COUNT(*) AS order_rows FROM orders",
    "SELECT COUNT(*) AS order_rows FROM orders UNION SELECT COUNT(*) AS order_rows FROM orders",
])
def test_only_union_all_joins_selects(sql: str):
    with pytest.raises(SqlRejected, match="只允许单条 SELECT 查询"):
        _run(Path("/tmp/union-gate-not-executed.duckdb"), sql)


def test_union_all_still_rejects_an_unauthorized_branch():
    sql = """
        SELECT COUNT(*) AS order_rows FROM orders
        UNION ALL
        SELECT COUNT(*) FROM stg_payments
    """
    with pytest.raises(SqlRejected, match="未授权的表"):
        _run(Path("/tmp/union-gate-not-executed.duckdb"), sql)


def test_payment_rows_alias_requires_payment_count(jaffle_shop_path: Path):
    with pytest.raises(SqlCorrection, match="payment_rows"):
        _run(jaffle_shop_path, "SELECT SUM(amount) AS payment_rows FROM payments")


def test_prior_payment_query_preserves_ordering_with_canonical_alias(jaffle_shop_path: Path):
    sql = """
        SELECT payment_method, COUNT(*) AS payment_count FROM payments
        WHERE order_date BETWEEN DATE '2019-01-01' AND DATE '2019-01-31'
        GROUP BY payment_method ORDER BY payment_count DESC LIMIT 1
    """
    shifted = prior_order_sql(
        sql, load_catalog(ROOT, "orders"), TODAY, shift=lambda day: day.replace(year=day.year - 1),
        joins=authorized_joins(ROOT),
    )

    result = _run(jaffle_shop_path, shifted)

    assert result["columns"] == ["payment_method", "payment_rows"]
    assert result["rows"] == [["信用卡", 15]]


@pytest.mark.parametrize("sql", [
    "SELECT internal_note, COUNT(*) AS order_rows FROM orders GROUP BY internal_note",
    "WITH visible AS (SELECT order_id, internal_note FROM orders) SELECT internal_note FROM visible",
    "WITH visible AS (SELECT order_id FROM orders WHERE internal_note IS NOT NULL) SELECT order_id FROM visible",
])
def test_direct_sql_rejects_fields_outside_space_authorization(tmp_path: Path, sql: str):
    path = tmp_path / "orders.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE orders (order_id INTEGER, internal_note VARCHAR)")

    with pytest.raises(SqlRejected, match="未授权的指标或维度"):
        _run(path, sql)


def test_payment_method_cte_can_use_a_different_grouping(jaffle_shop_path: Path):
    sql = """
        WITH paid AS (
            SELECT order_id, payment_method, COUNT(*) AS payment_rows, SUM(amount) AS payment_amount
            FROM payments
            GROUP BY order_id, payment_method
        )
        SELECT paid.payment_method,
               SUM(paid.payment_rows) AS payment_rows,
               SUM(paid.payment_amount) AS payment_amount
        FROM paid
        JOIN orders AS o ON o.order_id = paid.order_id
        WHERE o.order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
        GROUP BY paid.payment_method
        ORDER BY paid.payment_method
    """

    result = _run(jaffle_shop_path, sql)

    assert result["rows"] == [
        ["银行转账", 33, 411.0],
        ["优惠券", 13, 185.0],
        ["信用卡", 55, 871.0],
        ["礼品卡", 12, 205.0],
    ]


def test_payment_count_joins_orders_for_customer_without_inflating_order_amount(jaffle_shop_path: Path):
    """订单指标在订单表上汇总。支付笔数连接订单只为了取客户标识，不能被当成放大后的订单笔数。"""
    sql = """
        WITH orders_by_customer AS (
            SELECT customer_id, COUNT(*) AS order_rows, SUM(amount) AS order_amount
            FROM orders
            WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
            GROUP BY customer_id
        ),
        payments_by_customer AS (
            SELECT o.customer_id, p.payment_method,
                   COUNT(*) AS payment_rows, SUM(p.amount) AS payment_amount
            FROM orders AS o
            JOIN payments AS p ON o.order_id = p.order_id
            WHERE o.order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
            GROUP BY o.customer_id, p.payment_method
        )
        SELECT o.customer_id, o.order_rows, o.order_amount,
               p.payment_method, p.payment_rows, p.payment_amount
        FROM orders_by_customer AS o
        LEFT JOIN payments_by_customer AS p ON o.customer_id = p.customer_id
    """

    result = _run(jaffle_shop_path, sql)

    seen = {}
    payment_rows = 0
    payment_amount = 0.0
    for customer_id, order_rows, order_amount, _method, rows, amount in result["rows"]:
        assert seen.get(customer_id, (order_rows, order_amount)) == (order_rows, order_amount)
        seen[customer_id] = (order_rows, order_amount)
        payment_rows += rows or 0
        payment_amount += amount or 0.0
    assert sum(item[0] for item in seen.values()) == 99
    assert sum(item[1] for item in seen.values()) == 1672.0
    assert payment_rows == 113
    assert payment_amount == 1672.0


@pytest.mark.parametrize("sql", [
    """
    SELECT COUNT(*) AS payment_rows
    FROM payments AS p
    JOIN orders AS o ON p.order_id = o.customer_id
    """,
    """
    SELECT COUNT(*) AS payment_rows
    FROM orders AS o
    LEFT JOIN payments AS p ON o.order_id = p.order_id
    """,
    """
    SELECT COUNT(*) AS order_rows
    FROM orders AS o
    JOIN payments AS p ON o.order_id = p.order_id
    """,
])
def test_payment_count_still_rejects_multiplied_or_mislabeled_rows(sql: str):
    with pytest.raises(SqlRejected, match="支付明细"):
        _run(Path("/tmp/payment-count-guard-not-executed.duckdb"), sql)


def test_pivoted_payment_counts_are_repaired_instead_of_refused_as_fanout():
    sql = """
        WITH payment_summary AS (
            SELECT o.customer_id, p.payment_method, COUNT(*) AS payment_count
            FROM payments AS p
            JOIN orders AS o ON p.order_id = o.order_id
            GROUP BY o.customer_id, p.payment_method
        )
        SELECT customer_id,
               SUM(CASE WHEN payment_method = 'credit_card' THEN payment_count ELSE 0 END) AS credit_card_count
        FROM payment_summary
        GROUP BY customer_id
    """

    with pytest.raises(SqlCorrection, match="payment_method"):
        _run(Path("/tmp/payment-pivot-not-executed.duckdb"), sql)


def test_bare_payment_join_does_not_execute_inflated_order_amount(jaffle_shop_path: Path):
    sql = """
        SELECT SUM(o.amount) AS order_amount
        FROM orders AS o
        JOIN payments AS p ON p.order_id = o.order_id
        WHERE o.order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
    """

    with pytest.raises(SqlRejected, match="支付明细"):
        _run(jaffle_shop_path, sql)


def test_fanout_guard_follows_a_renamed_order_amount(jaffle_shop_path: Path):
    sql = """
        WITH selected_orders AS (SELECT order_id, amount AS total FROM orders)
        SELECT SUM(o.total) AS order_amount
        FROM selected_orders AS o
        JOIN payments AS p ON p.order_id = o.order_id
    """

    with pytest.raises(SqlRejected, match="支付明细"):
        _run(jaffle_shop_path, sql)


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
    # 碰到支付明细会走灵活校验。未发布的计算列不能因此放行。
    payments_nps = "SELECT CAST(100 AS DOUBLE) AS nps FROM payments"
    invented_nps = """
        WITH paid AS (
            SELECT order_id, SUM(amount) AS payment_amount
            FROM payments
            GROUP BY order_id
        )
        SELECT CAST(
            100 * SUM(CASE WHEN paid.payment_amount >= o.amount THEN 1 ELSE 0 END)
            AS DOUBLE
        ) AS nps
        FROM orders AS o
        JOIN paid ON paid.order_id = o.order_id
    """
    with pytest.raises(SqlRejected, match="未授权的指标或维度"):
        _run(jaffle_shop_path, payments_nps)
    with pytest.raises(SqlRejected, match="未授权的指标或维度"):
        _run(jaffle_shop_path, invented_nps)

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
