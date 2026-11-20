import os
from pathlib import Path

import pytest

from app.semantic.catalog import profile_jaffle_shop


@pytest.fixture
def jaffle_shop_path() -> Path:
    configured_path = os.environ.get("SALES_DUCKDB_PATH")
    if not configured_path:
        pytest.skip("SALES_DUCKDB_PATH is required for the real Jaffle Shop profile test")
    path = Path(configured_path)
    if not path.is_file():
        pytest.skip("SALES_DUCKDB_PATH does not point to a Jaffle Shop DuckDB file")
    return path


def test_profile_jaffle_shop_reports_the_real_catalog_and_relationship_evidence(jaffle_shop_path: Path):
    profile = profile_jaffle_shop(jaffle_shop_path)

    assert [(item.name, item.object_type, item.row_count) for item in profile.objects] == [
        ("customers", "table", 100),
        ("orders", "table", 99),
        ("raw_customers", "table", 100),
        ("raw_orders", "table", 99),
        ("raw_payments", "table", 113),
        ("stg_customers", "view", 100),
        ("stg_orders", "view", 99),
        ("stg_payments", "view", 113),
    ]
    assert [(column.name, column.data_type) for column in profile.objects[1].columns] == [
        ("order_id", "INTEGER"),
        ("customer_id", "INTEGER"),
        ("order_date", "DATE"),
        ("status", "VARCHAR"),
        ("credit_card_amount", "DOUBLE"),
        ("coupon_amount", "DOUBLE"),
        ("bank_transfer_amount", "DOUBLE"),
        ("gift_card_amount", "DOUBLE"),
        ("amount", "DOUBLE"),
    ]
    assert profile.candidate_keys["orders"].null_count == 0
    assert profile.candidate_keys["orders"].duplicate_count == 0
    assert profile.relationships["payment_staging_to_order_staging"].verification_status == "profiled"
    assert profile.relationships["payment_staging_to_order_staging"].match_rate == 1.0
    assert profile.relationships["payment_staging_to_order_staging"].orphan_count == 0
    assert profile.relationships["payment_staging_to_order_staging"].multi_child_parent_count == 13
    assert profile.staging_lineage["stg_payments"].transforms == ["amount / 100"]
    assert "raw_payments" in profile.staging_lineage["stg_payments"].view_sql


def test_profile_jaffle_shop_reports_reconciled_amounts_and_fanout_without_writing(jaffle_shop_path: Path):
    before = jaffle_shop_path.read_bytes()
    profile = profile_jaffle_shop(jaffle_shop_path)

    assert profile.order_baseline.total_orders == 99
    assert profile.order_baseline.total_amount == 1672.0
    assert profile.order_baseline.payment_total == 1672.0
    assert profile.order_baseline.payment_join_rows == 113
    assert profile.order_baseline.repeated_order_amount == 2083.0
    assert profile.order_baseline.preaggregated_join_rows == 99
    assert profile.order_baseline.preaggregated_order_amount == 1672.0
    assert profile.customer_baseline.total_customers == 100
    assert profile.customer_baseline.customers_without_orders == 38
    assert profile.customer_baseline.null_order_counts == 38
    assert profile.status_values == {"completed": 67, "placed": 13, "return_pending": 2, "returned": 4, "shipped": 13}
    assert jaffle_shop_path.read_bytes() == before
