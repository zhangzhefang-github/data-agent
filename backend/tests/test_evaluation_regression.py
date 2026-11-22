"""订单评测回归对比。

缝在 compare_runs：调用方指明基线和候选两份已写好的运行清单，
得到一份可序列化的对比，其中包含中文摘要。比较只读清单，不改 Golden Dataset。
"""

import json
from pathlib import Path

from app.evaluation.regression import compare_runs

PLATFORM_DATASET = Path(__file__).resolve().parents[1] / "app" / "evaluation" / "orders_platform.json"


def test_compare_runs_records_dataset_model_and_semantic_versions_of_both_runs():
    baseline = {
        "dataset_version": "orders-2026-11-22",
        "model_name": "gpt-baseline",
        "model_version": "2026-01-01",
        "prompt_version": "prompt-hash-baseline",
        "semantic_model_version": "orders-semantic-3",
        "semantic_model_hash": "aaa111",
        "cases": [],
    }
    candidate = {
        "dataset_version": "orders-2026-11-23",
        "semantic_model_version": "orders-semantic-4",
        "semantic_model_hash": "bbb222",
        "cases": [],
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["baseline"] == {
        "dataset_version": "orders-2026-11-22",
        "model_name": "gpt-baseline",
        "model_version": "2026-01-01",
        "prompt_version": "prompt-hash-baseline",
        "semantic_model_version": "orders-semantic-3",
        "semantic_model_hash": "aaa111",
    }
    assert compared["candidate"] == {
        "dataset_version": "orders-2026-11-23",
        "model_name": None,
        "model_version": None,
        "prompt_version": None,
        "semantic_model_version": "orders-semantic-4",
        "semantic_model_hash": "bbb222",
    }


def test_pass_to_fail_or_error_is_listed_as_a_regression():
    baseline = {
        "cases": [
            _case("kept", "PASS"),
            _case("broke", "PASS"),
            _case("errored", "PASS"),
            _case("still_failing", "FAIL"),
            _case("recovered", "FAIL"),
            _case("paused", "PASS"),
            _case("only_baseline", "PASS"),
        ]
    }
    candidate = {
        "cases": [
            _case("kept", "PASS"),
            _case("broke", "FAIL"),
            _case("errored", "ERROR"),
            _case("still_failing", "FAIL"),
            _case("recovered", "PASS"),
            _case("paused", "SKIPPED"),
            _case("only_candidate", "FAIL"),
        ]
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["regressions"] == ["broke", "errored"]


def test_newly_passed_lists_cases_the_candidate_passes_when_baseline_did_not():
    baseline = {
        "cases": [
            _case("kept", "PASS"),
            _case("recovered", "FAIL"),
            _case("fixed_error", "ERROR"),
            _case("unskipped", "SKIPPED"),
            _case("broke", "PASS"),
            _case("still_failing", "FAIL"),
        ]
    }
    candidate = {
        "cases": [
            _case("kept", "PASS"),
            _case("recovered", "PASS"),
            _case("fixed_error", "PASS"),
            _case("unskipped", "PASS"),
            _case("broke", "FAIL"),
            _case("still_failing", "FAIL"),
            _case("only_candidate", "PASS"),
        ]
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["newly_passed"] == ["recovered", "fixed_error", "unskipped", "only_candidate"]


def test_newly_failed_lists_cases_the_candidate_fails_when_baseline_did_not():
    baseline = {
        "cases": [
            _case("broke", "PASS"),
            _case("new_fail", "ERROR"),
            _case("from_skip", "SKIPPED"),
            _case("still_failing", "FAIL"),
            _case("errored", "PASS"),
            _case("recovered", "FAIL"),
        ]
    }
    candidate = {
        "cases": [
            _case("broke", "FAIL"),
            _case("new_fail", "FAIL"),
            _case("from_skip", "FAIL"),
            _case("still_failing", "FAIL"),
            _case("errored", "ERROR"),
            _case("recovered", "PASS"),
            _case("only_candidate", "FAIL"),
        ]
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["newly_failed"] == ["broke", "new_fail", "from_skip", "only_candidate"]
    assert compared["regressions"] == ["broke", "errored"]


def test_accuracy_deltas_are_candidate_minus_baseline_and_do_not_replace_regressions():
    baseline = {
        "cases": [
            _case("sem_ok", "PASS", category="权限与语义歧义", expected_behavior="refuse"),
            _case("sem_bad", "FAIL", category="权限与语义歧义", expected_behavior="refuse"),
            _case("ans_ok", "PASS"),
            _case("ans_bad", "FAIL"),
        ]
    }
    candidate = {
        "cases": [
            _case("sem_ok", "PASS", category="权限与语义歧义", expected_behavior="refuse"),
            _case("sem_bad", "PASS", category="权限与语义歧义", expected_behavior="refuse"),
            _case("ans_ok", "FAIL"),
            _case("ans_bad", "FAIL"),
        ]
    }

    moved = compare_runs(baseline=baseline, candidate=candidate)

    assert moved["deltas"]["semantic_accuracy"] == 0.5
    assert moved["deltas"]["result_accuracy"] == -0.5

    swapped = compare_runs(
        baseline={"cases": [_case("broke", "PASS"), _case("recovered", "FAIL")]},
        candidate={"cases": [_case("broke", "FAIL"), _case("recovered", "PASS")]},
    )
    assert swapped["deltas"]["result_accuracy"] == 0
    assert swapped["regressions"] == ["broke"]


def test_accuracy_delta_is_null_when_either_run_has_no_rate():
    baseline = {
        "cases": [
            _case("sem_ok", "PASS", category="权限与语义歧义", expected_behavior="refuse"),
            _case("ans_ok", "PASS"),
        ]
    }
    candidate = {
        "cases": [
            _case("ans_bad", "FAIL"),
        ]
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["deltas"]["semantic_accuracy"] is None
    assert compared["deltas"]["result_accuracy"] == -1

    other_way = compare_runs(baseline=candidate, candidate=baseline)
    assert other_way["deltas"]["semantic_accuracy"] is None
    assert other_way["deltas"]["result_accuracy"] == 1


def test_latency_delta_is_the_change_in_mean_and_null_when_either_side_has_none():
    baseline = {
        "cases": [
            _case("a", "PASS", latency=10),
            _case("b", "PASS", latency=30),
            _case("c", "PASS", latency=None),
        ]
    }
    candidate = {
        "cases": [
            _case("a", "PASS", latency=40),
            _case("b", "PASS", latency=None),
        ]
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["deltas"]["latency"] == 20

    missing = compare_runs(
        baseline=baseline,
        candidate={"cases": [_case("a", "PASS", latency=None), _case("b", "PASS")]},
    )
    assert missing["deltas"]["latency"] is None

    flagged = compare_runs(
        baseline={"cases": [_case("a", "PASS", latency=True)]},
        candidate={"cases": [_case("a", "PASS", latency=8)]},
    )
    assert flagged["deltas"]["latency"] is None


def test_token_delta_is_the_change_in_total_and_null_when_either_side_omits_usage():
    baseline = {
        "cases": [
            _case("a", "PASS", token_usage={"input_tokens": 3, "output_tokens": 2}),
            _case("b", "PASS"),
        ]
    }
    candidate = {
        "cases": [
            _case("a", "PASS", token_usage={"input_tokens": 8, "output_tokens": 4}),
            _case("b", "PASS", token_usage={"input_tokens": 0}),
        ]
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    assert compared["deltas"]["tokens"] == 7

    omitted = compare_runs(
        baseline=baseline,
        candidate={"cases": [_case("a", "PASS"), _case("b", "PASS")]},
    )
    assert omitted["deltas"]["tokens"] is None

    recorded_zero = compare_runs(
        baseline={"cases": [_case("a", "PASS", token_usage={"input_tokens": 0, "output_tokens": 0})]},
        candidate={"cases": [_case("a", "PASS", token_usage={"input_tokens": 4})]},
    )
    assert recorded_zero["deltas"]["tokens"] == 4


def test_summary_zh_names_version_moves_case_lists_and_deltas():
    baseline = {
        "dataset_version": "orders-2026-11-22",
        "model_name": "gpt-baseline",
        "model_version": "2026-01-01",
        "prompt_version": "prompt-hash-baseline",
        "semantic_model_version": "orders-semantic-3",
        "semantic_model_hash": "aaa111",
        "cases": [
            _case("broke", "PASS", latency=10, token_usage={"input_tokens": 3, "output_tokens": 2}),
            _case("recovered", "FAIL", latency=30),
            _case("sem_bad", "FAIL", category="权限与语义歧义", expected_behavior="refuse"),
        ],
    }
    candidate = {
        "dataset_version": "orders-2026-11-23",
        "semantic_model_version": "orders-semantic-4",
        "semantic_model_hash": "bbb222",
        "cases": [
            _case("broke", "FAIL", latency=40, token_usage={"input_tokens": 8, "output_tokens": 4}),
            _case("recovered", "PASS", latency=20),
            _case("sem_bad", "PASS", category="权限与语义歧义", expected_behavior="refuse"),
        ],
    }

    compared = compare_runs(baseline=baseline, candidate=candidate)

    expected = (
        "基线数据集版本为 orders-2026-11-22，候选数据集版本为 orders-2026-11-23。"
        "基线模型名为 gpt-baseline，候选模型名未记录。"
        "基线模型版本为 2026-01-01，候选模型版本未记录。"
        "基线提示版本为 prompt-hash-baseline，候选提示版本未记录。"
        "基线语义模型版本为 orders-semantic-3，候选语义模型版本为 orders-semantic-4。"
        "基线语义模型哈希为 aaa111，候选语义模型哈希为 bbb222。"
        "新增通过 2 条：recovered、sem_bad。"
        "新增失败 1 条：broke。"
        "由通过变失败或错误的回归 1 条：broke。"
        "语义准确率变化（候选减基线）为 1。"
        "结果准确率变化（候选减基线）为 0。"
        "延迟变化（候选减基线）为 10。"
        "token 变化（候选减基线）为 7。"
    )
    assert compared["summary_zh"] == expected
    assert json.loads(json.dumps(compared, ensure_ascii=False))["summary_zh"] == expected

    empty = compare_runs(
        baseline={"dataset_version": "orders-v1", "cases": []},
        candidate={"dataset_version": "orders-v1", "cases": []},
    )
    assert "新增通过 0 条。" in empty["summary_zh"]
    assert "新增失败 0 条。" in empty["summary_zh"]
    assert "由通过变失败或错误的回归 0 条。" in empty["summary_zh"]
    assert "语义准确率变化（候选减基线）无法计算。" in empty["summary_zh"]
    assert "结果准确率变化（候选减基线）无法计算。" in empty["summary_zh"]
    assert "延迟变化（候选减基线）无法计算。" in empty["summary_zh"]
    assert "token 变化（候选减基线）无法计算。" in empty["summary_zh"]
    assert "基线模型名未记录，候选模型名未记录。" in empty["summary_zh"]
    assert "基线提示版本未记录，候选提示版本未记录。" in empty["summary_zh"]


def test_compare_runs_reads_manifest_files_and_leaves_the_golden_dataset_unchanged(tmp_path):
    baseline = {
        "dataset_version": "orders-2026-11-22",
        "model_name": "gpt-baseline",
        "model_version": "2026-01-01",
        "semantic_model_version": "orders-semantic-3",
        "semantic_model_hash": "aaa111",
        "cases": [
            _case("broke", "PASS", latency=10),
            _case("sem_case", "FAIL", category="权限与语义歧义", expected_behavior="refuse"),
        ],
    }
    candidate = {
        "dataset_version": "orders-2026-11-23",
        "model_name": "gpt-candidate",
        "model_version": "2026-02-01",
        "semantic_model_version": "orders-semantic-4",
        "semantic_model_hash": "bbb222",
        "cases": [
            _case("broke", "FAIL", latency=30, token_usage={"input_tokens": 2, "output_tokens": 2}),
            _case("sem_case", "PASS", category="权限与语义歧义", expected_behavior="refuse"),
        ],
    }
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    baseline_bytes = baseline_path.read_bytes()
    candidate_bytes = candidate_path.read_bytes()
    golden_bytes = PLATFORM_DATASET.read_bytes()
    golden_mtime = PLATFORM_DATASET.stat().st_mtime_ns

    compared = compare_runs(baseline=str(baseline_path), candidate=candidate_path)

    assert compared["baseline"]["dataset_version"] == "orders-2026-11-22"
    assert compared["candidate"]["model_name"] == "gpt-candidate"
    assert compared["candidate"]["semantic_model_hash"] == "bbb222"
    assert compared["newly_passed"] == ["sem_case"]
    assert compared["newly_failed"] == ["broke"]
    assert compared["regressions"] == ["broke"]
    assert compared["deltas"]["semantic_accuracy"] == 1
    assert compared["deltas"]["result_accuracy"] == -1
    assert compared["deltas"]["latency"] == 20
    assert compared["deltas"]["tokens"] is None
    assert "由通过变失败或错误的回归 1 条：broke。" in compared["summary_zh"]
    assert baseline_path.read_bytes() == baseline_bytes
    assert candidate_path.read_bytes() == candidate_bytes
    assert PLATFORM_DATASET.read_bytes() == golden_bytes
    assert PLATFORM_DATASET.stat().st_mtime_ns == golden_mtime


def _case(case_id, evaluation_status, **fields):
    case = {
        "case_id": case_id,
        "evaluation_status": evaluation_status,
        "expected_behavior": "answer",
        "category": "基础指标与过滤",
    }
    case.update(fields)
    return case
