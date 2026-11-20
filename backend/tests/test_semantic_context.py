from pathlib import Path

import pytest

from app.semantic.context_builder import build_semantic_context
from app.semantic.loader import load_semantic_model
from app.semantic.models import MetricExpression
from app.semantic.validator import validate_semantic_model


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


def test_orders_context_serialization_omits_technical_keys_and_unapproved_fields():
    context = build_semantic_context(
        "sales", "orders", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH
    )

    payload = context.model_dump()
    dataset = payload["sql"]["datasets"][0]
    assert "primary_key" not in dataset
    assert "grain" not in dataset
    assert "base_grain" not in payload["business"]["metrics"][0]
    assert set(dataset["columns"]) == {
        "order_date",
        "status",
        "amount",
        "credit_card_amount",
        "coupon_amount",
        "bank_transfer_amount",
        "gift_card_amount",
    }
    serialized = context.model_dump_json()
    assert "order_id" not in serialized
    assert "customer_id" not in serialized
    assert context.business.metrics[0].base_grain == ["order_id"]


def test_default_context_rejects_a_model_that_cannot_be_published(tmp_path: Path):
    semantic = tmp_path / "semantic"
    semantic.mkdir()
    model_text = MODEL_PATH.read_text().replace("primary_key: [order_id]", "primary_key: [not_a_field]", 1)
    model_path = semantic / "jaffle_shop.yaml"
    model_path.write_text(model_text)
    spaces_path = semantic / "spaces.yaml"
    spaces_path.write_text(SPACES_PATH.read_text())

    with pytest.raises(ValueError, match="not published"):
        build_semantic_context("sales", "orders", model_path=model_path, spaces_path=spaces_path)


def test_orders_context_keeps_dimension_only_match_from_adding_every_metric():
    context = build_semantic_context(
        "sales", "orders", "订单状态", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH
    )

    assert [metric.id for metric in context.business.metrics] == []
    assert [dimension.id for dimension in context.business.dimensions] == ["orders.status"]


def test_orders_context_accepts_legacy_alias_when_selecting_a_metric():
    context = build_semantic_context(
        "sales", "orders", "order_rows", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH
    )

    assert [metric.id for metric in context.business.metrics] == ["orders.order_count"]
    assert [dimension.id for dimension in context.business.dimensions] == ["orders.order_date"]


def test_space_rejects_expression_kinds_the_query_runtime_cannot_compile(tmp_path: Path):
    model = load_semantic_model(MODEL_PATH)
    derived = model.metric("orders.order_amount").model_copy(deep=True)
    derived.id = "orders.amount_per_order"
    derived.expression = MetricExpression(
        kind="ratio",
        numerator_metric_id="orders.order_amount",
        denominator_metric_id="orders.order_count",
    )
    model.metrics.append(derived)
    assert validate_semantic_model(model).valid

    spaces_path = tmp_path / "spaces.yaml"
    spaces_path.write_text(
        SPACES_PATH.read_text().replace(
            "      - orders.order_amount\n",
            "      - orders.order_amount\n      - orders.amount_per_order\n",
            1,
        )
    )

    with pytest.raises(ValueError, match="unsupported expression kind"):
        build_semantic_context("sales", "orders", model=model, spaces_path=spaces_path)


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
