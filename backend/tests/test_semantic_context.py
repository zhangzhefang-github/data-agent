from pathlib import Path

import pytest

from app.semantic.context_builder import build_semantic_context
from app.semantic.loader import load_semantic_model


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"
SPACES_PATH = ROOT / "backend" / "configs" / "semantic" / "spaces.yaml"


def test_orders_context_selects_authorized_terms_and_serializes_without_sensitive_data():
    context = build_semantic_context(
        "sales",
        "orders",
        "今年各订单状态的订单金额和订单笔数",
        model=load_semantic_model(MODEL_PATH),
        spaces_path=SPACES_PATH,
    )

    assert context.model_id == "jaffle_shop"
    assert [metric.id for metric in context.business.metrics] == [
        "orders.order_amount",
        "orders.order_count",
    ]
    assert [dimension.id for dimension in context.business.dimensions] == [
        "orders.order_date",
        "orders.status",
    ]
    assert [dataset.id for dataset in context.sql.datasets] == ["order_mart"]
    assert context.sql.relationships == []
    assert context.business.metric_aliases == {"order_rows": "orders.order_count"}
    status = next(dimension for dimension in context.business.dimensions if dimension.id == "orders.status")
    assert status.allowed_values == {"已下单": "placed", "已发货": "shipped", "已完成": "completed", "退货中": "return_pending", "已退货": "returned"}

    serialized = context.model_dump_json()
    for restricted in ("first_name", "last_name", "customer_mart", "payment_staging", "raw_orders", "jaffle_duckdb"):
        assert restricted not in serialized


def test_orders_context_without_matches_returns_the_authorized_catalog_in_stable_order():
    model = load_semantic_model(MODEL_PATH)

    context = build_semantic_context("sales", "orders", "无法匹配的提问", model=model, spaces_path=SPACES_PATH)

    assert [metric.id for metric in context.business.metrics] == [
        "orders.order_amount",
        "orders.order_count",
        "orders.credit_card_amount",
        "orders.coupon_amount",
        "orders.bank_transfer_amount",
        "orders.gift_card_amount",
    ]
    assert [dimension.id for dimension in context.business.dimensions] == [
        "orders.order_date",
        "orders.status",
    ]


def test_orders_context_accepts_legacy_alias_when_selecting_a_metric():
    context = build_semantic_context(
        "sales", "orders", "order_rows", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH
    )

    assert [metric.id for metric in context.business.metrics] == ["orders.order_count"]
    assert [dimension.id for dimension in context.business.dimensions] == ["orders.order_date"]


@pytest.mark.parametrize(
    ("domain_id", "space_id", "message"),
    [
        ("finance", "orders", "Unknown semantic domain"),
        ("sales", "customers", "Unknown semantic space"),
        ("finance", "customers", "Unknown semantic domain"),
    ],
)
def test_context_rejects_unknown_domain_and_space(domain_id, space_id, message):
    with pytest.raises(ValueError, match=message):
        build_semantic_context(domain_id, space_id, model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH)
