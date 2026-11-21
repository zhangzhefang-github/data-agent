from app.query_scope import is_query_scope_question, render_query_scope
from app.semantic.context_builder import build_semantic_context
from app.semantic.loader import load_semantic_model

from test_semantic_context import MODEL_PATH, SPACES_PATH


def test_orders_scope_projects_authorized_datasets_and_the_customer_join():
    context = build_semantic_context(
        "sales", "orders", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH,
    )

    text = render_query_scope(context)

    assert text.startswith("可查询的数据范围\n这是订单主题空间。")
    assert "可以按订单日期、订单状态、客户标识筛选或分组" in text
    assert "1. 订单汇总（orders）" in text
    assert "可查询订单金额、订单笔数、信用卡金额、优惠券金额、银行转账金额、礼品卡金额" in text
    assert "不要再与 amount 相加" in text
    assert "每笔订单一笔，包含全部状态" in text
    assert "订单状态包括已下单、已发货、已完成、退货中、已退货" in text
    assert "2. 客户汇总（customers）。当前可关联的列是客户标识。" in text
    assert "订单汇总与客户汇总按客户标识等值内连接，多对一，不会放大行数。" in text
    assert "用户询问客户姓名时，用客户标识回答，并说明姓名未授权。" in text
    assert "可以这样问\n某个日期区间的订单笔数和订单金额" in text
    assert text.endswith("直接说出要看的时间、状态或客户即可查询。")
    for hidden in ("first_name", "last_name", "order_id", "payment", "stg_", "raw_"):
        assert hidden not in text


def test_scope_question_recognizes_the_capability_ask_only():
    assert is_query_scope_question("  请问我能查询哪些信息？ ")
    assert is_query_scope_question("能查什么")
    assert not is_query_scope_question("查询订单金额")
    assert not is_query_scope_question("每个客户有多少笔订单")
    assert not is_query_scope_question("去年同期呢")
