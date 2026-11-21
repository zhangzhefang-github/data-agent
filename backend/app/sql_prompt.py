"""把稳定的方言写法和当前空间授权渲染成 Direct SQL 提示。"""

from pathlib import Path

import yaml

from app.semantic.context_builder import build_semantic_context


def load_dialect(root: Path) -> dict:
    path = root / "backend" / "configs" / "sql_dialects" / "duckdb.yaml"
    dialect = yaml.safe_load(path.read_text(encoding="utf-8"))
    if dialect.get("engine") != "duckdb" or not str(dialect.get("version", "")).startswith("v"):
        raise ValueError("DuckDB 方言配置缺少引擎或版本前缀")
    return dialect


def authorized_tables(root: Path) -> list[tuple[str, set[str]]]:
    context = _orders_context(root)
    return [
        (dataset.physical_object, set(dataset.columns.values()))
        for dataset in context.sql.datasets
    ]


def render_direct_sql_prompt(root: Path, database: dict) -> str:
    dialect = load_dialect(root)
    if not str(database["version"]).startswith(dialect["version"]):
        raise ValueError("连接的数据库版本与方言配置不一致")
    context = _orders_context(root)
    metrics = []
    alias_by_target = {target: alias for alias, target in context.business.metric_aliases.items()}
    for metric in context.business.metrics:
        alias = alias_by_target.get(metric.id, metric.id.rsplit(".", 1)[-1])
        if metric.expression_kind == "count":
            expression = "COUNT(*)"
        elif metric.expression_kind == "sum" and metric.field_id:
            expression = f"SUM({metric.field_id})"
        else:
            continue
        metrics.append({"alias": alias, "name": metric.name, "expression": f"{expression} AS {alias}"})
    values = [
        {"dimension": dimension.name, "values": dimension.allowed_values}
        for dimension in context.business.dimensions
        if dimension.allowed_values
    ]
    example = "\n".join(item["sql"].strip() for item in dialect["examples"])
    rules = "\n".join(f"- {rule}" for rule in dialect["rules"])
    return (
        f"方言版本：{database['version']}\n"
        f"方言规则：\n{rules}\n"
        f"经典示例（只说明写法，其中的表不一定已授权）：\n{example}\n"
        f"本次授权的表：{database['tables']}\n"
        f"授权关系：{[item.id for item in context.sql.relationships]}\n"
        f"授权指标：{metrics}\n"
        f"状态字典：{values}\n"
        f"过滤规则：{context.business.filter_rules}"
    )


def _orders_context(root: Path):
    return build_semantic_context(
        "sales",
        "orders",
        model_path=root / "backend" / "configs" / "semantic" / "jaffle_shop.yaml",
        spaces_path=root / "backend" / "configs" / "semantic" / "spaces.yaml",
    )
