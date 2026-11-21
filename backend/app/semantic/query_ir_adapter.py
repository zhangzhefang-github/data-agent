"""把已授权的语义上下文投影成现有 Query IR 目录。"""

from app.semantic.context_builder import SemanticContext


def catalog_from_semantic_context(context: SemanticContext) -> dict:
    """Adapt an authorized semantic Context to the legacy Query IR catalog."""
    datasets = {dataset.id: dataset for dataset in context.sql.datasets}
    metrics = {}
    metric_input_aliases = {}
    exposed_columns = set()
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
            exposed_columns.add(dataset.columns[metric.field_id])

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
        exposed_columns.add(column)

    if len(metrics) != len(context.business.metrics) or len(dimensions) != len(context.business.dimensions):
        raise ValueError("语义上下文存在重复的兼容 ID")
    model_ids = {item["model"] for item in [*metrics.values(), *dimensions.values()]}
    if len(model_ids) != 1:
        raise ValueError("当前 Query IR 只能消费一个语义数据集")
    model = datasets[model_ids.pop()]
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
    }


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
