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
        # 客户标识有值，但客户表没有这一行。不能把非空当成客户记录存在。
        db.execute("INSERT INTO orders VALUES ('2016-06-01', 'placed', 1, 1, 0, 0, 0, 999)")
        db.execute("ALTER TABLE orders ADD COLUMN order_id INTEGER")
        db.execute("""
            CREATE TABLE payments (
                order_id INTEGER,
                payment_method VARCHAR,
                amount DOUBLE,
                order_date DATE
            )
        """)

    class BusinessClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2018, 2, 28, 12, tzinfo=tz)

    monkeypatch.setattr(main, "datetime", BusinessClock)
    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    monkeypatch.setattr(main, "CATALOG", load_catalog(main.ROOT, "orders"))
    monkeypatch.setattr(main, "DUCKDB_PATH", str(path))
    monkeypatch.setattr(main, "DATABASE_URL", None)

    def ask(client, candidates, message="按业务口径查询订单", conversation_id=None, check_prompt=True):
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
            assert [item.name for item in agent.tools] == ["execute_direct_sql"]
            if not check_prompt:
                return Run()
            assert "方言版本：v" in prompt
            assert "sales_transactions" in prompt
            assert "WITH weekly_sales" not in prompt
            assert "可以用 WITH、子查询、JOIN、GROUP BY 和窗口函数" in prompt
            assert "不要使用 WITH" not in prompt
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
            assert "'join_type': 'inner'" in relations
            assert "不会放大行数" in prompt
            assert "INTEGER" in authorized
            assert "order_to_payment" not in relations
            assert "'id': 'order'" in objects
            assert "'id': 'customer'" in objects
            dialect_rules = prompt.split("方言规则：", 1)[1].split("经典示例", 1)[0]
            assert "order_date" not in dialect_rules
            assert "只能原样写 order_date" not in prompt
            assert "不要 JOIN" not in prompt
            assert "不要另起别名" in prompt
            assert "不要单独查询客户表" in prompt
            assert "可查询的数据范围" in prompt
            assert "客户汇总" in prompt
            assert "等值内连接" in prompt
            assert "只回复下面这段原文" in prompt
            assert "可查询范围原文" in agent.instructions
            assert "本次执行 SQL" in agent.instructions
            assert "第一季度是 1–3 月" in prompt
            assert "不要再与 amount 相加" in prompt
            assert "基础粒度是单笔订单" in prompt
            assert "支付明细要先按 order_id 汇总" in prompt
            assert "main.payments" in authorized
            assert "stg_payments" not in prompt
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
    (TOTALS, ["order_rows", "order_amount"], [[609, 941.0]]),
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


@pytest.mark.parametrize("captured", [
    "SELECT COUNT(customer.customer_id) AS customer_count FROM main.customers customer "
    "INNER JOIN main.orders order_ ON customer.customer_id = order_.customer_id",
    "SELECT COUNT(DISTINCT orders.customer_id) AS customer_count FROM main.orders "
    "JOIN main.customers ON orders.customer_id = customers.customer_id",
    "SELECT COUNT(customer_id) AS customer_count FROM main.customers",
])
def test_customer_count_shape_can_be_repaired_into_a_customer_grouping(order_chat, captured):
    repaired = "SELECT customer_id, COUNT(*) AS order_rows FROM orders GROUP BY customer_id"
    with TestClient(main.app) as client:
        events, replies = order_chat(client, [captured, repaired], "帮我查一下目前有多少客户")
    assert "只允许访问已授权的订单表" not in replies[0]["error"]
    assert "客户标识" in replies[0]["error"]
    assert events["done"]["status"] == "completed"
    assert events["result"]["columns"] == ["customer_id", "order_rows"]
    assert "error" not in events


