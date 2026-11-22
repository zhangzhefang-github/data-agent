"""订单 Golden Dataset 的公开契约。

缝在数据集文件本身，以及 execute_order_sql 对参考 SQL 的执行结果。
参考 SQL 的预期行必须来自同一份 DuckDB 快照，不能把模型生成的行写成答案。
"""

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.direct_sql import SqlCorrection, SqlRejected, execute_order_sql
from app.query import load_catalog
from app.sql_prompt import authorized_joins, orders_query_scope

ROOT = Path(__file__).resolve().parents[2]
DATASET = Path(__file__).resolve().parents[1] / "app" / "evaluation" / "orders_platform.json"
TODAY = date(2026, 11, 21)
ENV_FILE = Path("/home/zzf/data-agent/.env")

CATEGORIES = {
    "基础指标与过滤": 15,
    "多维度与时间": 15,
    "JOIN 与 Fanout": 10,
    "多轮追问": 8,
    "权限与语义歧义": 7,
    "上下文定位与补充": 5,
}
REQUIRED_FIELDS = {
    "case_id",
    "intent_id",
    "domain",
    "space_id",
    "category",
    "tags",
    "difficulty",
    "user_question",
    "expected_behavior",
    "expected_metrics",
    "expected_dimensions",
    "expected_datasets",
    "expected_relationships",
    "expected_filters",
    "expected_sql",
    "expected_result",
    "comparison_policy",
    "expected_refusal_reason",
    "dataset_snapshot_id",
    "split",
}
COMPARISON_FIELDS = {
    "ordered",
    "absolute_tolerance",
    "relative_tolerance",
    "decimal_places",
    "dimension_keys",
    "duplicate_keys",
}
LEGACY_QUESTIONS = {
    "customer_orders": "2018-01-01 至 2018-04-09 有客户记录的订单笔数和订单金额是多少",
    "by_customer": "2018-01-01 至 2018-04-09 按客户标识统计订单笔数和订单金额",
    "top_customer": "2018-01-01 至 2018-04-09 订单金额最高的客户标识是什么",
}


def _load_dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def _cases():
    payload = _load_dataset()
    assert isinstance(payload["cases"], list)
    return payload["cases"]


def _duckdb_path():
    """只读取 DuckDB 路径。不打印、不返回密钥。"""
    configured = os.environ.get("SALES_DUCKDB_PATH")
    if not configured and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("SALES_DUCKDB_PATH="):
                configured = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        return None
    return path


