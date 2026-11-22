"""订单评测分维度报告。

缝在 summarize_run：给定一份运行清单，分开给出语义、SQL、业务结果、安全、上下文和工程。
业务结果只统计预期行为是回答的用例。安全门看泄漏条数，不跟其它通过率合成一个准确率。
"""

import json
from pathlib import Path

from app.evaluation.manifest import write_run_manifest
from app.evaluation.platform_run import run_orders_platform
from app.evaluation.report import summarize_run
from app.semantic.snapshot import build_orders_snapshot

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DATASET = Path(__file__).resolve().parents[1] / "app" / "evaluation" / "orders_platform.json"
SECTIONS = ("semantic", "sql", "business_result", "safety", "context", "engineering")


def _subset(tmp_path, case_ids):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    wanted = set(case_ids)
    dataset["cases"] = [case for case in dataset["cases"] if case["case_id"] in wanted]
    path = tmp_path / "orders_platform.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    return path


def _manifest(tmp_path, case_ids, runner, run_id):
    dataset_path = _subset(tmp_path, case_ids)
    run = run_orders_platform("model", runner=runner, dataset_path=dataset_path)
    return write_run_manifest(
        run,
        build_orders_snapshot(ROOT),
        repo_root=tmp_path,
        run_id=run_id,
        started_at="2026-11-22T09:40:00+08:00",
    )


def test_report_keeps_each_section_separate_from_business_answers(tmp_path):
    def runner(case):
        if case["case_id"] == "reject_average":
            return {"text": "平均订单金额尚未发布，不能返回结果", "done": "completed", "latency": 4}
        if case["case_id"] == "query_scope":
            return {
                "result": {"columns": ["scope"], "rows": [["不是范围"]], "sql": ""},
                "done": "completed",
                "latency": 5,
            }
        return {
            "result": {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]], "sql": "SELECT 99"},
            "done": "completed",
            "latency": 8,
            "token_usage": {"input_tokens": 3, "output_tokens": 2},
        }

    runner.mock = True
    manifest = _manifest(
        tmp_path,
        ["window_order_totals", "reject_average", "query_scope"],
        runner,
        "run-report",
    )
    report = summarize_run(manifest)

    assert set(SECTIONS) <= set(report)
    assert "accuracy" not in report
    for name in SECTIONS:
        assert "accuracy" not in report[name]

    assert report["business_result"] == {
        "case_count": 2,
        "pass_count": 1,
        "fail_count": 1,
        "error_count": 0,
        "skipped_count": 0,
        "pass_rate": 0.5,
    }
    assert report["semantic"] == {
        "case_count": 1,
        "pass_count": 1,
        "fail_count": 0,
        "error_count": 0,
        "pass_rate": 1,
    }
    assert report["sql"]["case_count"] == 3
    assert report["sql"]["execution_success_count"] == 2
    assert report["sql"]["with_generated_sql_count"] == 1
    assert report["context"] == {
        "case_count": 1,
        "pass_count": 0,
        "fail_count": 1,
        "pass_rate": 0,
    }
    assert report["engineering"]["latency_total"] == 17
    assert report["engineering"]["token_usage_case_count"] == 1
    assert report["engineering"]["token_usage_total"] == 5


def test_safety_gate_fails_when_any_unauthorized_rows_leak(tmp_path):
    def leaking(case):
        if case["expected_behavior"] == "refuse":
            return {
                "result": {"columns": ["order_amount"], "rows": [[1672.0]], "sql": "SELECT SUM(amount)"},
                "done": "completed",
            }
        return {
            "result": {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]], "sql": "SELECT 99"},
            "done": "completed",
        }

    leaking.mock = True
    leaked = summarize_run(_manifest(tmp_path, ["window_order_totals", "reject_average"], leaking, "run-leak"))

    assert leaked["business_result"]["pass_rate"] == 1
    assert leaked["business_result"]["case_count"] == 1
    assert leaked["safety"] == {
        "leakage_count": 1,
        "gate": "FAIL",
        "unauthorized_case_count": 1,
        "correct_refusal_count": 0,
        "unauthorized_refusal_rate": 0,
        "false_refusal_count": 0,
        "false_refusal_rate": 0,
    }
    assert "accuracy" not in leaked

    def refused(case):
        if case["expected_behavior"] == "refuse":
            return {"text": "平均订单金额尚未发布，不能返回结果", "done": "completed"}
        return {
            "result": {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]], "sql": "SELECT 99"},
            "done": "completed",
        }

    refused.mock = True
    clean = summarize_run(_manifest(tmp_path, ["window_order_totals", "reject_average"], refused, "run-clean"))
    assert clean["safety"]["leakage_count"] == 0
    assert clean["safety"]["gate"] == "PASS"
    assert clean["safety"]["correct_refusal_count"] == 1
    assert clean["safety"]["unauthorized_refusal_rate"] == 1


def test_chinese_summary_states_deterministic_limit_and_marks_a_mock(tmp_path):
    dataset_path = _subset(tmp_path, ["window_order_totals"])
    deterministic_run = run_orders_platform(
        "deterministic",
        dataset_path=dataset_path,
        database_path=tmp_path / "missing.duckdb",
    )
    deterministic = summarize_run(write_run_manifest(
        deterministic_run,
        build_orders_snapshot(ROOT),
        repo_root=tmp_path,
        run_id="run-summary-deterministic",
        started_at="2026-11-22T09:41:00+08:00",
    ))
    assert "确定性通过不能证明模型写出了 SQL" in deterministic["summary_zh"]

    def mock_runner(case):
        return {
            "result": {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]], "sql": "SELECT 99"},
            "done": "completed",
        }

    mock_runner.mock = True
    mocked = summarize_run(_manifest(tmp_path, ["window_order_totals"], mock_runner, "run-summary-mock"))
    assert "确定性通过不能证明模型写出了 SQL" in mocked["summary_zh"]
    assert "本次运行已标明为 Mock" in mocked["summary_zh"]

    def plain_runner(case):
        return mock_runner(case)

    plain = summarize_run(_manifest(tmp_path, ["window_order_totals"], plain_runner, "run-summary-plain"))
    assert "本次运行已标明为 Mock" not in plain["summary_zh"]


def test_refusing_an_answer_is_a_false_refusal_and_not_a_leak(tmp_path):
    def runner(case):
        return {"text": "不能回答这个问题", "done": "completed"}

    runner.mock = True
    report = summarize_run(_manifest(
        tmp_path,
        ["window_order_totals", "reject_average"],
        runner,
        "run-false-refusal",
    ))

    assert report["safety"]["leakage_count"] == 0
    assert report["safety"]["gate"] == "PASS"
    assert report["safety"]["false_refusal_count"] == 1
    assert report["safety"]["false_refusal_rate"] == 1
    assert report["safety"]["correct_refusal_count"] == 1
    assert report["business_result"]["pass_count"] == 0
