"""以一种模式运行订单平台数据集。

三种模式是这个入口上的三个适配器：确定性参考 SQL、可替换模型 Runner、真实模型。
确定性通过只说明参考 SQL 和快照一致，不说明模型会写 SQL。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.direct_sql import SqlCorrection, SqlRejected, SqlTimeout, execute_order_sql
from app.evaluation.judgement import judge
from app.evaluation.result_comparator import ComparisonPolicy, compare_results
from app.query import load_catalog
from app.query_scope import is_query_scope_question
from app.sql_prompt import authorized_joins, orders_query_scope

PLATFORM_DATASET = Path(__file__).with_name("orders_platform.json")
ENV_FILE = Path("/home/zzf/data-agent/.env")
ROOT = Path(__file__).resolve().parents[3]
TODAY = date(2026, 11, 21)
_EVALUATION_STATUSES = ("PASS", "FAIL", "ERROR", "SKIPPED")
_BEHAVIOR_EXPECT = {"refuse": "rejected", "clarify": "clarification", "answer": "rows"}


def run_orders_platform(
    mode,
    *,
    runner=None,
    split=None,
    dataset_version=None,
    dataset_path=None,
    database_path=None,
):
    """运行 orders_platform.json。mode 只能是 deterministic、model 或 live 之一。"""
    if mode not in {"deterministic", "model", "live"}:
        raise ValueError("订单平台模式必须是 deterministic、model 或 live")
    dataset = json.loads(Path(dataset_path or PLATFORM_DATASET).read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if split is not None:
        cases = [case for case in cases if case["split"] == split]
    if dataset_version is not None and dataset["dataset_version"] != dataset_version:
        reports = [_error_case(case, "数据集版本不一致") for case in cases]
        return _report(mode, dataset, reports, proves_model_sql=False, mock=False)

    database = _resolve_database_path(database_path)
    if mode == "deterministic":
        if not _snapshot_matches(dataset, database):
            reports = [_error_case(case, "数据快照与数据集不一致") for case in cases]
            return _report(mode, dataset, reports, proves_model_sql=False, mock=False)
        reports = _run_deterministic(cases, database)
        return _report(mode, dataset, reports, proves_model_sql=False, mock=False)

    if mode == "model":
        if runner is None:
            raise ValueError("model 模式需要传入可替换的 Runner")
        reports = [_score_case(case, runner(case)) for case in cases]
        return _report(mode, dataset, reports, proves_model_sql=False, mock=bool(getattr(runner, "mock", False)))

    reports = _run_live(cases, database)
    proves_model_sql = any(case["proves_model_sql"] for case in reports)
    return _report(mode, dataset, reports, proves_model_sql=proves_model_sql, mock=False)


def _run_deterministic(cases, database_path: Path):
    catalog = load_catalog(ROOT, "orders")
    joins = authorized_joins(ROOT)
    return [_score_case(case, _deterministic_observation(case, database_path, catalog, joins)) for case in cases]


def _deterministic_observation(case, database_path, catalog, joins):
    if case["expected_behavior"] == "answer" and is_query_scope_question(case["user_question"]):
        text = orders_query_scope(ROOT)
        return {"text": text, "result": {"columns": ["scope"], "rows": [[text]], "sql": ""}, "done": "completed"}
    try:
        result = execute_order_sql(database_path, case["expected_sql"], catalog, TODAY, joins)
    except (SqlRejected, SqlCorrection) as exc:
        return _validator_observation(case, exc)
    except SqlTimeout:
        return {"timeout": True}
    return {"result": result, "done": "completed"}


def _validator_observation(case, exc):
    """校验器拒绝或要求修正，是拒绝和澄清用例的正确确定性观察。"""
    message = str(exc).strip()
    behavior = case["expected_behavior"]
    if behavior == "clarify":
        if not any(marker in message for marker in ("请先提供", "需要澄清", "请补充")):
            message = f"需要澄清。{message}" if message else "需要澄清"
        return {"text": message, "done": "completed"}
    if behavior == "refuse":
        return {"text": message or "查询被拒绝", "done": "completed"}
    return {"error": {"code": "validator_error", "message": message}, "done": "failed"}


def _comparison_policy(case):
    raw = case["comparison_policy"]
    duplicate = raw["duplicate_keys"]
    # 数据集里的 "reject" 是策略名，不是列名。比较器的字段只收列名。
    if duplicate == "reject" or not duplicate:
        duplicate_keys = ()
    elif isinstance(duplicate, str):
        duplicate_keys = (duplicate,)
    else:
        duplicate_keys = tuple(duplicate)
    result_columns = set(case["expected_result"]["columns"])
    # 维度键必须是结果列。过滤条件写在键里、但没有出现在结果中时，不能交给比较器。
    dimension_keys = tuple(key for key in raw["dimension_keys"] if key in result_columns)
    return ComparisonPolicy(
        ordered=bool(raw["ordered"]),
        absolute_tolerance=Decimal(str(raw["absolute_tolerance"])),
        relative_tolerance=Decimal(str(raw["relative_tolerance"])),
        decimal_places=raw["decimal_places"],
        dimension_keys=dimension_keys,
        duplicate_keys=duplicate_keys,
    )


def _normalize_scope_rows(rows):
    return [[_normalize_scope_text(value) if isinstance(value, str) else value for value in row] for row in rows]


def _normalize_scope_text(text):
    def sort_columns(match):
        columns = [column for column in match.group(1).split("、") if column]
        return "当前可关联的列是" + "、".join(sorted(columns))

    return re.sub(r"当前可关联的列是([^。]*)", sort_columns, text)


def _legacy_expected(case):
    behavior = case["expected_behavior"]
    expected = {"expect": _BEHAVIOR_EXPECT[behavior]}
    if behavior == "answer":
        expected["columns"] = case["expected_result"]["columns"]
        expected["rows"] = case["expected_result"]["rows"]
    return expected


def _run_live(cases, database_path):
    """真实模式只走订单聊天路径。没有密钥时跳过，不用参考 SQL 或 Mock 填通过。"""
    if not os.getenv("OPENAI_API_KEY"):
        return [_skipped_platform_case(case) for case in cases]
    duckdb_path = str(database_path) if database_path else os.getenv("SALES_DUCKDB_PATH")
    if not duckdb_path:
        return [_error_case(case, "SALES_DUCKDB_PATH 未设置") for case in cases]
    from fastapi.testclient import TestClient

    from app import main
    from app.evaluation.runner import send

    reports = []
    with tempfile.TemporaryDirectory(prefix="orders-platform-live-") as directory:
        conversations = Path(directory) / "conversations.db"
        with patch.object(main, "CATALOG", load_catalog(main.ROOT, "orders")), patch.object(
            main, "DUCKDB_PATH", duckdb_path
        ), patch.object(main, "DATABASE_URL", None), patch.object(main, "CONVERSATIONS", conversations):
            with TestClient(main.app) as client:
                for case in cases:
                    report = _score_case(case, _chat_observation(client, case, send))
                    if report["evaluation_status"] == "PASS":
                        report["proves_model_sql"] = True
                    reports.append(report)
    return reports


def _chat_observation(client, case, send):
    question = case.get("user_question") or case.get("question")
    if "previous_question" in case:
        initial = send(client, case["previous_question"])
        if not initial.get("conversation_id") or not initial.get("result"):
            observation = dict(initial)
            if not initial.get("error") and not initial.get("text") and not initial.get("result"):
                observation["no_model_result"] = True
            return observation
        return send(client, question, initial["conversation_id"])
    return send(client, question)


def _skipped_platform_case(case):
    verdict = judge({"skipped": True, "missing_api_key": True}, _legacy_expected(case))
    return {
        "id": case["case_id"],
        "evaluation_status": verdict["evaluation_status"],
        "execution_status": verdict["execution_status"],
        "proves_model_sql": False,
        "failure_stage": "configuration",
        "reason": "OPENAI_API_KEY 未设置",
        "actual_columns": [],
        "actual_rows": [],
        "generated_sql": "",
        "latency": None,
        "error_details": {"message": "OPENAI_API_KEY 未设置"},
        "leaked": verdict.get("leaked", 0),
        "expected_behavior": case.get("expected_behavior"),
        "expected_result": case.get("expected_result"),
        "category": case.get("category"),
        "actual_result": {"columns": [], "rows": []},
    }


def _score_case(case, observation):
    if is_query_scope_question(case["user_question"]) and isinstance(observation, dict) and not observation.get("result"):
        text = (observation.get("text") or "").strip()
        if text:
            observation = {**observation, "result": {"columns": ["scope"], "rows": [[text]], "sql": ""}}
    verdict = judge(observation, _legacy_expected(case))
    result = observation.get("result") if isinstance(observation.get("result"), dict) else None
    actual_columns = [] if result is None else list(result.get("columns") or [])
    actual_rows = [] if result is None else list(result.get("rows") or [])
    evaluation = verdict["evaluation_status"]
    failure = verdict.get("failure_stage")
    if case["expected_behavior"] == "answer" and verdict["execution_status"] == "success":
        expected = case["expected_result"]
        compared_actual = actual_rows
        compared_expected = expected["rows"]
        if is_query_scope_question(case["user_question"]):
            # 可关联列来自集合，句子里的顺序不稳定。比较前把列名排序，缺列仍然对不上。
            compared_actual = _normalize_scope_rows(actual_rows)
            compared_expected = _normalize_scope_rows(expected["rows"])
        matched = compare_results(
            actual_columns,
            compared_actual,
            expected["columns"],
            compared_expected,
            _comparison_policy(case),
        ).matched
        evaluation = "PASS" if matched else "FAIL"
        failure = None if matched else "result"
    report = {
        "id": case["case_id"],
        "evaluation_status": evaluation,
        "execution_status": verdict["execution_status"],
        "proves_model_sql": False,
        "actual_columns": actual_columns,
        "actual_rows": actual_rows,
        "failure_stage": failure,
        "generated_sql": _generated_sql(observation),
        "latency": _latency(observation),
        "error_details": _error_details(observation),
        "leaked": verdict.get("leaked", 0),
        "expected_behavior": case.get("expected_behavior"),
        "expected_result": case.get("expected_result"),
        "category": case.get("category"),
        "actual_result": {"columns": actual_columns, "rows": actual_rows},
    }
    if isinstance(observation, dict) and "token_usage" in observation:
        report["token_usage"] = observation["token_usage"]
    return report


def _resolve_database_path(database_path):
    """只取 DuckDB 路径。不读取、不打印密钥。"""
    if database_path:
        return Path(database_path)
    configured = os.getenv("SALES_DUCKDB_PATH")
    if not configured and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("SALES_DUCKDB_PATH="):
                configured = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not configured:
        return None
    return Path(configured)


def _snapshot_matches(dataset, database_path) -> bool:
    if database_path is None or not database_path.is_file():
        return False
    actual = f"jaffle_shop.duckdb@{hashlib.sha256(database_path.read_bytes()).hexdigest()[:16]}"
    if actual != dataset["dataset_snapshot_id"]:
        return False
    return all(case["dataset_snapshot_id"] == dataset["dataset_snapshot_id"] for case in dataset["cases"])


def _error_case(case, reason):
    return {
        "id": case["case_id"],
        "evaluation_status": "ERROR",
        "execution_status": "database_error",
        "proves_model_sql": False,
        "reason": reason,
        "generated_sql": "",
        "latency": None,
        "error_details": {"message": reason},
        "leaked": 0,
        "expected_behavior": case.get("expected_behavior"),
        "expected_result": case.get("expected_result"),
        "category": case.get("category"),
        "actual_columns": [],
        "actual_rows": [],
        "actual_result": {"columns": [], "rows": []},
    }


def _generated_sql(observation):
    if not isinstance(observation, dict):
        return ""
    result = observation.get("result")
    if isinstance(result, dict) and isinstance(result.get("sql"), str):
        return result["sql"]
    sql = observation.get("sql")
    return sql if isinstance(sql, str) else ""


def _latency(observation):
    if not isinstance(observation, dict):
        return None
    if "latency" in observation:
        return observation["latency"]
    return observation.get("latency_ms")


def _error_details(observation):
    if not isinstance(observation, dict) or not observation.get("error"):
        return None
    error = observation["error"]
    if isinstance(error, dict):
        return {"code": error.get("code"), "message": error.get("message")}
    if isinstance(error, str):
        return {"message": error}
    return {"message": "查询失败"}


def _summary(cases):
    return {
        name: sum(case.get("evaluation_status") == name for case in cases)
        for name in _EVALUATION_STATUSES
    }


def _report(mode, dataset, cases, *, proves_model_sql, mock):
    return {
        "mode": mode,
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_snapshot_id": dataset["dataset_snapshot_id"],
        "proves_model_sql": proves_model_sql,
        "mock": mock,
        "cases": cases,
        "summary": _summary(cases),
    }
