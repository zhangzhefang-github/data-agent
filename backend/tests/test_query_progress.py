from app.query_progress import describe_sql, describe_structured


def test_payment_breakdown_uses_dates_measures_and_order():
    sql = """
    SELECT payment_method, COUNT(*) AS payment_rows, SUM(amount) AS payment_amount
    FROM payments
    WHERE order_date >= DATE '2018-01-01' AND order_date < DATE '2018-04-09'
    GROUP BY payment_method
    ORDER BY payment_amount DESC
    """
    assert describe_sql(sql) == "2018-01-01 至 2018-04-09的支付笔数和金额，按支付方式，按结果排序"


def test_schema_qualified_orders_keep_the_status_breakdown():
    sql = "SELECT status, COUNT(*) AS order_rows FROM main.orders GROUP BY status LIMIT 500"
    assert describe_sql(sql) == "订单笔数，按订单状态"


def test_monthly_orders_are_named_as_a_trend():
    sql = "SELECT date_trunc('month', order_date) AS month, SUM(amount) AS order_amount FROM orders GROUP BY 1"
    assert describe_sql(sql) == "订单月度趋势"


def test_structured_query_uses_catalog_names():
    catalog = {
        "metrics": {"sales_amount": {"name": "销售额"}},
        "dimensions": {"city": {"name": "城市"}},
    }
    assert describe_structured(
        ["sales_amount"], ["city"], "2026-01-01", "2026-11-23", catalog,
    ) == "2026-01-01 至 2026-11-23的销售额，按城市"
