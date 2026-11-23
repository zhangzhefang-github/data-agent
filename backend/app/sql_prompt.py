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


def authorized_tables(root: Path, context: SemanticContext | None = None) -> list[tuple[str, set[str]]]:
    context = context or _orders_context(root)
    return [
        (dataset.physical_object, set(dataset.columns.values()))
        for dataset in context.sql.datasets
    ]


def authorized_joins(root: Path, context: SemanticContext | None = None) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    """已授权、且不会放大行数的等值连接。键是排序后的 (物理表, 物理列) 对。"""
    return authorized_join_keys(context or _orders_context(root))


def authorized_join_keys(context: SemanticContext) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    return _authorized_joins(context)


def _authorized_joins(context: SemanticContext) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    columns = {dataset.id: dataset.columns for dataset in context.sql.datasets}
    physical = {dataset.id: dataset.physical_object.lower() for dataset in context.sql.datasets}
    keys = set()
    for relationship in context.sql.relationships:
        # Fanout 关系可以出现在查询里，但不因此变成免检的等值连接。
        if relationship.fanout_risk:
            continue
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


def render_direct_sql_prompt(
    root: Path,
    database: dict,
    context: SemanticContext | None = None,
    dialect: dict | None = None,
) -> str:
    dialect = dialect or load_dialect(root)
    if not str(database["version"]).startswith(dialect["version"]):
        raise ValueError("连接的数据库版本与方言配置不一致")
    context = context or _orders_context(root)
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
        f"经典示例（只说明方言写法，其中的表不一定已授权）：\n{example}\n"
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
        "本次查询约束：直接查询本次授权的表，只提交一条只读 SELECT。"
        "可以用 WITH、子查询、JOIN、GROUP BY、窗口函数和 UNION ALL。"
        "筛选和分组可以使用授权维度中的列。时间维度用 DATE 'YYYY-MM-DD'。"
        "有状态字典的维度在 WHERE 中使用字典里的字面量，不要用 CASE 翻译。"
        "维度列在 SELECT 和 GROUP BY 中必须使用授权维度的 column，不要另起别名。"
        "空间内列出的表可以连接。统计客户数时按客户标识分组，并使用订单笔数，不要单独查询客户表，也不要对客户标识做 COUNT。"
        "指标表达式和结果别名必须与授权指标完全一致，不要相减或改写。"
        "已发布订单指标的基础粒度是单笔订单。"
        "支付明细要先按 order_id 汇总，再与订单连接。不要在未汇总的支付行上计算订单笔数或订单金额。"
        + _payment_example(context)
    )



def _payment_example(context: SemanticContext) -> str:
    datasets = {item.physical_object.lower(): set(item.columns.values()) for item in context.sql.datasets}
    metrics = {metric.id for metric in context.business.metrics}
    if not ({"orders.order_amount", "orders.order_count"} <= metrics and
            {"order_id", "amount"} <= datasets.get("orders", set()) and
            {"order_id", "amount"} <= datasets.get("payments", set())):
        return ""
    return (
        "\n支付预汇总示例（仅示范有匹配支付的订单；按实际问题决定连接和过滤条件）：\n"
        "WITH paid AS (SELECT order_id, COUNT(*) AS payment_rows, SUM(amount) AS payment_amount "
        "FROM payments GROUP BY order_id) "
        "SELECT COUNT(*) AS order_rows, SUM(o.amount) AS order_amount, "
        "SUM(paid.payment_rows) AS payment_rows, SUM(paid.payment_amount) AS payment_amount "
        "FROM orders o JOIN paid ON paid.order_id = o.order_id;\n"
        "要筛选多笔支付，在外层 WHERE 使用 paid.payment_rows > 1。"
        "不要把 COUNT(*) 改成 COUNT(order_id)，不要用 SUM(COALESCE(...)) 改写指标；"
        "订单日期条件写在订单表上，避免额外的订单汇总 CTE。"
    )

def _orders_context(root: Path):
    return build_semantic_context(
        "sales",
        "orders",
        model_path=root / "backend" / "configs" / "semantic" / "jaffle_shop.yaml",
        spaces_path=root / "backend" / "configs" / "semantic" / "spaces.yaml",
    )


