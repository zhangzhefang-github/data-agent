"""订单平台数据集的三种运行模式。

缝在 run_orders_platform：给定平台数据集和一种模式，观察报告中的模式、
评测结果，以及这份结果是否被说成模型会写 SQL。
"""

import json
import os
import sys
from pathlib import Path

import pytest

from app.evaluation.platform_run import run_orders_platform
from app.sql_prompt import orders_query_scope

ROOT = Path(__file__).resolve().parents[2]

PLATFORM_DATASET = Path(__file__).resolve().parents[1] / "app" / "evaluation" / "orders_platform.json"
ENV_FILE = Path("/home/zzf/data-agent/.env")


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


def _platform_subset(tmp_path, cases):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    dataset["cases"] = cases
    path = tmp_path / "orders_platform.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    return path


def test_run_returns_a_manifest_and_a_sectioned_report(tmp_path):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    window = next(case for case in dataset["cases"] if case["case_id"] == "window_order_totals")
    dataset_path = _platform_subset(tmp_path, [window])

    def fake_runner(case):
        return {
            "result": {"columns": ["order_rows", "order_amount"], "rows": [[99, 1672.0]], "sql": "SELECT 1"},
            "done": "completed",
        }

    fake_runner.mock = True
    report = run_orders_platform(
        "model",
        runner=fake_runner,
        dataset_path=dataset_path,
        repo_root=tmp_path,
    )

    assert report["proves_model_sql"] is False
    assert report["mock"] is True
    assert report["cases"][0]["evaluation_status"] == "PASS"
    assert report["cases"][0]["actual_rows"] == [[99, 1672.0]]
    manifest = report["manifest"]
    assert manifest["mode"] == "model"
    assert manifest["mock"] is True
    assert manifest["proves_model_sql"] is False
    assert manifest["dataset_version"] == report["dataset_version"]
    written = tmp_path / "var" / "evaluation-runs" / f"{manifest['run_id']}.json"
    assert json.loads(written.read_text(encoding="utf-8")) == manifest
    assert set(report["report"]) >= {
        "semantic",
        "sql",
        "business_result",
        "safety",
        "context",
        "engineering",
        "summary_zh",
    }
    assert report["report"]["business_result"]["pass_count"] == 1
    assert "综合准确率" not in report["report"]["summary_zh"]


def test_pinned_model_or_semantic_version_mismatch_is_error_not_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-eval-named")
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    window = next(case for case in dataset["cases"] if case["case_id"] == "window_order_totals")
    dataset_path = _platform_subset(tmp_path, [window])

    def fake_runner(case):
        return {
            "result": {"columns": ["order_rows", "order_amount"], "rows": [[99, 1672.0]], "sql": "SELECT 1"},
            "done": "completed",
        }

    fake_runner.mock = True
    wrong_model = run_orders_platform(
        "model",
        runner=fake_runner,
        dataset_path=dataset_path,
        repo_root=tmp_path,
        model_version="not-the-configured-model",
    )
    assert wrong_model["summary"]["PASS"] == 0
    assert wrong_model["summary"]["ERROR"] == 1
    assert wrong_model["cases"][0]["evaluation_status"] == "ERROR"
    assert wrong_model["manifest"]["model_version"] is None
    assert wrong_model["proves_model_sql"] is False

    wrong_semantic = run_orders_platform(
        "model",
        runner=fake_runner,
        dataset_path=dataset_path,
        repo_root=tmp_path,
        semantic_model_version="not-the-orders-snapshot",
    )
    assert wrong_semantic["summary"]["PASS"] == 0
    assert wrong_semantic["cases"][0]["evaluation_status"] == "ERROR"
    assert wrong_semantic["manifest"]["model_version"] is None

    matched = run_orders_platform(
        "model",
        runner=fake_runner,
        dataset_path=dataset_path,
        repo_root=tmp_path,
        model_version="gpt-eval-named",
        semantic_model_version="1.0",
    )
    assert matched["cases"][0]["evaluation_status"] == "PASS"
    assert matched["cases"][0]["actual_rows"] == [[99, 1672.0]]
    assert matched["manifest"]["model_version"] is None
    assert matched["mock"] is True


