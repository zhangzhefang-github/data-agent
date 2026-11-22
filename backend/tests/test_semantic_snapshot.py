"""订单语义快照、短目录和授权边界。"""

from datetime import date
from pathlib import Path

import pytest

from app.direct_sql import SqlRejected, _validate_order_sql, context_coverage_gap
from app.semantic.context_builder import build_semantic_context
from app.semantic.loader import load_semantic_model
from app.semantic.registry import SemanticModelRegistry
from app.semantic.retrieval import (
    LexicalSemanticLocator,
    expand_authorization,
    is_small_space,
    present_context,
    short_catalog,
)
from app.semantic.snapshot import assemble_snapshot, authorization_payload, build_orders_snapshot, content_hash, validation_inputs
from app.sql_prompt import load_dialect, render_direct_sql_prompt

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"
SPACES_PATH = ROOT / "backend" / "configs" / "semantic" / "spaces.yaml"


def _published_orders():
    model = load_semantic_model(MODEL_PATH)
    registry = SemanticModelRegistry()
    registry.publish(model)
    published = registry.get(model.id, model.version)
    context = build_semantic_context("sales", "orders", model=published, spaces_path=SPACES_PATH)
    return published, context


def test_orders_space_is_small_and_snapshot_is_frozen():
    snapshot = build_orders_snapshot(ROOT)
    assert snapshot.model_id == "jaffle_shop"
    assert snapshot.model_version == "1.0"
    assert snapshot.space_id == "orders"
    assert snapshot.dialect_id == "duckdb"
    assert snapshot.data_source_id == "jaffle_duckdb"
    assert snapshot.schema_fingerprint == "unbound"
    assert is_small_space(snapshot.authorization)
    presented = present_context(snapshot, "订单笔数是多少")
    assert {item.id for item in presented.sql.datasets} == {item.id for item in snapshot.authorization.sql.datasets}
    assert len(snapshot.snapshot_id) == 16
    with pytest.raises(Exception):
        snapshot.snapshot_id = "changed"


def test_short_catalog_omits_unauthorized_assets_and_physical_detail():
    snapshot = build_orders_snapshot(ROOT)
    catalog = short_catalog(snapshot.authorization)
    identifiers = {item.id for item in catalog}
    rendered = "\n".join(item.model_dump_json() for item in catalog)

    assert "orders.order_count" in identifiers
    assert "order_mart_customer_mart" in identifiers
    assert "payment" not in identifiers
    assert "payment_staging" not in rendered
    assert "payment_source" not in rendered
    assert "raw_orders" not in rendered
    assert "order_staging" not in rendered
    assert "customer_staging" not in rendered
    assert "first_name" not in rendered
    assert "last_name" not in rendered
    assert "source_fields" not in rendered
    assert "SUM(" not in rendered
    assert "COUNT(*)" not in rendered


def test_synonym_and_id_locate_the_order_count_metric():
    snapshot = build_orders_snapshot(ROOT)
    catalog = short_catalog(snapshot.authorization)
    locator = LexicalSemanticLocator()

    by_synonym = locator.rank("帮我看订单数", catalog)
    by_id = locator.rank("请使用 orders.order_count", catalog)

    assert by_synonym[0].item.id == "orders.order_count"
    assert by_synonym[0].reason == "synonym"
    assert by_id[0].item.id == "orders.order_count"
    assert by_id[0].reason == "id"


def test_large_space_expands_only_the_selected_metric_and_its_grain():
    published, context = _published_orders()
    huge = context.business.metrics[0].model_copy(update={
        "id": "extra.metric_7",
        "name": "额外指标7",
        "definition": "说明" * 20_000,
        "synonyms": [],
    })
    wide = context.model_copy(update={
        "business": context.business.model_copy(update={"metrics": [*context.business.metrics, huge]}),
    })
    assert not is_small_space(wide)
    expanded = expand_authorization(wide, "额外指标7", assemble_snapshot(published, context, load_dialect(ROOT)).object_datasets)

    assert [item.id for item in expanded.business.metrics] == ["extra.metric_7"]
    assert expanded.business.metrics[0].base_grain == ["order_id"]
    assert {item.id for item in expanded.sql.datasets} == {"order_mart"}
    assert "customer_mart" not in {item.id for item in expanded.sql.datasets}
    assert expanded.sql.relationships == []


