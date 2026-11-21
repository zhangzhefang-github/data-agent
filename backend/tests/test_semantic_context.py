from pathlib import Path

import pytest

from app.semantic.context_builder import build_semantic_context
from app.semantic.loader import load_semantic_model
from app.semantic.models import MetricExpression
from app.semantic.validator import validate_semantic_model


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"
SPACES_PATH = ROOT / "backend" / "configs" / "semantic" / "spaces.yaml"


def test_order_space_omits_a_metric_marked_pending_confirmation(tmp_path: Path):
    model = load_semantic_model(MODEL_PATH)
    pending = model.metric("orders.order_amount").model_copy(deep=True)
    pending.id = "orders.pending_amount"
    pending.name = "待确认金额"
    pending.published = False
    pending.pending_confirmation = True
    model.metrics.append(pending)
    spaces_path = tmp_path / "spaces.yaml"
    spaces_path.write_text(
        SPACES_PATH.read_text().replace(
            "      - orders.order_amount\n",
            "      - orders.order_amount\n      - orders.pending_amount\n",
            1,
        )
    )

    context = build_semantic_context("sales", "orders", model=model, spaces_path=spaces_path)

    assert [metric.id for metric in context.business.metrics] == [
        "orders.order_amount",
        "orders.order_count",
        "orders.credit_card_amount",
        "orders.coupon_amount",
        "orders.bank_transfer_amount",
        "orders.gift_card_amount",
    ]


def test_pending_customer_measures_stay_out_of_the_order_space_query_metrics():
    context = build_semantic_context(
        "sales", "orders", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH
    )

    assert [metric.id for metric in context.business.metrics] == [
        "orders.order_amount",
        "orders.order_count",
        "orders.credit_card_amount",
        "orders.coupon_amount",
        "orders.bank_transfer_amount",
        "orders.gift_card_amount",
    ]
    assert {metric.name for metric in context.business.metrics}.isdisjoint({"客户订单数", "客户生命周期价值"})


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
    assert [dataset.id for dataset in context.sql.datasets] == ["customer_mart", "order_mart"]
    assert [item.id for item in context.sql.relationships] == ["order_mart_customer_mart"]
    assert context.sql.relationships[0].fanout_risk is False
    assert context.sql.relationships[0].join_type == "inner"
    assert context.business.metric_aliases == {"order_rows": "orders.order_count"}
    status = next(dimension for dimension in context.business.dimensions if dimension.id == "orders.status")
    assert status.allowed_values == {"已下单": "placed", "已发货": "shipped", "已完成": "completed", "退货中": "return_pending", "已退货": "returned"}

    serialized = context.model_dump_json()
    for restricted in ("first_name", "last_name", "payment_staging", "raw_orders", "jaffle_duckdb"):
        assert restricted not in serialized
    assert "order_to_payment" not in serialized


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
        "orders.customer_id",
    ]


def test_orders_context_serialization_omits_technical_keys_and_unapproved_fields():
    context = build_semantic_context(
        "sales", "orders", model=load_semantic_model(MODEL_PATH), spaces_path=SPACES_PATH
    )

    payload = context.model_dump()
    datasets = {item["id"]: item for item in payload["sql"]["datasets"]}
    dataset = datasets["order_mart"]
    assert "primary_key" not in dataset
    assert "grain" not in dataset
    assert "base_grain" not in payload["business"]["metrics"][0]
    assert set(dataset["columns"]) == {
        "customer_id",
        "order_date",
        "status",
        "amount",
        "credit_card_amount",
        "coupon_amount",
        "bank_transfer_amount",
        "gift_card_amount",
    }
    assert set(datasets["customer_mart"]["columns"]) == {"customer_id"}
    serialized = context.model_dump_json()
    assert "order_id" not in serialized
    assert payload["sql"]["relationships"][0]["source_fields"] == ["customer_id"]
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


def test_context_omits_dimensions_that_are_not_user_visible(tmp_path: Path):
    spaces_path = tmp_path / "spaces.yaml"
    spaces_path.write_text(
        SPACES_PATH.read_text().replace(
            "      - orders.status\n",
            "      - orders.status\n      - customers.first_order\n",
        ).replace(
            "      - order_mart\n",
            "      - order_mart\n      - customer_mart\n",
        )
    )

    context = build_semantic_context(
        "sales", "orders", model=load_semantic_model(MODEL_PATH), spaces_path=spaces_path
    )

    assert [dimension.id for dimension in context.business.dimensions] == [
        "orders.order_date",
        "orders.status",
        "orders.customer_id",
    ]


def test_listed_restricted_dataset_keeps_authorized_columns_and_drops_staging(tmp_path: Path):
    model = load_semantic_model(MODEL_PATH)
    next(item for item in model.dimensions if item.id == "customers.first_order").user_visible = True
    spaces_path = tmp_path / "spaces.yaml"
    spaces_path.write_text(
        SPACES_PATH.read_text().replace(
            "      - orders.status\n",
            "      - orders.status\n      - customers.first_order\n",
        ).replace(
            "      - customer_mart\n",
            "      - customer_mart\n      - customer_staging\n",
        )
    )

    context = build_semantic_context("sales", "orders", model=model, spaces_path=spaces_path)

    assert "customers.first_order" in [dimension.id for dimension in context.business.dimensions]
    assert "customer_mart" in [dataset.id for dataset in context.sql.datasets]
    assert "customer_staging" not in [dataset.id for dataset in context.sql.datasets]
    assert "first_name" not in context.model_dump_json()


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