def test_live_manifest_records_only_a_supplied_model_version(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-eval-named")
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    window = next(case for case in dataset["cases"] if case["case_id"] == "window_order_totals")
    dataset_path = _platform_subset(tmp_path, [window])

    omitted = run_orders_platform("live", dataset_path=dataset_path, repo_root=tmp_path)
    assert omitted["summary"]["SKIPPED"] == 1
    assert omitted["summary"]["PASS"] == 0
    assert omitted["manifest"]["model_version"] is None
    assert omitted["proves_model_sql"] is False

    pinned = run_orders_platform(
        "live",
        dataset_path=dataset_path,
        repo_root=tmp_path,
        model_version="gpt-eval-named",
    )
    assert pinned["summary"]["SKIPPED"] == 1
    assert pinned["summary"]["PASS"] == 0
    assert pinned["manifest"]["mode"] == "live"
    assert pinned["manifest"]["model_name"] == "gpt-eval-named"
    assert pinned["manifest"]["model_version"] == "gpt-eval-named"
    assert pinned["manifest"]["mock"] is False


def test_dataset_version_mismatch_is_error_and_not_a_pass():
    report = run_orders_platform("deterministic", dataset_version="not-the-dataset")

    assert report["proves_model_sql"] is False
    assert report["summary"]["PASS"] == 0
    assert report["summary"]["ERROR"] == len(report["cases"])
    assert report["cases"]


def test_deterministic_snapshot_mismatch_is_error_and_does_not_prove_model_sql(tmp_path):
    database = tmp_path / "not-the-snapshot.duckdb"
    database.write_bytes(b"not a snapshot")

    report = run_orders_platform("deterministic", database_path=database)

    assert report["mode"] == "deterministic"
    assert report["proves_model_sql"] is False
    assert report["mock"] is False
    assert report["summary"]["PASS"] == 0
    assert report["cases"]
    assert report["summary"]["ERROR"] == len(report["cases"])
    assert all(case["evaluation_status"] == "ERROR" for case in report["cases"])
    assert all(case["proves_model_sql"] is False for case in report["cases"])
    window = next(case for case in report["cases"] if case["id"] == "window_order_totals")
    assert window["evaluation_status"] == "ERROR"


def test_deterministic_answer_passes_only_when_reference_sql_matches_the_snapshot(tmp_path):
    database = _duckdb_path()
    if database is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    window = next(case for case in dataset["cases"] if case["case_id"] == "window_order_totals")
    wrong = json.loads(json.dumps(window))
    wrong["case_id"] = "window_order_totals_wrong"
    wrong["expected_result"]["rows"] = [[1, 1.0]]
    dataset_path = _platform_subset(tmp_path, [window, wrong])

    report = run_orders_platform("deterministic", dataset_path=dataset_path, database_path=database)

    assert report["mode"] == "deterministic"
    assert report["proves_model_sql"] is False
    assert report["mock"] is False
    by_id = {case["id"]: case for case in report["cases"]}
    matched = by_id["window_order_totals"]
    assert matched["evaluation_status"] == "PASS"
    assert matched["proves_model_sql"] is False
    assert matched["actual_rows"] == [[99, 1672.0]]
    assert by_id["window_order_totals_wrong"]["evaluation_status"] == "FAIL"
    assert report["summary"]["PASS"] == 1
    assert report["summary"]["FAIL"] == 1
    assert report["summary"]["ERROR"] == 0


def test_deterministic_refusal_passes_only_when_the_reference_sql_is_rejected(tmp_path):
    database = _duckdb_path()
    if database is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    refused = next(case for case in dataset["cases"] if case["case_id"] == "reject_average")
    leaked = json.loads(json.dumps(refused))
    leaked["case_id"] = "reject_average_returns_rows"
    leaked["expected_sql"] = next(
        case["expected_sql"] for case in dataset["cases"] if case["case_id"] == "window_order_totals"
    )
    dataset_path = _platform_subset(tmp_path, [refused, leaked])

    report = run_orders_platform("deterministic", dataset_path=dataset_path, database_path=database)

    by_id = {case["id"]: case for case in report["cases"]}
    assert by_id["reject_average"]["evaluation_status"] == "PASS"
    assert by_id["reject_average"]["proves_model_sql"] is False
    assert by_id["reject_average"]["actual_rows"] == []
    assert by_id["reject_average_returns_rows"]["evaluation_status"] == "FAIL"
    assert by_id["reject_average_returns_rows"]["actual_rows"] == [[99, 1672.0]]
    assert report["summary"]["PASS"] == 1
    assert report["proves_model_sql"] is False


def test_deterministic_validator_correction_is_not_a_clarification(tmp_path):
    database = _duckdb_path()
    if database is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    by_case = {case["case_id"]: case for case in dataset["cases"]}
    named = by_case["clarify_customer_name"]
    empty = by_case["clarify_success_status"]
    answered = json.loads(json.dumps(named))
    answered["case_id"] = "clarify_customer_name_returns_rows"
    answered["expected_sql"] = by_case["window_order_totals"]["expected_sql"]
    dataset_path = _platform_subset(tmp_path, [named, empty, answered])

    report = run_orders_platform("deterministic", dataset_path=dataset_path, database_path=database)

    by_id = {case["id"]: case for case in report["cases"]}
    assert by_id["clarify_customer_name"]["evaluation_status"] == "ERROR"
    assert by_id["clarify_customer_name"]["execution_status"] == "validator_error"
    assert by_id["clarify_success_status"]["evaluation_status"] == "SKIPPED"
    assert by_id["clarify_customer_name_returns_rows"]["evaluation_status"] == "FAIL"
    assert by_id["clarify_customer_name_returns_rows"]["actual_rows"] == [[99, 1672.0]]
    assert report["summary"]["PASS"] == 0
    assert report["summary"]["ERROR"] == 1
    assert report["summary"]["SKIPPED"] == 1
    assert report["summary"]["FAIL"] == 1
    assert report["proves_model_sql"] is False


def test_deterministic_scope_answer_uses_the_authorized_text(tmp_path):
    database = _duckdb_path()
    if database is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    scope = next(case for case in dataset["cases"] if case["case_id"] == "query_scope")
    scope["expected_result"]["rows"] = [[orders_query_scope(ROOT)]]
    wrong = json.loads(json.dumps(scope))
    wrong["case_id"] = "query_scope_wrong"
    wrong["expected_result"]["rows"] = [["不是可查询范围"]]
    dataset_path = _platform_subset(tmp_path, [scope, wrong])

    report = run_orders_platform("deterministic", dataset_path=dataset_path, database_path=database)

    by_id = {case["id"]: case for case in report["cases"]}
    assert by_id["query_scope"]["evaluation_status"] == "PASS"
    assert by_id["query_scope"]["proves_model_sql"] is False
    assert by_id["query_scope"]["actual_rows"] == [[orders_query_scope(ROOT)]]
    assert by_id["query_scope_wrong"]["evaluation_status"] == "FAIL"
    assert report["proves_model_sql"] is False


def test_deterministic_platform_dataset_runs_without_the_sales_golden(monkeypatch):
    database = _duckdb_path()
    if database is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")
    from app.evaluation import runner

    def sales_golden_is_not_used():
        raise AssertionError("销售 golden.json 不应被订单平台运行读取")

    monkeypatch.setattr(runner, "load_dataset", sales_golden_is_not_used)
    monkeypatch.setattr(runner, "load_orders_dataset", sales_golden_is_not_used)

    report = run_orders_platform("deterministic", database_path=database)

    assert report["mode"] == "deterministic"
    assert report["dataset_id"] == "orders_platform"
    assert report["proves_model_sql"] is False
    assert report["mock"] is False
    assert len(report["cases"]) == 60
    assert all(case["proves_model_sql"] is False for case in report["cases"])
    window = next(case for case in report["cases"] if case["id"] == "window_order_totals")
    assert window["evaluation_status"] == "PASS"
    assert window["actual_rows"] == [[99, 1672.0]]
    skipped = sorted(case["id"] for case in report["cases"] if case["evaluation_status"] == "SKIPPED")
    assert skipped == [
        "clarify_highest_customer_metric",
        "clarify_order_plus_payment",
        "clarify_success_status",
    ]
    failed = [
        case["id"] for case in report["cases"] if case["evaluation_status"] not in {"PASS", "SKIPPED"}
    ]
    assert failed == ["clarify_customer_name", "clarify_customer_count"]
    assert report["summary"] == {"PASS": 55, "FAIL": 0, "ERROR": 2, "SKIPPED": 3}


def test_model_mode_scores_an_injected_runner_and_marks_the_mock(monkeypatch, tmp_path):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    by_case = {case["case_id"]: case for case in dataset["cases"]}
    dataset_path = _platform_subset(tmp_path, [by_case["window_order_totals"], by_case["reject_average"]])

    def fake_runner(case):
        if case["expected_behavior"] == "refuse":
            return {
                "result": {"columns": ["order_amount"], "rows": [[1672.0]], "sql": "SELECT SUM(amount)"},
                "done": "completed",
            }
        return {
            "result": {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]], "sql": "SELECT 1"},
            "done": "completed",
        }

    fake_runner.mock = True

    def reference_sql_is_not_executed(*args, **kwargs):
        raise AssertionError("模型模式不能执行参考 SQL")

    monkeypatch.setattr("app.evaluation.platform_run.execute_order_sql", reference_sql_is_not_executed)
    monkeypatch.setattr("app.direct_sql.execute_order_sql", reference_sql_is_not_executed)

    report = run_orders_platform("model", runner=fake_runner, dataset_path=dataset_path)

    assert report["mode"] == "model"
    assert report["mock"] is True
    assert report["proves_model_sql"] is False
    by_id = {case["id"]: case for case in report["cases"]}
    assert by_id["window_order_totals"]["evaluation_status"] == "PASS"
    assert by_id["window_order_totals"]["proves_model_sql"] is False
    assert by_id["window_order_totals"]["actual_rows"] == [[1672.0, 99]]
    assert by_id["reject_average"]["evaluation_status"] == "FAIL"
    assert report["summary"]["PASS"] == 1
    assert report["summary"]["FAIL"] == 1
    assert report["summary"]["SKIPPED"] == 0

    def real_runner(case):
        return fake_runner(case)

    unmarked = run_orders_platform("model", runner=real_runner, dataset_path=dataset_path)
    assert unmarked["mock"] is False
    assert unmarked["proves_model_sql"] is False


