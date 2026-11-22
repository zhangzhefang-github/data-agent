"""比较两份已写好的运行清单。只读，不改 Golden Dataset。"""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.report import summarize_run

_VERSION_FIELDS = (
    "dataset_version",
    "model_name",
    "model_version",
    "semantic_model_version",
    "semantic_model_hash",
)


def compare_runs(*, baseline, candidate):
    """比较基线和候选。参数可以是清单字典，或已写好的 JSON 文件。只读。"""
    baseline = _load_manifest(baseline)
    candidate = _load_manifest(candidate)
    compared = {
        "baseline": _identity(baseline),
        "candidate": _identity(candidate),
        "newly_passed": _newly_passed(baseline, candidate),
        "newly_failed": _newly_failed(baseline, candidate),
        "regressions": _regressions(baseline, candidate),
        "deltas": _deltas(baseline, candidate),
    }
    compared["summary_zh"] = _summary_zh(compared)
    return compared


def _newly_passed(baseline, candidate):
    """候选通过，且基线这条不是通过（含基线没有这条）。"""
    baseline_status = _status_by_id(baseline)
    passed = []
    for case in candidate.get("cases") or []:
        previous = baseline_status.get(case.get("case_id"))
        if case.get("evaluation_status") == "PASS" and previous != "PASS":
            passed.append(case.get("case_id"))
    return passed


def _newly_failed(baseline, candidate):
    """候选失败，且基线这条不是失败（含基线没有这条）。"""
    baseline_status = _status_by_id(baseline)
    failed = []
    for case in candidate.get("cases") or []:
        previous = baseline_status.get(case.get("case_id"))
        if case.get("evaluation_status") == "FAIL" and previous != "FAIL":
            failed.append(case.get("case_id"))
    return failed


def _regressions(baseline, candidate):
    """基线通过、候选失败或错误，才算由通过变失败的回归。"""
    baseline_status = _status_by_id(baseline)
    regressions = []
    for case in candidate.get("cases") or []:
        previous = baseline_status.get(case.get("case_id"))
        current = case.get("evaluation_status")
        if previous == "PASS" and current in ("FAIL", "ERROR"):
            regressions.append(case.get("case_id"))
    return regressions


def _deltas(baseline, candidate):
    """差值取候选减基线。任一侧没有值时为空，不用 0 代替缺失的延迟。"""
    base = summarize_run(baseline)
    cand = summarize_run(candidate)
    return {
        "semantic_accuracy": _subtract(cand["semantic"]["pass_rate"], base["semantic"]["pass_rate"]),
        "result_accuracy": _subtract(cand["business_result"]["pass_rate"], base["business_result"]["pass_rate"]),
        "latency": _subtract(cand["engineering"]["latency_mean"], base["engineering"]["latency_mean"]),
        "tokens": _subtract(_recorded_token_total(cand), _recorded_token_total(base)),
    }


def _recorded_token_total(summary):
    """清单省略 token_usage 表示没有用量。整份运行都没有时，总量为空而不是 0。"""
    engineering = summary["engineering"]
    if engineering["token_usage_case_count"] == 0:
        return None
    return engineering["token_usage_total"]


def _subtract(candidate_value, baseline_value):
    """任一侧没有值时返回空，不用 0 代替缺失。"""
    if candidate_value is None or baseline_value is None:
        return None
    return candidate_value - baseline_value


def _status_by_id(manifest):
    return {
        case.get("case_id"): case.get("evaluation_status")
        for case in manifest.get("cases") or []
    }


def _load_manifest(source):
    """读取调用方给出的清单。字典原样使用，文件只读打开。"""
    if isinstance(source, dict):
        return source
    loaded = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("运行清单必须是 JSON 对象")
    return loaded


def _identity(manifest):
    return {field: manifest.get(field) for field in _VERSION_FIELDS}


_VERSION_LABELS = (
    ("dataset_version", "数据集版本"),
    ("model_name", "模型名"),
    ("model_version", "模型版本"),
    ("semantic_model_version", "语义模型版本"),
    ("semantic_model_hash", "语义模型哈希"),
)


def _summary_zh(compared):
    sentences = [
        f"{_version_clause('基线', label, compared['baseline'][field])}，{_version_clause('候选', label, compared['candidate'][field])}"
        for field, label in _VERSION_LABELS
    ]
    sentences.append(_list_sentence("新增通过", compared["newly_passed"]))
    sentences.append(_list_sentence("新增失败", compared["newly_failed"]))
    sentences.append(_list_sentence("由通过变失败或错误的回归", compared["regressions"]))
    deltas = compared["deltas"]
    sentences.append(_delta_sentence("语义准确率", deltas["semantic_accuracy"]))
    sentences.append(_delta_sentence("结果准确率", deltas["result_accuracy"]))
    sentences.append(_delta_sentence("延迟", deltas["latency"]))
    # 中文句子是「token 变化」，标签和「变化」之间留一个空格。
    sentences.append(_delta_sentence("token ", deltas["tokens"]))
    return "。".join(sentences) + "。"


def _version_clause(role, label, value):
    if value is None or value == "":
        return f"{role}{label}未记录"
    return f"{role}{label}为 {value}"


def _list_sentence(label, case_ids):
    if not case_ids:
        return f"{label} 0 条"
    joined = "、".join(str(case_id) for case_id in case_ids)
    return f"{label} {len(case_ids)} 条：{joined}"


def _delta_sentence(label, value):
    if value is None:
        return f"{label}变化（候选减基线）无法计算"
    return f"{label}变化（候选减基线）为 {_format_number(value)}"


def _format_number(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
