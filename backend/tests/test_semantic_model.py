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


def test_metric_records_multiple_sources_that_pass_reference_validation(tmp_path: Path):
    model = load_semantic_model(_write_source_fixture(tmp_path, metric_sources=["retail_orders", "wholesale_orders"]))

    metric = model.metric("orders.order_amount")

    assert metric.source_dataset_ids == ["retail_orders", "wholesale_orders"]
    assert validate_semantic_model(model).valid


def test_metric_rejects_an_unknown_source_among_multiple_sources(tmp_path: Path):
    model = load_semantic_model(_write_source_fixture(tmp_path, metric_sources=["retail_orders", "missing_orders"]))

    result = validate_semantic_model(model)

    assert [(item.object_id, item.field, item.code) for item in result.errors] == [
        ("orders.order_amount", "source_dataset_ids", "unknown_dataset")
    ]


def test_dimension_records_multiple_sources_that_pass_reference_validation(tmp_path: Path):
    model = load_semantic_model(
        _write_source_fixture(
            tmp_path,
            metric_sources=["retail_orders"],
            dimension_sources=["retail_orders", "wholesale_orders"],
        )
    )

    dimension = next(item for item in model.dimensions if item.id == "orders.order_date")

    assert dimension.source_dataset_ids == ["retail_orders", "wholesale_orders"]
    assert validate_semantic_model(model).valid


def test_dimension_rejects_an_unknown_source_among_multiple_sources(tmp_path: Path):
    model = load_semantic_model(
        _write_source_fixture(
            tmp_path,
            metric_sources=["retail_orders"],
            dimension_sources=["retail_orders", "missing_orders"],
        )
    )

    result = validate_semantic_model(model)

    assert [(item.object_id, item.field, item.code) for item in result.errors] == [
        ("orders.order_date", "source_dataset_ids", "unknown_dataset")
    ]


def test_published_order_metrics_and_order_dimensions_still_use_only_order_mart():
    model = load_semantic_model(MODEL_PATH)
    published = [metric for metric in model.metrics if metric.published]
    order_dimensions = [
        dimension for dimension in model.dimensions if dimension.id in {"orders.order_date", "orders.status"}
    ]

    assert {metric.id: metric.source_dataset_ids for metric in published} == {
        "orders.order_amount": ["order_mart"],
        "orders.order_count": ["order_mart"],
        "orders.credit_card_amount": ["order_mart"],
        "orders.coupon_amount": ["order_mart"],
        "orders.bank_transfer_amount": ["order_mart"],
        "orders.gift_card_amount": ["order_mart"],
    }
    assert {dimension.id: dimension.source_dataset_ids for dimension in order_dimensions} == {
        "orders.order_date": ["order_mart"],
        "orders.status": ["order_mart"],
    }


def _write_source_fixture(path: Path, *, metric_sources: list[str], dimension_sources: list[str] | None = None) -> Path:
    """独立于 Jaffle Shop 的模型，只用来观察指标和维度的来源。"""
    dimension_sources = dimension_sources or ["retail_orders"]
    metric_source_yaml = _source_yaml(metric_sources)
    dimension_source_yaml = _source_yaml(dimension_sources)
    fixture = path / "multi_source.yaml"
    fixture.write_text(
        f"""\
id: multi_source_example
version: "1.0"
domains:
  - {{id: sales, name: 销售}}
data_sources:
  - {{id: warehouse, name: 仓库, dialect: duckdb}}
business_objects: []
business_relationships: []
datasets:
  - id: retail_orders
    name: 零售订单
    domain_id: sales
    dataset_type: fact
    grain: [order_id]
    primary_key: [order_id]
    fields:
      - {{id: order_id, name: 订单标识, data_type: integer}}
      - {{id: amount, name: 金额, data_type: integer}}
      - {{id: order_date, name: 订单日期, data_type: date}}
    physical_mapping:
      data_source_id: warehouse
      object_name: retail_orders
      object_type: table
      column_mappings: {{order_id: order_id, amount: amount, order_date: order_date}}
  - id: wholesale_orders
    name: 批发订单
    domain_id: sales
    dataset_type: fact
    grain: [order_id]
    primary_key: [order_id]
    fields:
      - {{id: order_id, name: 订单标识, data_type: integer}}
      - {{id: amount, name: 金额, data_type: integer}}
      - {{id: order_date, name: 订单日期, data_type: date}}
    physical_mapping:
      data_source_id: warehouse
      object_name: wholesale_orders
      object_type: table
      column_mappings: {{order_id: order_id, amount: amount, order_date: order_date}}
dataset_relationships: []
dimensions:
  - id: orders.order_date
    name: 订单日期
    {dimension_source_yaml}
    field_id: order_date
    is_time: true
metrics:
  - id: orders.order_amount
    name: 订单金额
    definition: 订单金额合计
    expression: {{kind: sum, field_id: amount}}
    {metric_source_yaml}
    base_grain: [order_id]
    time_dimension_id: orders.order_date
    compatible_dimension_ids: [orders.order_date]
""",
        encoding="utf-8",
    )
    return fixture


def _source_yaml(sources: list[str]) -> str:
    if len(sources) == 1:
        return f"source_dataset_id: {sources[0]}"
    joined = ", ".join(sources)
    return f"source_dataset_ids: [{joined}]"
