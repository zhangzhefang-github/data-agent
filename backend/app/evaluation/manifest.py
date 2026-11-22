"""把一次订单平台运行写成一份不含密钥的 JSON 清单。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from app.sql_prompt import render_direct_sql_prompt

EVALUATOR_VERSION = "3.1.0"
DATABASE_ID = "jaffle_shop_duckdb"
REPO_ROOT = Path(__file__).resolve().parents[3]
_SK_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]+")


def write_run_manifest(run, snapshot, *, run_id, started_at, repo_root=None):
    """从平台运行和订单语义快照写出 var/evaluation-runs/<run_id>.json。"""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    provider, model_name, model_version = _model_identity(run)
    manifest = {
        "run_id": run_id,
        "started_at": started_at,
        "dataset_version": run["dataset_version"],
        "model_provider": provider,
        "model_name": model_name,
        "model_version": model_version,
        "prompt_version": None,
        "prompt_hash": _orders_prompt_hash(snapshot),
        "semantic_model_version": snapshot.model_version,
        "semantic_model_hash": snapshot.model_hash,
        "authorization_version": snapshot.authorization_version,
        "authorization_hash": snapshot.authorization_hash,
        "context_snapshot_id": snapshot.snapshot_id,
        "dialect_version": snapshot.dialect_version,
        "database_id": DATABASE_ID,
        "dataset_snapshot_id": run["dataset_snapshot_id"],
        "evaluator_version": EVALUATOR_VERSION,
        "mode": run["mode"],
        "proves_model_sql": run["proves_model_sql"],
        "mock": run["mock"],
        "cases": [_case_record(case) for case in run["cases"]],
    }
    manifest = _redact(manifest, _configured_secrets())
    path = root / "var" / "evaluation-runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _model_identity(run):
    """真实模式只记录已配置的模型名。不读取密钥，也不编造版本号。"""
    if run.get("mode") != "live":
        return None, None, None
    name = os.environ.get("OPENAI_MODEL")
    if not isinstance(name, str) or not name.strip():
        return None, None, None
    return "openai", name.strip(), None


def _configured_secrets():
    """只用于替换。密钥本身不写入清单。"""
    value = os.environ.get("OPENAI_API_KEY")
    if isinstance(value, str) and len(value) >= 8:
        return (value,)
    return ()


def _redact(value, secrets):
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED]")
        return _SK_TOKEN.sub("[REDACTED]", redacted)
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    return value


def _case_record(case):
    record = {
        "case_id": case.get("case_id") or case["id"],
        "generated_sql": case.get("generated_sql") or "",
        "execution_status": case.get("execution_status"),
        "evaluation_status": case.get("evaluation_status"),
        "actual_result": case.get("actual_result") or {
            "columns": list(case.get("actual_columns") or []),
            "rows": list(case.get("actual_rows") or []),
        },
        "expected_result": case.get("expected_result"),
        "latency": case.get("latency"),
        "error_details": case.get("error_details"),
        "leaked": case.get("leaked", 0),
        "expected_behavior": case.get("expected_behavior"),
        "category": case.get("category"),
    }
    if "token_usage" in case:
        record["token_usage"] = case["token_usage"]
    return record


def _orders_prompt_hash(snapshot) -> str:
    """仓库没有已发布的提示版本。哈希当前订单提示，不另造版本号。"""
    tables = {}
    # 列来自映射，插入顺序随进程变化。哈希前按列名排序，不改查询范围原文。
    for dataset in snapshot.authorization.sql.datasets:
        tables[f"main.{dataset.physical_object}"] = {
            column: column for column in sorted(dataset.columns.values())
        }
    database = {"version": snapshot.dialect_version, "tables": tables}
    prompt = render_direct_sql_prompt(REPO_ROOT, database, snapshot.authorization, snapshot.dialect)
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