def _snapshot_id(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"jaffle_shop.duckdb@{digest}"


def _row_key(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def test_orders_platform_schema_and_category_counts():
    cases = _cases()

    assert len(cases) == 60
    assert [len(cases)] == [sum(CATEGORIES.values())]
    counts = {category: 0 for category in CATEGORIES}
    seen_ids = set()
    seen_questions = set()
    snapshot_ids = set()
    for case in cases:
        assert REQUIRED_FIELDS <= set(case)
        assert case["case_id"] not in seen_ids
        assert case["user_question"] not in seen_questions
        seen_ids.add(case["case_id"])
        seen_questions.add(case["user_question"])
        assert case["domain"] == "sales"
        assert case["space_id"] == "orders"
        assert case["category"] in CATEGORIES
        counts[case["category"]] += 1
        assert case["split"] in {"dev", "validation", "holdout"}
        assert case["difficulty"] in {"低", "中", "高"}
        assert case["expected_behavior"] in {"answer", "refuse", "clarify"}
        assert isinstance(case["tags"], list) and case["tags"]
        assert all(isinstance(tag, str) and tag for tag in case["tags"])
        for name in (
            "expected_metrics",
            "expected_dimensions",
            "expected_datasets",
            "expected_relationships",
        ):
            assert isinstance(case[name], list)
            assert all(isinstance(item, str) and item for item in case[name])
        assert isinstance(case["expected_filters"], list)
        for item in case["expected_filters"]:
            assert {"field", "operator", "value"} <= set(item)
        assert isinstance(case["expected_sql"], str)
        assert isinstance(case["expected_refusal_reason"], str)
        result = case["expected_result"]
        assert isinstance(result["columns"], list)
        assert isinstance(result["rows"], list)
        policy = case["comparison_policy"]
        assert COMPARISON_FIELDS <= set(policy)
        assert isinstance(policy["ordered"], bool)
        assert isinstance(policy["dimension_keys"], list)
        assert policy["duplicate_keys"] == "reject"
        assert case["user_question"].strip()
        assert case["dataset_snapshot_id"]
        snapshot_ids.add(case["dataset_snapshot_id"])
        if case["expected_behavior"] == "answer":
            assert case["expected_refusal_reason"] == ""
        else:
            assert case["expected_refusal_reason"]
            assert result["rows"] == []
    assert counts == CATEGORIES
    assert len(snapshot_ids) == 1


def test_same_intent_stays_in_one_split_and_synonyms_share_result():
    grouped = {}
    for case in _cases():
        grouped.setdefault(case["intent_id"], []).append(case)

    for intent_id, items in grouped.items():
        assert len({item["split"] for item in items}) == 1, intent_id
        if len(items) > 1:
            assert len({item["case_id"] for item in items}) == len(items)
            head = items[0]
            for item in items[1:]:
                assert item["expected_behavior"] == head["expected_behavior"]
                assert item["expected_result"] == head["expected_result"]
                assert item["expected_sql"] == head["expected_sql"]

    for intent_id in ("customer_orders", "by_customer", "top_customer"):
        assert len(grouped[intent_id]) == 2


def test_three_legacy_acceptance_questions_keep_their_results():
    cases = {case["case_id"]: case for case in _cases()}

    customer_orders = cases["customer_orders"]
    by_customer = cases["by_customer"]
    top_customer = cases["top_customer"]
    assert customer_orders["user_question"] == LEGACY_QUESTIONS["customer_orders"]
    assert by_customer["user_question"] == LEGACY_QUESTIONS["by_customer"]
    assert top_customer["user_question"] == LEGACY_QUESTIONS["top_customer"]
    assert customer_orders["expected_behavior"] == "answer"
    assert customer_orders["expected_result"]["rows"] == [[99, 1672.0]]
    assert by_customer["expected_result"]["columns"] == ["customer_id", "order_rows", "order_amount"]
    assert len(by_customer["expected_result"]["rows"]) == 62
    assert sum(row[1] for row in by_customer["expected_result"]["rows"]) == 99
    assert sum(row[2] for row in by_customer["expected_result"]["rows"]) == 1672.0
    assert top_customer["expected_result"]["rows"] == [[51, 99.0]]
    kept = cases["payment_preaggregate_keeps_order_amount"]
    assert kept["expected_behavior"] == "answer"
    assert kept["expected_result"]["rows"] == [[99, 1672.0, 113, 1672.0]]


def test_unsupported_requests_and_inflated_payment_join_are_not_correct_answers():
    cases = _cases()
    by_id = {case["case_id"]: case for case in cases}

    inflated = by_id["payment_join_inflates_order_amount"]
    assert inflated["expected_behavior"] == "refuse"
    assert inflated["expected_result"]["rows"] == []
    assert "2083" in inflated["expected_refusal_reason"]
    assert "放大" in inflated["expected_refusal_reason"]
    for case in cases:
        if case["expected_behavior"] != "answer":
            continue
        for row in case["expected_result"]["rows"]:
            assert 2083.0 not in row
            assert 2083 not in row

    for phrase in ("平均订单金额", "客户姓名", "stg_payments", "raw_orders"):
        matched = [case for case in cases if phrase in case["user_question"]]
        assert matched
        assert all(case["expected_behavior"] in {"refuse", "clarify"} for case in matched)

    scope = by_id["query_scope"]
    assert scope["expected_behavior"] == "answer"
    assert scope["expected_sql"] == ""
    assert scope["expected_result"]["rows"] == [[orders_query_scope(ROOT)]]
    prior_year = by_id["followup_prior_year"]
    assert prior_year["expected_behavior"] == "answer"
    assert prior_year["expected_result"]["rows"] == []


def test_reference_sql_matches_expected_result_when_duckdb_exists():
    cases = _cases()
    path = _duckdb_path()
    if path is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")

    assert {case["dataset_snapshot_id"] for case in cases} == {_snapshot_id(path)}
    catalog = load_catalog(ROOT, "orders")
    joins = authorized_joins(ROOT)
    for case in cases:
        sql = case["expected_sql"].strip()
        if not sql:
            continue
        if case["expected_behavior"] != "answer":
            with pytest.raises((SqlRejected, SqlCorrection)):
                execute_order_sql(path, sql, catalog, TODAY, joins)
            continue
        actual = execute_order_sql(path, sql, catalog, TODAY, joins)
        expected = case["expected_result"]
        assert actual["columns"] == expected["columns"]
        if case["comparison_policy"]["ordered"]:
            assert actual["rows"] == expected["rows"]
        else:
            assert sorted(actual["rows"], key=_row_key) == sorted(expected["rows"], key=_row_key)

    with duckdb.connect(str(path), read_only=True) as connection:
        inflated = connection.execute(
            """
            SELECT SUM(orders.amount)
            FROM orders
            JOIN payments ON orders.order_id = payments.order_id
            WHERE orders.order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'
            """
        ).fetchone()[0]
    assert inflated == 2083.0
    refused = next(case for case in cases if case["case_id"] == "payment_join_inflates_order_amount")
    assert refused["expected_result"]["rows"] != [[inflated]]
