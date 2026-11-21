"""把稳定的方言写法和当前空间授权渲染成 Direct SQL 提示。"""

from pathlib import Path

import yaml

from app.query_scope import render_query_scope
from app.semantic.context_builder import SemanticContext, build_semantic_context


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


def authorized_joins(root: Path) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    """已授权、且不会放大行数的等值连接。键是排序后的 (物理表, 物理列) 对。"""
    return _authorized_joins(_orders_context(root))


def _authorized_joins(context: SemanticContext) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    columns = {dataset.id: dataset.columns for dataset in context.sql.datasets}
    physical = {dataset.id: dataset.physical_object.lower() for dataset in context.sql.datasets}
    keys = set()
    for relationship in context.sql.relationships:
        if len(relationship.source_fields) != 1 or len(relationship.target_fields) != 1:
            continue
        source_column = columns[relationship.source_dataset_id][relationship.source_fields[0]].lower()
        target_column = columns[relationship.target_dataset_id][relationship.target_fields[0]].lower()
        keys.add(tuple(sorted((
            (physical[relationship.source_dataset_id], source_column),
            (physical[relationship.target_dataset_id], target_column),
        ))))
    return keys


def orders_query_scope(root: Path) -> str:
    """订单主题空间的可查询范围。不按单次问题裁剪。"""
    return render_query_scope(_orders_context(root))


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
        metrics.append({
            "alias": alias,
            "name": metric.name,
            "expression": f"{expression} AS {alias}",
            "definition": metric.definition,
        })
    values = [
        {"dimension": dimension.name, "values": dimension.allowed_values}
        for dimension in context.business.dimensions
        if dimension.allowed_values
    ]
    columns_by_dataset = {dataset.id: dataset.columns for dataset in context.sql.datasets}
    dimensions = [
        {
            "name": dimension.name,
            "column": columns_by_dataset[dimension.source_dataset_id][dimension.field_id],
            "is_time": dimension.is_time,
        }
        for dimension in context.business.dimensions
        if dimension.field_id in columns_by_dataset.get(dimension.source_dataset_id, {})
    ]
    example = "\n".join(item["sql"].strip() for item in dialect["examples"])
    rules = "\n".join(f"- {rule}" for rule in dialect["rules"])
    physical = {dataset.id: dataset.physical_object for dataset in context.sql.datasets}
    relationships = [
        {
            "id": item.id,
            "source": physical[item.source_dataset_id],
            "source_fields": [columns_by_dataset[item.source_dataset_id][field] for field in item.source_fields],
            "target": physical[item.target_dataset_id],
            "target_fields": [columns_by_dataset[item.target_dataset_id][field] for field in item.target_fields],
            "cardinality": item.cardinality,
            "join_type": item.join_type,
            "fanout_risk": item.fanout_risk,
        }
        for item in context.sql.relationships
    ]
    return (
        f"方言版本：{database['version']}\n"
        f"方言规则：\n{rules}\n"
        f"经典示例（只说明方言写法，不要模仿其中的 WITH、子查询、DISTINCT 或 date_trunc，其中的表也不一定已授权）：\n{example}\n"
        f"业务对象：{[{'id': item.id, 'name': item.name} for item in context.business.objects]}\n"
        f"本次授权的表：{database['tables']}\n"
        f"授权关系：{relationships}\n"
        f"授权指标：{metrics}\n"
        f"日历：{None if context.business.calendar is None else context.business.calendar.model_dump()}\n"
        f"授权维度：{dimensions}\n"
        f"状态字典：{values}\n"
        f"过滤规则：{context.business.filter_rules}\n"
        "可查询范围（用户询问能查什么、有哪些数据或可以问什么时，只回复下面这段原文，不要调用工具，也不要改写。"
        "示例问题只说明问法，其中的日期不要套用到用户没有说出的时间上）：\n"
        f"{render_query_scope(context)}\n"
        "本次查询约束：直接查询本次授权的表，只提交一条 SELECT。"
        "不要使用 WITH、子查询、DISTINCT、QUALIFY、date_trunc，也不要模仿经典示例的这些写法。"
        "筛选和分组只能使用授权维度中的列。时间维度用 DATE 'YYYY-MM-DD'。"
        "有状态字典的维度在 WHERE 中使用字典里的字面量，不要用 CASE 翻译。"
        "维度列在 SELECT 和 GROUP BY 中必须使用授权维度的 column，不要另起别名。"
        "是否关联由问题决定，只使用授权关系中的等值内连接。"
        "查询必须从订单表开始。统计客户数时按客户标识分组，并使用订单笔数，不要单独查询客户表，也不要对客户标识做 COUNT。"
        "指标表达式和结果别名必须与授权指标完全一致，不要相减或改写。"
        "已发布订单指标的基础粒度是单笔订单。"
        "支付明细未授权，不能把订单行和支付行连接后再汇总订单金额。"
    )


def _orders_context(root: Path):
    return build_semantic_context(
        "sales",
        "orders",
        model_path=root / "backend" / "configs" / "semantic" / "jaffle_shop.yaml",
        spaces_path=root / "backend" / "configs" / "semantic" / "spaces.yaml",
    )
