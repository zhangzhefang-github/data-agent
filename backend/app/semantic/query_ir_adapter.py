"""把已授权的语义上下文投影成现有 Query IR 目录。"""

from app.semantic.context_builder import SemanticContext


def catalog_from_semantic_context(context: SemanticContext) -> dict:
    """Adapt an authorized semantic Context to the legacy Query IR catalog."""
    datasets = {dataset.id: dataset for dataset in context.sql.datasets}
    metrics = {}
    metric_input_aliases = {}
    column_owners: list[tuple[str, str]] = []
    for metric in context.business.metrics:
        wire_id = _wire_metric_id(metric.id, context.business.metric_aliases)
        dataset = datasets[metric.source_dataset_id]
        expression = _legacy_metric_expression(metric.expression_kind, metric.field_id, dataset.columns)
        metrics[wire_id] = {
            "name": metric.name,
            "model": dataset.id,
            "expression": expression,
            "grain": metric.base_grain,
            "allowed_filters": [_wire_dimension_id(identifier) for identifier in metric.compatible_dimension_ids],
            "description": metric.definition,
            "synonyms": metric.synonyms,
        }
        metric_input_aliases[metric.id] = wire_id
        if metric.field_id:
            column_owners.append((dataset.id, dataset.columns[metric.field_id]))

    dimensions = {}
    dimension_input_aliases = {}
    for dimension in context.business.dimensions:
        wire_id = _wire_dimension_id(dimension.id)
        dataset = datasets[dimension.source_dataset_id]
        column = dataset.columns[dimension.field_id]
        dimensions[wire_id] = {
            "name": dimension.name,
            "model": dataset.id,
            "column": column,
            "synonyms": dimension.synonyms,
            "is_time": dimension.is_time,
        }
        if dimension.allowed_values:
            dimensions[wire_id]["values"] = [
                {"name": name, "value": value}
                for name, value in dimension.allowed_values.items()
            ]
        dimension_input_aliases[dimension.id] = wire_id
        column_owners.append((dataset.id, column))

    if len(metrics) != len(context.business.metrics) or len(dimensions) != len(context.business.dimensions):
        raise ValueError("语义上下文存在重复的兼容 ID")
    # 指标决定订单表。支付方式这类维度可以落在另一张已授权的表上。
    metric_models = {item["model"] for item in metrics.values()}
    dimension_models = {item["model"] for item in dimensions.values()}
    if len(metric_models) > 1 or (not metric_models and len(dimension_models) != 1):
        raise ValueError("当前 Query IR 只能消费一个语义数据集")
    model = datasets[(metric_models or dimension_models).pop()]
    exposed_columns = {column for owner, column in column_owners if owner == model.id}
    date_dimension = next((dimension for dimension in context.business.dimensions if dimension.is_time), None)
    return {
        "id": context.space_id,
        "name": context.space_name,
        "description": "由已发布规范语义模型授权的指标和维度",
        "models": {
            model.id: {
                "table": model.physical_object,
                "date_column": None if date_dimension is None else model.columns[date_dimension.field_id],
                "columns": sorted(exposed_columns),
            }
        },
        "metrics": metrics,
        "dimensions": dimensions,
        "metric_input_aliases": metric_input_aliases,
        "dimension_input_aliases": dimension_input_aliases,
        "instructions": "只使用已发布规范语义模型授权的指标和维度。",
        "physical_tables": [
            {"id": dataset.id, "table": dataset.physical_object.lower()}
            for dataset in context.sql.datasets
        ],
        "fanouts": _fanouts(context),
    }


def _fanouts(context: SemanticContext) -> list[dict]:
    datasets = {dataset.id: dataset for dataset in context.sql.datasets}
    fanouts = []
    for relationship in context.sql.relationships:
        if not relationship.fanout_risk or relationship.cardinality != "many_to_one":
            continue
        source = datasets[relationship.source_dataset_id]
        target = datasets[relationship.target_dataset_id]
        field_id = relationship.source_fields[0]
        fanouts.append({
            "table": source.physical_object.lower(),
            "anchor": target.physical_object.lower(),
            "key": source.columns.get(field_id, field_id).lower(),
        })
    return fanouts


def _wire_metric_id(identifier: str, aliases: dict[str, str]) -> str:
    return next((alias for alias, target in aliases.items() if target == identifier), identifier.rsplit(".", 1)[-1])


def _wire_dimension_id(identifier: str) -> str:
    return identifier.rsplit(".", 1)[-1]


def _legacy_metric_expression(kind: str, field_id: str | None, columns: dict[str, str]) -> str:
    if kind == "count":
        return "COUNT(*)"
    if kind == "sum" and field_id:
        return f"SUM({columns[field_id]})"
    raise ValueError(f"指标表达式 {kind} 不受当前 Query IR 支持")
