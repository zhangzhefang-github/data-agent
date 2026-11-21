import json
from datetime import datetime
from types import SimpleNamespace

import duckdb
import pytest
from agents.tool_context import ToolContext
from fastapi.testclient import TestClient

from app import main
from app.query import load_catalog

TOTALS = "SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount FROM orders"
HISTORICAL = TOTALS + " WHERE order_date BETWEEN '2018-01-01' AND '2018-12-31'"


@pytest.fixture
def order_chat(monkeypatch, tmp_path):
    path = tmp_path / "orders.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("""
            CREATE TABLE orders (
                order_date DATE, status VARCHAR, amount DOUBLE, credit_card_amount DOUBLE,
                coupon_amount DOUBLE, bank_transfer_amount DOUBLE, gift_card_amount DOUBLE,
                customer_id INTEGER
            )
        """)
        db.execute("""
            INSERT INTO orders VALUES
                ('2017-01-01', 'completed', 7, 7, 0, 0, 0, 12),
                ('2017-02-28', 'placed', 90, 90, 0, 0, 0, 13),
                ('2018-01-01', 'completed', 10, 4, 2, 1, 3, 14),
                ('2018-02-01', 'placed', 20, 14, 3, 2, 1, 15),
                ('2018-03-01', 'returned', 5, 5, 0, 0, 0, 16),
                ('2023-02-28', 'completed', 8, 8, 0, 0, 0, 17),
                ('2023-03-01', 'completed', 100, 100, 0, 0, 0, 18),
                ('2023-02-28', 'placed', 100, 100, 0, 0, 0, 19)
        """)
        db.execute("""
            INSERT INTO orders
            SELECT DATE '2010-01-01' + CAST(i AS INTEGER), 'completed', 1, 1, 0, 0, 0, 20
            FROM range(600) AS days(i)
        """)
        db.execute("CREATE TABLE customers (customer_id INTEGER)")
        db.execute("INSERT INTO customers VALUES (12), (13), (14), (15), (16), (17), (18), (19), (20)")

    class BusinessClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2018, 2, 28, 12, tzinfo=tz)

    monkeypatch.setattr(main, "datetime", BusinessClock)
    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    monkeypatch.setattr(main, "CATALOG", load_catalog(main.ROOT, "orders"))
    monkeypatch.setattr(main, "DUCKDB_PATH", str(path))
    monkeypatch.setattr(main, "DATABASE_URL", None)

    def ask(client, candidates, message="按业务口径查询订单", conversation_id=None):
        replies = []

        class Run:
            async def stream_events(self):
                for index, sql in enumerate(candidates):
                    arguments = json.dumps({"sql": sql})
                    context = ToolContext(None, tool_name=tool.name, tool_call_id=str(index), tool_arguments=arguments)
                    replies.append(json.loads(await tool.on_invoke_tool(context, arguments)))
                yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(
                    type="response.output_text.delta", delta="完成",
                ))

        def runner(agent, prompt, **kwargs):
            nonlocal tool
            tool = next(item for item in agent.tools if item.name == "execute_direct_sql")
            assert not any(item.name == "execute_semantic_query" for item in agent.tools)
            assert "方言版本：v" in prompt
            assert "sales_transactions" in prompt
            assert "WITH weekly_sales" not in prompt
            assert "不要使用 WITH" in prompt
            assert "本次授权的表：" in prompt
            authorized = prompt.split("本次授权的表：", 1)[1].split("授权关系：", 1)[0]
            objects = prompt.split("业务对象：", 1)[1].split("本次授权的表：", 1)[0]
            assert "main.orders" in authorized
            assert "main.customers" in authorized
            assert "customer_id" in authorized
            assert "first_name" not in prompt
            assert "last_name" not in prompt
            relations = prompt.split("授权关系：", 1)[1].split("授权指标：", 1)[0]
            assert "order_mart_customer_mart" in relations
            assert "'source': 'orders'" in relations
            assert "'target': 'customers'" in relations
            assert "many_to_one" in relations
            assert "order_to_payment" not in relations
            assert "'id': 'order'" in objects
            assert "'id': 'customer'" in objects
            dialect_rules = prompt.split("方言规则：", 1)[1].split("经典示例", 1)[0]
            assert "order_date" not in dialect_rules
            assert "只能原样写 order_date" not in prompt
            assert "不要 JOIN" not in prompt
            assert "不要另起别名" in prompt
            assert "本次执行 SQL" in agent.instructions
            assert "第一季度是 1–3 月" in prompt
            assert "不要再与 amount 相加" in prompt
            return Run()

        tool = None
        monkeypatch.setattr(main.Runner, "run_streamed", runner)
        payload = {"message": message, "conversation_id": conversation_id}
        response = client.post("/api/chat/stream", json=payload)
        assert response.status_code == 200
        events = dict(
            (kind.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
            for part in response.text.strip().split("\n\n")
            for kind, data in [part.split("\n", 1)]
        )
        return events, replies

    return ask


JOINED = (
    "SELECT COUNT(*) AS order_rows FROM orders "
    "JOIN customers ON orders.customer_id = customers.customer_id"
)


@pytest.mark.parametrize(("sql", "columns", "rows"), [
    (HISTORICAL, ["order_rows", "order_amount"], [[3, 35.0]]),
    (JOINED, ["order_rows"], [[608]]),
    (TOTALS, ["order_rows", "order_amount"], [[608, 940.0]]),
    (TOTALS + " WHERE order_date = '2019-01-01'", ["order_rows", "order_amount"], []),
    (
        "SELECT status, SUM(credit_card_amount) AS credit_card_amount, SUM(coupon_amount) AS coupon_amount, "
        "SUM(bank_transfer_amount) AS bank_transfer_amount, SUM(gift_card_amount) AS gift_card_amount "
        "FROM main.orders o WHERE o.status IN ('已下单', '已完成') AND o.order_date BETWEEN '2018-01-01' AND '2018-12-31' "
        "GROUP BY o.status ORDER BY credit_card_amount DESC LIMIT 1",
        ["status", "credit_card_amount", "coupon_amount", "bank_transfer_amount", "gift_card_amount"],
        [["已下单", 14.0, 3.0, 2.0, 1.0]],
    ),
])
def test_order_chat_golden_results(order_chat, sql, columns, rows):
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [sql])
    assert events["done"]["status"] == "completed"
    assert events["result"]["columns"] == columns
    assert events["result"]["rows"] == rows
    assert set(events["result"]) == {"columns", "rows", "sql", "source", "elapsed_ms"}


def test_explicit_inner_join_counts_orders_that_match_a_customer(order_chat):
    sql = (
        "SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount "
        "FROM main.orders "
        "INNER JOIN main.customers ON main.orders.customer_id = main.customers.customer_id "
        "WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-03-01'"
    )
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [sql])
    assert events["done"]["status"] == "completed"
    assert events["result"]["rows"] == [[3, 35.0]]


def test_schema_qualified_customer_join_counts_orders_in_the_window(order_chat):
    sql = (
        "SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount "
        "FROM main.orders "
        "JOIN main.customers ON main.orders.customer_id = main.customers.customer_id "
        "WHERE main.orders.order_date BETWEEN DATE '2018-01-01' AND DATE '2018-03-01'"
    )
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [sql])
    assert events["done"]["status"] == "completed"
    assert events["result"]["rows"] == [[3, 35.0]]


def test_group_by_customer_identifier_sums_back_to_the_order_count(order_chat):
    sql = (
        "SELECT customer_id, COUNT(*) AS order_rows FROM orders "
        "WHERE order_date BETWEEN '2018-01-01' AND '2018-03-01' "
        "GROUP BY customer_id ORDER BY customer_id"
    )
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [sql])
    assert events["done"]["status"] == "completed"
    assert events["result"]["rows"] == [[14, 1], [15, 1], [16, 1]]
    assert sum(row[1] for row in events["result"]["rows"]) == 3


def test_aliased_customer_order_join_keeps_order_count_and_amount(order_chat):
    sql = (
        "SELECT COUNT(*) AS order_rows, SUM(o.amount) AS order_amount "
        "FROM orders AS o JOIN customers AS c ON c.customer_id = o.customer_id"
    )
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [sql])
    assert events["done"]["status"] == "completed"
    assert events["result"]["columns"] == ["order_rows", "order_amount"]
    assert events["result"]["rows"] == [[608, 940.0]]


@pytest.mark.parametrize("candidate", [
    HISTORICAL + "; SELECT 1",
    "DELETE FROM orders",
    "SELECT COUNT(*) AS order_rows FROM customers",
    "SELECT COUNT(*) AS order_rows FROM orders JOIN customers ON TRUE",
    "SELECT COUNT(*) AS order_rows FROM orders LEFT JOIN customers ON orders.customer_id = customers.customer_id",
    "SELECT COUNT(*) AS order_rows FROM orders JOIN payments ON orders.order_id = payments.order_id",
    "SELECT first_name, COUNT(*) AS order_rows FROM orders",
    "SELECT COUNT(*) AS order_rows FROM range(10)",
])
def test_failed_repair_is_checked_for_authorization(order_chat, candidate):
    wrong = "SELECT SUM(amount) AS order_rows FROM orders"
    with TestClient(main.app) as client:
        events, replies = order_chat(client, [wrong, candidate, HISTORICAL])
    assert replies[0]["remaining_repairs"] == 2
    assert len(replies) == 1
    assert events["done"]["status"] == "failed"
    assert "result" not in events


def test_query_repairs_are_bounded(order_chat):
    wrong = "SELECT SUM(amount) AS order_rows FROM orders"
    with TestClient(main.app) as client:
        failed, replies = order_chat(client, [wrong, wrong, wrong, HISTORICAL])
        success, repaired = order_chat(client, [wrong, wrong, HISTORICAL])
    assert [reply["remaining_repairs"] for reply in replies] == [2, 1]
    assert failed["done"]["status"] == "failed"
    assert "result" not in failed
    assert repaired[-1]["rows"] == [[3, 35.0]]
    assert success["result"]["rows"] == [[3, 35.0]]


def test_order_metric_alias_can_be_repaired(order_chat):
    guessed_aliases = HISTORICAL.replace("AS order_rows", "AS order_count").replace(
        "AS order_amount", "AS total_amount",
    )
    with TestClient(main.app) as client:
        events, replies = order_chat(
            client, [guessed_aliases, HISTORICAL],
            "2018 年有多少笔订单，订单金额是多少？",
        )
    assert replies[0]["remaining_repairs"] == 2
    assert "order_rows" in replies[0]["error"]
    assert "order_amount" in replies[0]["error"]
    assert events["result"]["columns"] == ["order_rows", "order_amount"]
    assert events["result"]["rows"] == [[3, 35.0]]
    assert events["done"]["status"] == "completed"


def test_rows_are_capped_before_returning_results(order_chat):
    sql = (
        "SELECT order_date, COUNT(*) AS order_rows FROM orders "
        "WHERE order_date BETWEEN '2010-01-01' AND '2012-12-31' "
        "GROUP BY order_date ORDER BY order_date LIMIT 600"
    )
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [sql])
    assert len(events["result"]["rows"]) == 500
    assert events["result"]["rows"][0] == ["2010-01-01", 1]


def test_order_query_timeout_preserves_last_success(order_chat, monkeypatch):
    connect = duckdb.connect

    class InterruptingConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql):
            if "2019-01-01" in sql:
                raise duckdb.InterruptException("driver diagnostic")
            return self.connection.execute(sql)

    def interrupting_connect(*args, **kwargs):
        return InterruptingConnection(connect(*args, **kwargs))

    with TestClient(main.app) as client:
        first, _ = order_chat(client, [HISTORICAL])
        assert first["result"]["rows"] == [[3, 35.0]]
        conversation_id = first["conversation"]["conversation_id"]
        route = f"/api/conversations/{conversation_id}"
        last_query = client.get(route).json()["last_query"]
        monkeypatch.setattr(duckdb, "connect", interrupting_connect)
        timed_out, _ = order_chat(
            client, [TOTALS + " WHERE order_date = '2019-01-01'", HISTORICAL],
            conversation_id=conversation_id,
        )
        assert client.get(route).json()["last_query"] == last_query

    assert timed_out["error"] == {"code": "query_timeout", "message": "订单查询超时"}
    assert timed_out["done"]["status"] == "failed"
    assert "result" not in timed_out


def test_direct_follow_up_keeps_last_success_after_failure_and_shifts_leap_day(order_chat):
    sql = (
        "SELECT COUNT(*) AS order_rows FROM orders "
        "WHERE order_date BETWEEN '2024-02-01' AND '2024-02-29' AND status = '已完成'"
    )
    with TestClient(main.app) as client:
        first, _ = order_chat(client, [sql])
        conversation_id = first["conversation"]["conversation_id"]
        route = f"/api/conversations/{conversation_id}"
        before = client.get(route).json()
        failed, _ = order_chat(client, ["DELETE FROM orders"], conversation_id=conversation_id)
        assert failed["done"]["status"] == "failed"
        assert client.get(route).json()["last_query"] == before["last_query"]
        events, _ = order_chat(client, [HISTORICAL], "去年同期呢？", conversation_id)
        after = client.get(route).json()
    assert events["result"]["rows"] == [[1]]
    assert "2023-02-28" in events["result"]["sql"]
    assert after["messages"][1]["result"] == first["result"]
    assert after["last_query"]["mode"] == "orders_duckdb"


def test_direct_follow_up_uses_prior_authorized_sql(order_chat):
    with TestClient(main.app) as client:
        first, _ = order_chat(client, [HISTORICAL + " AND status = '已完成' ORDER BY order_amount DESC"])
        conversation_id = first["conversation"]["conversation_id"]
        shifted, _ = order_chat(client, [HISTORICAL], "去年同期", conversation_id)
        unconnected, _ = order_chat(client, [HISTORICAL], "去年同期")
    assert first["result"]["rows"] == [[1, 10.0]]
    assert shifted["result"]["rows"] == [[1, 7.0]]
    assert "result" not in unconnected
    assert "请先提供" in unconnected["text"]["delta"]
