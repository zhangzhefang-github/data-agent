import json
import os
from datetime import date
from pathlib import Path

import pytest

from app.query import DataSource, Filter, OrderBy, Query, execute_query, load_catalog, question_catalog
from app.semantic.loader import load_semantic_model


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def jaffle_shop_path() -> Path:
    configured_path = os.environ.get("SALES_DUCKDB_PATH")
    if not configured_path:
        pytest.skip("SALES_DUCKDB_PATH is required for the real Jaffle Shop query test")
    return Path(configured_path)


def test_order_question_totals_stay_unchanged_with_unconfirmed_mart_lineage(jaffle_shop_path: Path):
    catalog = load_catalog(ROOT, "orders")
    result = execute_query(
        jaffle_shop_path,
        Query(
            metrics=["orders.order_count", "orders.order_amount"],
            time_range={"start": "2018-01-01", "end": "2018-04-09"},
        ),
        date(2026, 11, 20),
        catalog,
        source=DataSource(engine="duckdb", location=str(jaffle_shop_path)),
    )

    assert result["columns"] == ["order_rows", "order_amount"]
    assert result["rows"] == [[99, 1672.0]]


def test_orders_catalog_is_projected_from_the_published_semantic_model():
    catalog = load_catalog(ROOT, "orders")

    assert catalog["id"] == "orders"
    assert catalog["name"] == "订单主题空间"
    assert set(catalog["metrics"]) == {
        "order_amount",
        "order_rows",
        "credit_card_amount",
        "coupon_amount",
        "bank_transfer_amount",
        "gift_card_amount",
    }
    assert set(catalog["dimensions"]) == {"order_date", "status"}
    assert catalog["metric_input_aliases"]["orders.order_count"] == "order_rows"
    assert catalog["dimensions"]["status"]["values"] == [
        {"name": "已下单", "value": "placed"},
        {"name": "已发货", "value": "shipped"},
        {"name": "已完成", "value": "completed"},
        {"name": "退货中", "value": "return_pending"},
        {"name": "已退货", "value": "returned"},
    ]
    assert "customers" not in json.dumps(catalog, ensure_ascii=False)
    assert "raw_orders" not in json.dumps(catalog, ensure_ascii=False)


def test_orders_dimension_question_catalog_does_not_require_a_time_dimension():
    catalog = question_catalog(ROOT, "orders", "订单状态")

    assert catalog["metrics"] == {}
    assert set(catalog["dimensions"]) == {"status"}
    assert catalog["models"]["order_mart"]["date_column"] is None


def test_orders_canonical_count_and_legacy_alias_share_legacy_result_contract(jaffle_shop_path: Path):
    catalog = load_catalog(ROOT, "orders")
    source = DataSource(engine="duckdb", location=str(jaffle_shop_path))
    canonical = execute_query(
        jaffle_shop_path,
        Query(
            metrics=["orders.order_count", "order_amount"],
            dimensions=["status"],
            time_range={"start": "2018-01-01", "end": "2018-04-09"},
            order_by=OrderBy(field="orders.order_count", direction="desc"),
        ),
        date(2026, 11, 20),
        catalog,
        source=source,
    )
    legacy = execute_query(
        jaffle_shop_path,
        Query(
            metrics=["order_rows", "order_amount"],
            dimensions=["status"],
            time_range={"start": "2018-01-01", "end": "2018-04-09"},
            order_by=OrderBy(field="order_rows", direction="desc"),
        ),
        date(2026, 11, 20),
        catalog,
        source=source,
    )

    assert canonical["columns"] == legacy["columns"]
    assert canonical["sql"] == legacy["sql"]
    assert sorted(canonical["rows"]) == sorted(legacy["rows"])
    assert canonical["columns"] == ["status", "order_rows", "order_amount"]
    assert canonical["rows"][0] == ["completed", 67, 1103.0]
    assert 'AS "order_rows"' in canonical["sql"]


def test_status_aggregation_exposes_its_own_query_output_grain(jaffle_shop_path: Path):
    catalog = load_catalog(ROOT, "orders")
    model = load_semantic_model(ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml")
    result = execute_query(
        jaffle_shop_path,
        Query(
            metrics=["orders.order_count", "order_amount"],
            dimensions=["status"],
            time_range={"start": "2018-01-01", "end": "2018-04-09"},
        ),
        date(2026, 11, 20),
        catalog,
        source=DataSource(engine="duckdb", location=str(jaffle_shop_path)),
    )

    assert result["output_grain"] == ["orders.status"]
    assert result["output_grain"] != model.dataset("order_mart").grain
    assert result["output_grain"] != model.metric("orders.order_count").base_grain
    assert model.dataset("order_mart").grain == ["order_id"]
    assert model.metric("orders.order_count").base_grain == ["order_id"]
    assert ["completed", 67, 1103.0] in result["rows"]


def test_orders_semantic_catalog_preserves_current_year_empty_result(jaffle_shop_path: Path):
    catalog = load_catalog(ROOT, "orders")
    result = execute_query(
        jaffle_shop_path,
        Query(metrics=["orders.order_count"], dimensions=["status"]),
        date(2026, 11, 20),
        catalog,
        source=DataSource(engine="duckdb", location=str(jaffle_shop_path)),
    )

    assert result["columns"] == ["status", "order_rows"]
    assert result["rows"] == []


def test_orders_rejects_canonical_and_legacy_count_ids_after_normalization(jaffle_shop_path: Path):
    with pytest.raises(ValueError, match="指标和维度不能重复"):
        execute_query(
            jaffle_shop_path,
            Query(metrics=["orders.order_count", "order_rows"]),
            date(2026, 11, 20),
            load_catalog(ROOT, "orders"),
            source=DataSource(engine="duckdb", location=str(jaffle_shop_path)),
        )


def test_orders_semantic_catalog_maps_status_dictionary_and_intersects_date_filters(jaffle_shop_path: Path):
    catalog = load_catalog(ROOT, "orders")
    result = execute_query(
        jaffle_shop_path,
        Query(
            metrics=["orders.order_count", "order_amount"],
            dimensions=["status"],
            filters=[
                Filter(field="status", operator="eq", value="已完成"),
                Filter(field="status", operator="eq", value="已退货"),
                Filter(field="order_date", operator="gte", value="2018-02-01"),
                Filter(field="order_date", operator="lte", value="2018-02-28"),
            ],
            time_range={"start": "2018-01-01", "end": "2018-04-09"},
        ),
        date(2026, 11, 20),
        catalog,
        source=DataSource(engine="duckdb", location=str(jaffle_shop_path)),
    )

    assert result["rows"] == [["completed", 26, 400.0]]
    assert "IN (?, ?)" in result["sql"]
    assert result["sql"].count("?") == 5


def test_sales_catalog_keeps_its_existing_order_count_meaning():
    catalog = load_catalog(ROOT, "sales")

    assert catalog["metrics"]["order_count"]["description"] == "有效订单数"
    assert "metric_input_aliases" not in catalog


def test_orders_runtime_rejects_a_model_that_cannot_be_published(tmp_path: Path):
    semantic = tmp_path / "backend" / "configs" / "semantic"
    semantic.mkdir(parents=True)
    model_text = (ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml").read_text()
    semantic.joinpath("jaffle_shop.yaml").write_text(
        model_text.replace("primary_key: [order_id]", "primary_key: [not_a_field]", 1)
    )
    semantic.joinpath("spaces.yaml").write_text(
        (ROOT / "backend" / "configs" / "semantic" / "spaces.yaml").read_text()
    )

    with pytest.raises(ValueError, match="not published"):
        load_catalog(tmp_path, "orders")