def test_live_mode_without_api_key_is_skipped_and_not_counted_as_pass(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def reference_sql_is_not_executed(*args, **kwargs):
        raise AssertionError("真实模式缺密钥时不能执行参考 SQL")

    monkeypatch.setattr("app.evaluation.platform_run.execute_order_sql", reference_sql_is_not_executed)

    report = run_orders_platform("live")

    assert report["mode"] == "live"
    assert report["mock"] is False
    assert report["proves_model_sql"] is False
    assert report["cases"]
    assert all(case["evaluation_status"] == "SKIPPED" for case in report["cases"])
    assert all(case["proves_model_sql"] is False for case in report["cases"])
    assert report["summary"]["PASS"] == 0
    assert report["summary"]["SKIPPED"] == len(report["cases"])
    assert report["summary"]["FAIL"] == 0
    assert report["summary"]["ERROR"] == 0


def test_live_mode_scores_the_chat_path_and_ignores_reference_sql_and_mock(monkeypatch, tmp_path):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    by_case = {case["case_id"]: case for case in dataset["cases"]}
    selected = [by_case["window_order_totals"], by_case["reject_average"]]
    dataset_path = _platform_subset(tmp_path, selected)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SALES_DUCKDB_PATH", "/tmp/jaffle_shop.duckdb")
    seen = []

    def fake_send(client, question, conversation_id=None):
        seen.append(question)
        if "平均订单金额" in question:
            return {
                "result": {"columns": ["order_amount"], "rows": [[1672.0]], "sql": "SELECT SUM(amount)"},
                "done": "completed",
            }
        return {
            "result": {"columns": ["order_rows", "order_amount"], "rows": [[99, 1672.0]], "sql": "SELECT COUNT(*)"},
            "done": "completed",
        }

    def reference_sql_is_not_executed(*args, **kwargs):
        raise AssertionError("真实模式不能执行参考 SQL")

    def mock_runner(case):
        raise AssertionError("真实模式不能使用 Mock Runner")

    mock_runner.mock = True
    monkeypatch.setattr("app.evaluation.runner.send", fake_send)
    monkeypatch.setattr("app.evaluation.platform_run.execute_order_sql", reference_sql_is_not_executed)

    report = run_orders_platform("live", runner=mock_runner, dataset_path=dataset_path)

    assert report["mode"] == "live"
    assert report["mock"] is False
    by_id = {case["id"]: case for case in report["cases"]}
    assert by_id["window_order_totals"]["evaluation_status"] == "PASS"
    assert by_id["window_order_totals"]["proves_model_sql"] is True
    assert by_id["window_order_totals"]["actual_rows"] == [[99, 1672.0]]
    assert by_id["reject_average"]["evaluation_status"] == "FAIL"
    assert by_id["reject_average"]["proves_model_sql"] is False
    assert report["summary"]["PASS"] == 1
    assert seen == [case["user_question"] for case in selected]


def test_live_followup_sends_the_previous_question_as_its_own_turn(monkeypatch, tmp_path):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    followup = next(case for case in dataset["cases"] if case["case_id"] == "followup_prior_year")
    dataset_path = _platform_subset(tmp_path, [followup])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SALES_DUCKDB_PATH", "/tmp/jaffle_shop.duckdb")
    seen = []

    def fake_send(client, question, conversation_id=None):
        seen.append(question)
        if conversation_id is None:
            return {
                "conversation_id": "conv-1",
                "result": {
                    "columns": ["order_rows", "order_amount"],
                    "rows": [[99, 1672.0]],
                    "sql": "SELECT 1",
                },
                "done": "completed",
            }
        return {
            "result": {"columns": ["order_rows", "order_amount"], "rows": [], "sql": "SELECT 1"},
            "done": "completed",
        }

    monkeypatch.setattr("app.evaluation.runner.send", fake_send)
    report = run_orders_platform("live", dataset_path=dataset_path, repo_root=tmp_path)

    assert seen == [followup["previous_question"], followup["user_question"]]
    assert followup["user_question"] == "去年同期"
    assert report["cases"][0]["evaluation_status"] == "PASS"
    assert report["proves_model_sql"] is True
    assert report["mock"] is False


def test_deterministic_loads_the_duckdb_path_without_an_explicit_argument(monkeypatch, tmp_path):
    database = _duckdb_path()
    if database is None:
        pytest.skip("SALES_DUCKDB_PATH 指向的 DuckDB 不存在，跳过参考 SQL 重执行")
    monkeypatch.delenv("SALES_DUCKDB_PATH", raising=False)
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    window = next(case for case in dataset["cases"] if case["case_id"] == "window_order_totals")
    dataset_path = _platform_subset(tmp_path, [window])

    report = run_orders_platform("deterministic", dataset_path=dataset_path)

    assert report["cases"][0]["evaluation_status"] == "PASS"
    assert report["cases"][0]["actual_rows"] == [[99, 1672.0]]
    assert report["proves_model_sql"] is False


def test_cli_default_remains_sales_deterministic_and_orders_stays_legacy(monkeypatch, capsys):
    from app.evaluation import runner

    monkeypatch.setattr(sys, "argv", ["runner"])
    assert runner.main() == 0
    sales = json.loads(capsys.readouterr().out)
    assert sales["mode"] == "deterministic"
    assert sales.get("dataset_id") != "orders_platform"
    assert all(case["status"] == "passed" for case in sales["cases"])

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SALES_DUCKDB_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["runner", "--orders"])
    assert runner.main() == 0
    legacy = json.loads(capsys.readouterr().out)
    assert legacy["mode"] == "orders"
    legacy_ids = {case["id"] for case in legacy["cases"]}
    assert "window_totals" in legacy_ids
    assert "window_order_totals" not in legacy_ids
    assert legacy["summary"]["PASS"] == 0


