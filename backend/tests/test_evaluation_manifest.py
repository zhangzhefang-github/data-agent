"""订单评测运行清单。

缝在 write_run_manifest：给定一次平台运行和订单语义快照，写出一份 UTF-8 JSON。
密钥、数据库家目录路径和模型是否写出 SQL，都在这份清单上观察。
"""

import json
from pathlib import Path

from app.evaluation.manifest import write_run_manifest
from app.evaluation.platform_run import run_orders_platform
from app.semantic.snapshot import build_orders_snapshot

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DATASET = Path(__file__).resolve().parents[1] / "app" / "evaluation" / "orders_platform.json"

# 独立于清单实现、预先对当前订单提示文本做的 SHA-256。仓库没有已发布的提示版本。
ORDERS_PROMPT_HASH = "749f1c33cf022d41769ed85e0d87f784f1c5f6891aad8596f6feba7683a4c1bd"
SEMANTIC_MODEL_HASH = "5eceaf7f7a33cd2a0b36996ce61cf9314b50a941cfcf324ae5308102f76f4792"
AUTHORIZATION_HASH = "1451c8c4c3de4143fa15cd71b3d0347921a18faed996976d2ac3d555104bea75"


def _subset(tmp_path, case_ids):
    dataset = json.loads(PLATFORM_DATASET.read_text(encoding="utf-8"))
    wanted = set(case_ids)
    dataset["cases"] = [case for case in dataset["cases"] if case["case_id"] in wanted]
    path = tmp_path / "orders_platform.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    return path


def _mock_runner(case):
    return {
        "result": {"columns": ["order_rows", "order_amount"], "rows": [[99, 1672.0]], "sql": "SELECT 99"},
        "done": "completed",
    }


_mock_runner.mock = True


def test_manifest_writes_one_utf8_file_with_run_lineage(tmp_path):
    dataset_path = _subset(tmp_path, ["window_order_totals"])
    run = run_orders_platform("model", runner=_mock_runner, dataset_path=dataset_path)
    snapshot = build_orders_snapshot(ROOT)

    manifest = write_run_manifest(
        run,
        snapshot,
        repo_root=tmp_path,
        run_id="run-32",
        started_at="2026-11-22T09:29:00+08:00",
    )

    path = tmp_path / "var" / "evaluation-runs" / "run-32.json"
    text = path.read_text(encoding="utf-8")
    written = json.loads(text)

    assert manifest["run_id"] == "run-32"
    assert written == manifest
    assert written["started_at"] == "2026-11-22T09:29:00+08:00"
    assert written["dataset_version"] == "2026-11-22"
    assert written["model_provider"] is None
    assert written["model_name"] is None
    assert written["model_version"] is None
    assert written["prompt_version"] == ORDERS_PROMPT_HASH
    assert written["prompt_hash"] == ORDERS_PROMPT_HASH
    assert written["semantic_model_version"] == "1.0"
    assert written["semantic_model_hash"] == SEMANTIC_MODEL_HASH
    assert written["semantic_model_hash"] == snapshot.model_hash
    assert written["authorization_version"] == "1451c8c4c3de"
    assert written["authorization_hash"] == AUTHORIZATION_HASH
    assert written["authorization_hash"] == snapshot.authorization_hash
    assert written["context_snapshot_id"] == "2659fcad3d0cfef5"
    assert written["context_snapshot_id"] == snapshot.snapshot_id
    assert written["dialect_version"] == "v1."
    assert written["dialect_version"] == snapshot.dialect_version
    assert written["database_id"] == "jaffle_shop_duckdb"
    assert written["dataset_snapshot_id"] == "jaffle_shop.duckdb@2859dd1db78e5c19"
    assert written["evaluator_version"] == "3.1.0"
    assert written["mode"] == "model"
    assert written["proves_model_sql"] is False
    assert written["mock"] is True
    assert "/home/" not in text
    assert "sk-" not in text