def test_scope_question_returns_the_authorized_range_without_the_model(order_chat, monkeypatch):
    def fail_runner(*args, **kwargs):
        raise AssertionError("能力问题不应调用模型")

    monkeypatch.setattr(main.Runner, "run_streamed", fail_runner)
    with TestClient(main.app) as client:
        response = client.post("/api/chat/stream", json={"message": "请问我能查询哪些信息？"})
        events = dict(
            (kind.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
            for part in response.text.strip().split("\n\n")
            for kind, data in [part.split("\n", 1)]
        )
        stored = client.get(f"/api/conversations/{events['conversation']['conversation_id']}").json()

    text = events["text"]["delta"]
    assert events["done"]["status"] == "completed"
    assert "result" not in events
    assert text.startswith("可查询的数据范围")
    assert "订单汇总（orders）" in text
    assert "客户汇总（customers）" in text
    assert "等值内连接" in text
    assert "某个日期区间的订单笔数和订单金额" in text
    assert "first_name" not in text
    assert "支付明细（payments）" in text
    assert "关联可能放大行数" in text
    assert "stg_" not in text
    assert stored["messages"][1]["content"] == text
    assert stored["messages"][1]["result"] is None


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


def test_customer_presence_requires_the_equijoin_and_keeps_the_prior_query(order_chat):
    presence = (
        "SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount FROM orders "
        "WHERE customer_id IS NOT NULL"
    )
    joined = (
        "SELECT COUNT(*) AS order_rows, SUM(amount) AS order_amount FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id"
    )
    with TestClient(main.app) as client:
        first, _ = order_chat(client, [HISTORICAL])
        conversation_id = first["conversation"]["conversation_id"]
        route = f"/api/conversations/{conversation_id}"
        before = client.get(route).json()["last_query"]
        rejected, replies = order_chat(client, [presence], conversation_id=conversation_id)
        assert client.get(route).json()["last_query"] == before
        events, repaired = order_chat(client, [presence, joined], conversation_id=conversation_id)
    assert "等值内连接" in replies[0]["error"]
    assert rejected["done"]["status"] == "failed"
    assert "result" not in rejected
    assert repaired[0]["remaining_repairs"] == 2
    assert events["result"]["rows"] == [[608, 940.0]]


def test_joined_customer_identifier_groups_without_fanout_and_names_the_top_customer(order_chat):
    grouped = (
        "SELECT c.customer_id, COUNT(*) AS order_rows, SUM(o.amount) AS order_amount "
        "FROM orders AS o INNER JOIN customers AS c ON o.customer_id = c.customer_id "
        "GROUP BY c.customer_id"
    )
    top = (
        "SELECT c.customer_id, SUM(o.amount) AS order_amount "
        "FROM orders AS o INNER JOIN customers AS c ON o.customer_id = c.customer_id "
        "GROUP BY c.customer_id ORDER BY order_amount DESC LIMIT 1"
    )
    with TestClient(main.app) as client:
        grouped_events, _ = order_chat(client, [grouped])
        top_events, _ = order_chat(client, [top])
    rows = grouped_events["result"]["rows"]
    assert sum(row[1] for row in rows) == 608
    assert sum(row[2] for row in rows) == 940.0
    assert all(row[0] != 999 for row in rows)
    assert top_events["result"]["rows"] == [[20, 600.0]]
    assert "first_name" not in top_events["result"]["sql"]
    assert "last_name" not in top_events["result"]["sql"]


def test_direct_sql_loads_the_semantic_model_once_per_request(order_chat, monkeypatch):
    from app.semantic import context_builder, snapshot as snapshot_module

    calls = {"n": 0}
    original = snapshot_module.load_semantic_model

    def counted(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(snapshot_module, "load_semantic_model", counted)
    monkeypatch.setattr(
        context_builder,
        "load_semantic_model",
        lambda path: (_ for _ in ()).throw(AssertionError("请求过程中重新加载了语义模型")),
    )
    with TestClient(main.app) as client:
        events, _ = order_chat(client, [TOTALS])
    assert events["done"]["status"] == "completed"
    assert calls["n"] == 1


def test_large_space_supplements_an_authorized_dataset_then_revalidates(order_chat, monkeypatch):
    monkeypatch.setattr(main, "is_small_space", lambda context: False)
    wrong = "SELECT SUM(amount) AS order_rows FROM orders"
    with TestClient(main.app) as client:
        events, replies = order_chat(
            client,
            [JOINED, wrong, HISTORICAL],
            "订单笔数是多少",
            check_prompt=False,
        )
    assert replies[0]["added_datasets"] == ["customer_mart"]
    assert replies[0]["remaining_coverage"] == 1
    assert replies[0]["context"]["relationships"][0]["fanout_risk"] is False
    assert "first_name" not in json.dumps(replies[0]["context"], ensure_ascii=False)
    assert replies[1]["remaining_repairs"] == 2
    assert events["result"]["rows"] == [[3, 35.0]]
    assert events["done"]["status"] == "completed"


def test_large_space_still_rejects_an_unauthorized_dataset(order_chat, monkeypatch):
    monkeypatch.setattr(main, "is_small_space", lambda context: False)
    payments = "SELECT COUNT(*) AS order_rows FROM orders JOIN stg_payments ON orders.order_id = stg_payments.order_id"
    with TestClient(main.app) as client:
        events, replies = order_chat(client, [payments, HISTORICAL], "订单笔数是多少", check_prompt=False)
    assert replies == []
    assert events["done"]["status"] == "failed"
    assert "result" not in events


def test_context_coverage_stops_after_two_supplements(order_chat, monkeypatch):
    monkeypatch.setattr(main, "is_small_space", lambda context: False)
    monkeypatch.setattr(main, "context_coverage_gap", lambda sql, authorization, presentation: ["customer_mart"])
    with TestClient(main.app) as client:
        events, replies = order_chat(client, [JOINED, JOINED, JOINED], "订单笔数是多少", check_prompt=False)
    assert [reply["remaining_coverage"] for reply in replies] == [1, 0]
    assert events["done"]["status"] == "failed"
    assert "result" not in events


def test_terminal_refusal_is_shared_with_history(order_chat):
    with TestClient(main.app) as client:
        events, _ = order_chat(client, ["SELECT secret FROM orders"])
        assert events['done']['outcome']['status'] == 'refused'
        history = client.get('/api/conversations/' + events['conversation']['conversation_id']).json()
        assert history['messages'][-1]['outcome'] == events['done']['outcome']
        assert history['messages'][-1]['result'] is None


def test_clarification_outcome_overrides_partial_text_and_persists(order_chat, monkeypatch):
    class Run:
        async def stream_events(self):
            yield SimpleNamespace(type='raw_response_event', data=SimpleNamespace(type='response.output_text.delta', delta='正在查询'))
            args = json.dumps({'sql': '', 'clarification': '客户姓名不可查，请确认是否只查订单金额。'})
            context = ToolContext(None, tool_name=tool.name, tool_call_id='clarify', tool_arguments=args)
            await tool.on_invoke_tool(context, args)

    def runner(agent, prompt, **kwargs):
        nonlocal tool
        tool = agent.tools[0]
        return Run()

    tool = None
    monkeypatch.setattr(main.Runner, 'run_streamed', runner)
    with TestClient(main.app) as client:
        from app.evaluation.runner import send
        response = send(client, '按客户姓名查询订单金额')
        assert response['outcome'] == {'status': 'clarification_required', 'reason': '客户姓名不可查，请确认是否只查订单金额。'}
        history = client.get('/api/conversations/' + response['conversation_id']).json()
        assert history['messages'][-1]['outcome'] == response['outcome']
        assert history['messages'][-1]['result'] is None
