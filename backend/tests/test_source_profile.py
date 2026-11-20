import os
from pathlib import Path

import pytest

from app.semantic.catalog import profile_jaffle_shop
from app.semantic.loader import load_semantic_model


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"


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


def test_profile_jaffle_shop_reconciles_order_amounts_by_payment_method(jaffle_shop_path: Path):
    before = jaffle_shop_path.read_bytes()
    profile = profile_jaffle_shop(jaffle_shop_path)

    # 期望值来自对真实库的只读汇总，不由被测实现重算。
    assert {
        method: (item.order_amount, item.payment_amount)
        for method, item in profile.order_baseline.amounts_by_payment_method.items()
    } == {
        "credit_card": (871.0, 871.0),
        "coupon": (185.0, 185.0),
        "bank_transfer": (411.0, 411.0),
        "gift_card": (205.0, 205.0),
    }
    assert jaffle_shop_path.read_bytes() == before


def test_real_profile_matches_the_modeled_relationship_states_and_customer_evidence(jaffle_shop_path: Path):
    profile = profile_jaffle_shop(jaffle_shop_path)
    model = load_semantic_model(MODEL_PATH)

    assert {relationship.id: relationship.verification_status for relationship in model.business_relationships} == {
        "customer_to_order": "proposed",
        "order_to_payment": "proposed",
    }
    evidence_by_datasets = {
        (evidence.source_dataset_id, evidence.target_dataset_id): evidence
        for evidence in profile.relationships.values()
    }
    assert len(model.dataset_relationships) == len(evidence_by_datasets) == 5
    for relationship in model.dataset_relationships:
        evidence = evidence_by_datasets[(relationship.source_dataset_id, relationship.target_dataset_id)]
        assert relationship.verification_status == evidence.verification_status == "profiled"
        assert relationship.match_rate == evidence.match_rate == 1.0
        assert relationship.orphan_count == evidence.orphan_count == 0

    assert profile.customer_baseline.customers_with_orders == 62
    assert profile.customer_baseline.customers_without_orders == 38
    assert profile.customer_baseline.order_count_mismatches == 0
    assert profile.customer_baseline.date_mismatches == 0
    assert profile.customer_baseline.lifetime_value_mismatches == 0