def test_manifest_records_case_results_and_omits_missing_token_usage(tmp_path):
    dataset_path = _subset(tmp_path, ["window_order_totals", "reject_average"])

    def runner(case):
        if case["case_id"] == "reject_average":
            return {"error": {"code": "model_error", "message": "模型没有返回 SQL"}, "done": "failed", "latency": 9}
        return {
            "result": {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]], "sql": "SELECT 99"},
            "done": "completed",
            "latency": 17,
            "token_usage": {"input_tokens": 11, "output_tokens": 4},
        }

    runner.mock = True
    run = run_orders_platform("model", runner=runner, dataset_path=dataset_path)
    manifest = write_run_manifest(
        run,
        build_orders_snapshot(ROOT),
        repo_root=tmp_path,
        run_id="run-cases",
        started_at="2026-11-22T09:30:00+08:00",
    )

    by_id = {case["case_id"]: case for case in manifest["cases"]}
    answered = by_id["window_order_totals"]
    assert answered["generated_sql"] == "SELECT 99"
    assert answered["execution_status"] == "success"
    assert answered["evaluation_status"] == "PASS"
    assert answered["actual_result"] == {"columns": ["order_amount", "order_rows"], "rows": [[1672.0, 99]]}
    assert answered["expected_result"] == {"columns": ["order_rows", "order_amount"], "rows": [[99, 1672.0]]}
    assert answered["latency"] == 17
    assert answered["token_usage"] == {"input_tokens": 11, "output_tokens": 4}
    assert answered["error_details"] is None
    assert answered["leaked"] == 0

    failed = by_id["reject_average"]
    assert failed["generated_sql"] == ""
    assert failed["execution_status"] == "model_error"
    assert failed["evaluation_status"] == "ERROR"
    assert failed["latency"] == 9
    assert "token_usage" not in failed
    assert failed["error_details"] == {"code": "model_error", "message": "模型没有返回 SQL"}
    assert failed["leaked"] == 0


def test_manifest_file_omits_a_key_planted_in_the_observation(tmp_path, monkeypatch):
    planted_token = "sk-planted-manifest-key-123456"
    planted_key = "planted-api-key-not-a-token"
    monkeypatch.setenv("OPENAI_API_KEY", planted_key)
    dataset_path = _subset(tmp_path, ["window_order_totals"])

    def runner(case):
        return {
            "result": {
                "columns": ["order_rows", "order_amount"],
                "rows": [[99, 1672.0]],
                "sql": f"SELECT '{planted_token}'",
            },
            "done": "completed",
            "latency": 3,
            "token_usage": {"note": planted_key},
        }

    runner.mock = True
    run = run_orders_platform("model", runner=runner, dataset_path=dataset_path)
    write_run_manifest(
        run,
        build_orders_snapshot(ROOT),
        repo_root=tmp_path,
        run_id="run-secret",
        started_at="2026-11-22T09:31:00+08:00",
    )

    text = (tmp_path / "var" / "evaluation-runs" / "run-secret.json").read_text(encoding="utf-8")
    assert planted_token not in text
    assert planted_key not in text
    assert "sk-" not in text
    assert "[REDACTED]" in text


def test_deterministic_manifest_does_not_prove_the_model_wrote_sql(tmp_path):
    dataset_path = _subset(tmp_path, ["window_order_totals"])
    run = run_orders_platform(
        "deterministic",
        dataset_path=dataset_path,
        database_path=tmp_path / "missing.duckdb",
    )
    manifest = write_run_manifest(
        run,
        build_orders_snapshot(ROOT),
        repo_root=tmp_path,
        run_id="run-deterministic",
        started_at="2026-11-22T09:32:00+08:00",
    )

    assert manifest["mode"] == "deterministic"
    assert manifest["proves_model_sql"] is False
    assert manifest["mock"] is False
    assert manifest["model_name"] is None


def test_live_manifest_records_the_configured_model_name_without_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-eval-named")
    dataset_path = _subset(tmp_path, ["window_order_totals"])
    run = run_orders_platform("live", dataset_path=dataset_path)
    manifest = write_run_manifest(
        run,
        build_orders_snapshot(ROOT),
        repo_root=tmp_path,
        run_id="run-live",
        started_at="2026-11-22T09:33:00+08:00",
    )
    text = (tmp_path / "var" / "evaluation-runs" / "run-live.json").read_text(encoding="utf-8")

    assert run["mock"] is False
    assert manifest["mode"] == "live"
    assert manifest["mock"] is False
    assert manifest["proves_model_sql"] is False
    assert manifest["model_provider"] == "openai"
    assert manifest["model_name"] == "gpt-eval-named"
    assert manifest["model_version"] is None
    assert "gpt-eval-named" in text
    assert "sk-" not in text
