from pathlib import Path

import os

import pytest

from app.semantic.catalog import profile_jaffle_shop
from app.semantic.loader import load_semantic_model
from app.semantic.registry import SemanticModelRegistry
from app.semantic.validator import validate_semantic_model


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"


def test_loads_and_publishes_the_real_jaffle_shop_semantic_model(monkeypatch):
    model = load_semantic_model(MODEL_PATH)
    result = validate_semantic_model(model)

    assert result.valid
    assert {metric.id for metric in model.metrics if metric.published} == {
        "orders.order_amount",
        "orders.order_count",
        "orders.credit_card_amount",
        "orders.coupon_amount",
        "orders.bank_transfer_amount",
        "orders.gift_card_amount",
    }
    assert model.dataset("order_mart").grain == ["order_id"]
    assert model.metric("orders.order_count").expression.kind == "count"
    registry = SemanticModelRegistry()
    registry.publish(model)
    assert registry.get("jaffle_shop", "1.0") is model


def test_validator_reports_invalid_references_cycles_and_catalog_mappings():
    model = load_semantic_model(MODEL_PATH)
    invalid = model.model_copy(deep=True)
    invalid.metrics[0].source_dataset_id = "missing_dataset"
    invalid.metrics[1].expression.metric_id = invalid.metrics[1].id
    invalid.dimensions[0].compatible_metric_ids = ["missing_metric"]
    invalid.datasets[0].physical_mapping.object_name = "missing_table"

    result = validate_semantic_model(invalid, catalog_objects={"orders": {"order_id", "order_date"}})

    assert not result.valid
    assert {diagnostic.code for diagnostic in result.errors} >= {
        "unknown_dataset",
        "derived_metric_cycle",
        "unknown_metric",
        "missing_physical_object",
    }
    for diagnostic in result.errors:
        assert diagnostic.object_id
        assert diagnostic.field
        assert diagnostic.reason
        assert diagnostic.suggestion


def test_registry_rejects_a_model_with_hard_errors():
    model = load_semantic_model(MODEL_PATH)
    model.datasets[0].primary_key = ["not_a_field"]

    with pytest.raises(ValueError, match="not published"):
        SemanticModelRegistry().publish(model)


def test_real_catalog_validates_every_physical_mapping_when_available():
    configured_path = os.environ.get("SALES_DUCKDB_PATH")
    if not configured_path:
        pytest.skip("SALES_DUCKDB_PATH is required for the real catalog validation test")
    profile = profile_jaffle_shop(Path(configured_path))
    catalog = {item.name: {column.name for column in item.columns} for item in profile.objects}

    result = validate_semantic_model(load_semantic_model(MODEL_PATH), catalog)

    assert result.valid