def test_cli_platform_selects_one_mode_and_does_not_pass_a_mock(monkeypatch, capsys):
    from app.evaluation import runner

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["runner", "--platform", "live", "--split", "holdout"])
    assert runner.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "live"
    assert report["dataset_id"] == "orders_platform"
    assert report["mock"] is False
    assert report["proves_model_sql"] is False
    assert report["summary"]["PASS"] == 0
    assert report["summary"]["SKIPPED"] == len(report["cases"])
    assert "window_order_totals" not in {case["id"] for case in report["cases"]}
    assert {case["id"] for case in report["cases"]} == {
        case["case_id"]
        for case in json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))["cases"]
        if case["split"] == "holdout"
    }

    monkeypatch.setattr(sys, "argv", ["runner", "--platform", "model"])
    with pytest.raises(SystemExit) as caught:
        runner.main()
    assert caught.value.code != 0
    assert "PASS" not in capsys.readouterr().out


def test_conversation_evaluation_continues_after_structured_clarification():
    from app.evaluation.platform_run import _chat_observation

    calls = []
    def send(client, question, conversation_id=None):
        calls.append((question, conversation_id))
        if conversation_id is None:
            return {'conversation_id': 'session', 'outcome': {'status': 'clarification_required', 'reason': '选口径'}}
        return {'outcome': {'status': 'success', 'reason': '完成'}, 'result': {'rows': [[1]]}}

    result = _chat_observation(None, {'previous_question': '最高的客户是谁', 'question': '按金额'}, send)
    assert calls == [('最高的客户是谁', None), ('按金额', 'session')]
    assert result['result']['rows'] == [[1]]
