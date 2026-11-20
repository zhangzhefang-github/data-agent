from pathlib import Path

import os

import pytest

from app.semantic.catalog import profile_jaffle_shop
from app.semantic.loader import load_semantic_model
from app.semantic.models import MetricFilter
from app.semantic.registry import SemanticModelRegistry
from app.semantic.validator import validate_dataset_join, validate_semantic_model


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"


def test_published_model_marks_raw_to_staging_lineage_confirmed():
    registry = SemanticModelRegistry()
    registry.publish(load_semantic_model(MODEL_PATH))
    published = registry.get("jaffle_shop", "1.0")

    confirmed = {
        (record.source_dataset_id, record.target_dataset_id): record.verification_status
        for record in published.dataset_lineage
        if record.verification_status == "confirmed"
    }
    assert confirmed == {
        ("customer_source", "customer_staging"): "confirmed",
        ("order_source", "order_staging"): "confirmed",
        ("payment_source", "payment_staging"): "confirmed",
    }


def test_published_model_marks_mart_lineage_proposed_not_confirmed():
    registry = SemanticModelRegistry()
    registry.publish(load_semantic_model(MODEL_PATH))
    published = registry.get("jaffle_shop", "1.0")

    mart = {
        (record.source_dataset_id, record.target_dataset_id): record.verification_status
        for record in published.dataset_lineage
        if record.target_dataset_id in {"customer_mart", "order_mart"}
    }
    assert mart == {
        ("customer_staging", "customer_mart"): "proposed",
        ("order_staging", "order_mart"): "proposed",
    }


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


def test_profiled_relationship_cannot_be_used_until_confirmed_and_authorized():
    model = load_semantic_model(MODEL_PATH)

    result = validate_dataset_join(
        model,
        "order_mart",
        "customer_mart",
        allowed_relationship_ids={"order_mart_customer_mart"},
    )

    assert not result.valid
    assert {diagnostic.code for diagnostic in result.errors} == {"unconfirmed_relationship"}

    model.dataset_relationships[0].verification_status = "confirmed"
    result = validate_dataset_join(
        model,
        "order_mart",
        "customer_mart",
        allowed_relationship_ids={"order_mart_customer_mart"},
    )

    assert result.valid


def test_join_validation_rejects_unauthorized_ambiguous_and_unsafe_fanout_relationships():
    model = load_semantic_model(MODEL_PATH)
    relationship = model.dataset_relationships[0]
    relationship.verification_status = "confirmed"

    unauthorized = validate_dataset_join(model, "order_mart", "customer_mart", allowed_relationship_ids=set())
    assert {diagnostic.code for diagnostic in unauthorized.errors} == {"unauthorized_relationship"}

    duplicate = relationship.model_copy(deep=True)
    duplicate.id = "alternate_order_customer"
    model.dataset_relationships.append(duplicate)
    ambiguous = validate_dataset_join(
        model,
        "order_mart",
        "customer_mart",
        allowed_relationship_ids={relationship.id, duplicate.id},
    )
    assert {diagnostic.code for diagnostic in ambiguous.errors} == {"ambiguous_join_path"}

    payment_relationship = next(item for item in model.dataset_relationships if item.id == "payment_staging_order_staging")
    payment_relationship.verification_status = "confirmed"
    unsafe = validate_dataset_join(
        model,
        "payment_staging",
        "order_staging",
        allowed_relationship_ids={payment_relationship.id},
    )
    assert {diagnostic.code for diagnostic in unsafe.errors} == {"fanout_risk"}

    preaggregated = validate_dataset_join(
        model,
        "payment_staging",
        "order_staging",
        allowed_relationship_ids={payment_relationship.id},
        preaggregated_source=True,
    )
    assert preaggregated.valid


def test_validator_rejects_order_metric_compatible_with_customer_grain_dimension():
    model = load_semantic_model(MODEL_PATH)
    model.metric("orders.order_count").compatible_dimension_ids = ["customers.first_order"]

    result = validate_semantic_model(model)

    assert [(item.object_id, item.field, item.code) for item in result.errors] == [
        ("orders.order_count", "compatible_dimension_ids", "incompatible_grain")
    ]


def test_validator_rejects_customer_grain_default_filter_on_order_metric():
    model = load_semantic_model(MODEL_PATH)
    model.metric("orders.order_amount").default_filters = [
        MetricFilter(dimension_id="customers.first_order", operator="eq", value="2018-01-01")
    ]

    result = validate_semantic_model(model)

    assert [(item.object_id, item.field, item.code) for item in result.errors] == [
        ("orders.order_amount", "default_filters.dimension_id", "incompatible_grain")
    ]


def test_model_validator_requires_many_to_many_relationships_to_disclose_fanout_risk():
    model = load_semantic_model(MODEL_PATH)
    relationship = model.dataset_relationships[0]
    relationship.cardinality = "many_to_many"
    relationship.fanout_risk = False

    result = validate_semantic_model(model)

    assert {diagnostic.code for diagnostic in result.errors} >= {"missing_fanout_risk"}