def render_two_phase_prompt(
    root: Path,
    database: dict,
    presentation: SemanticContext,
    catalog: list[dict],
    dialect: dict | None = None,
) -> str:
    """大空间提示只放本次展开的资产。短目录不含表达式和物理映射。"""
    dialect = dialect or load_dialect(root)
    if not str(database["version"]).startswith(dialect["version"]):
        raise ValueError("连接的数据库版本与方言配置不一致")
    rules = "\n".join(f"- {rule}" for rule in dialect["rules"])
    visible_tables = {
        f"main.{dataset.physical_object}" for dataset in presentation.sql.datasets
    }
    tables = {name: columns for name, columns in database["tables"].items() if name in visible_tables}
    if presentation.business.metrics or presentation.sql.datasets:
        body = _expanded_context(presentation, tables)
    else:
        body = "语义短目录（只有标识、名称和同义词，没有表达式和物理映射）：\n" + "\n".join(
            f"- {item['kind']} {item['id']} {item['name']} {','.join(item['synonyms'])} {item['summary']}".rstrip()
            for item in catalog
        )
    return (
        f"方言版本：{database['version']}\n"
        f"方言规则：\n{rules}\n"
        f"{body}\n"
        "本次查询约束：直接查询本次展开的表，只提交一条只读 SELECT。"
        "可以用 WITH、子查询、JOIN、GROUP BY、窗口函数和 UNION ALL。"
        "筛选和分组可以使用已展开维度中的列。时间维度用 DATE 'YYYY-MM-DD'。"
        "指标表达式和结果别名必须与已展开指标完全一致。"
        "已展开的表可以连接。内连接会丢掉对端没有匹配的行。"
        "fanout_risk 为真时，先按订单汇总支付明细，再计算订单笔数和订单金额。"
        + _payment_example(presentation)
    )


def _expanded_context(context: SemanticContext, tables: dict) -> str:
    columns_by_dataset = {dataset.id: dataset.columns for dataset in context.sql.datasets}
    alias_by_target = {target: alias for alias, target in context.business.metric_aliases.items()}
    metrics = []
    for metric in context.business.metrics:
        alias = alias_by_target.get(metric.id, metric.id.rsplit(".", 1)[-1])
        if metric.expression_kind == "count":
            expression = f"COUNT(*) AS {alias}"
        elif metric.expression_kind == "sum" and metric.field_id:
            expression = f"SUM({metric.field_id}) AS {alias}"
        else:
            continue
        metrics.append({
            "id": metric.id,
            "name": metric.name,
            "expression": expression,
            "definition": metric.definition,
            "base_grain": metric.base_grain,
            "compatible_dimension_ids": metric.compatible_dimension_ids,
        })
    dimensions = [
        {
            "id": dimension.id,
            "name": dimension.name,
            "column": columns_by_dataset[dimension.source_dataset_id][dimension.field_id],
            "synonyms": dimension.synonyms,
        }
        for dimension in context.business.dimensions
        if dimension.field_id in columns_by_dataset.get(dimension.source_dataset_id, {})
    ]
    datasets = [
        {
            "id": dataset.id,
            "name": dataset.name,
            "grain": dataset.grain,
            "physical_object": dataset.physical_object,
            "columns": dataset.columns,
        }
        for dataset in context.sql.datasets
    ]
    relationships = []
    for item in context.sql.relationships:
        relationships.append({
            "id": item.id,
            "source_fields": [columns_by_dataset[item.source_dataset_id][field] for field in item.source_fields],
            "target_fields": [columns_by_dataset[item.target_dataset_id][field] for field in item.target_fields],
            "cardinality": item.cardinality,
            "join_type": item.join_type,
            "fanout_risk": item.fanout_risk,
            "row_filtering_risk": item.join_type == "inner",
        })
    return (
        f"本次展开的表：{tables}\n"
        f"数据集：{datasets}\n"
        f"指标：{metrics}\n"
        f"维度：{dimensions}\n"
        f"关系：{relationships}\n"
    )