def test_spaces_do_not_share_authorized_assets(tmp_path: Path):
    published, _ = _published_orders()
    path = tmp_path / "orders_only_space.yaml"
    path.write_text(
        "spaces:\n"
        "  - id: orders_only\n"
        "    domain_id: sales\n"
        "    name: 只要订单\n"
        "    metric_ids: [orders.order_count]\n"
        "    dimension_ids: [orders.order_date]\n"
        "    dataset_ids: [order_mart]\n",
        encoding="utf-8",
    )
    narrow = build_semantic_context("sales", "orders_only", model=published, spaces_path=path)
    orders = build_orders_snapshot(ROOT)
    narrow_ids = {item.id for item in short_catalog(narrow)}
    order_ids = {item.id for item in short_catalog(orders.authorization)}

    assert "customer_mart" in order_ids
    assert "customer_mart" not in narrow_ids
    assert "orders.customer_id" not in narrow_ids
    assert content_hash(authorization_payload(narrow)) != orders.authorization_hash
    assert all(hit.item.id != "orders.customer_id" for hit in LexicalSemanticLocator().rank("客户标识", short_catalog(narrow)))


def test_schema_change_changes_the_snapshot_id_and_fields_keep_physical_types():
    snapshot = build_orders_snapshot(ROOT)
    original = {
        "main.orders": {"amount": "DOUBLE", "customer_id": "INTEGER"},
        "main.customers": {"customer_id": "INTEGER"},
    }
    changed = {
        "main.orders": {"amount": "INTEGER", "customer_id": "INTEGER"},
        "main.customers": {"customer_id": "INTEGER"},
    }
    bound = snapshot.with_schema({"version": "v1.2.0", "tables": original})
    other = snapshot.with_schema({"version": "v1.2.0", "tables": changed})

    assert bound.snapshot_id != snapshot.snapshot_id
    assert bound.snapshot_id != other.snapshot_id
    assert bound.schema_fingerprint != other.schema_fingerprint
    assert any(field.field_id == "amount" and field.physical_type == "DOUBLE" for field in bound.authorized_fields)
    assert all(field.field_id != "first_name" for field in bound.authorized_fields)
    assert snapshot.with_schema({"version": "v1.2.0", "tables": original}).snapshot_id == bound.snapshot_id


def test_prompt_and_validator_share_the_full_authorization(tmp_path: Path):
    snapshot = build_orders_snapshot(ROOT)
    database = {
        "version": "v1.2.0",
        "tables": {
            "main.orders": {"amount": "DOUBLE", "customer_id": "INTEGER", "order_date": "DATE", "status": "VARCHAR"},
            "main.customers": {"customer_id": "INTEGER"},
        },
    }
    bound = snapshot.with_schema(database)
    expanded = expand_authorization(bound.authorization, "订单笔数是多少", bound.object_datasets)
    catalog, joins = validation_inputs(bound)
    prompt = render_direct_sql_prompt(ROOT, database, bound.authorization, bound.dialect)

    assert "customer_mart" not in {item.id for item in expanded.sql.datasets}
    assert validation_inputs(bound)[1] == joins
    assert any("customers" in {left[0], right[0]} for left, right in joins)
    assert "main.customers" in prompt
    assert "first_name" not in prompt
    assert "payment_staging" not in prompt
    assert str(tmp_path) not in prompt
    assert "jaffle_duckdb" not in prompt
    gap = context_coverage_gap(
        "SELECT COUNT(*) AS order_rows FROM orders JOIN customers ON orders.customer_id = customers.customer_id",
        bound.authorization,
        expanded,
    )
    assert gap == ["customer_mart"]
    assert context_coverage_gap(
        "SELECT COUNT(*) AS order_rows FROM orders JOIN payments ON orders.order_id = payments.order_id",
        bound.authorization,
        expanded,
    ) == []
    with pytest.raises(SqlRejected, match="未授权"):
        _validate_order_sql(
            "SELECT COUNT(*) AS order_rows FROM orders JOIN payments ON orders.order_id = payments.order_id",
            catalog,
            date(2026, 11, 21),
            joins,
        )
