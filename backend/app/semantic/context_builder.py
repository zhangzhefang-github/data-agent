"""Build serializable, Space-authorized semantic context for query consumers."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.semantic.loader import load_semantic_model
from app.semantic.models import Dataset, Dimension, Metric, SemanticModel
from app.semantic.registry import SemanticModelRegistry


# Query IR 目前只编译 sum 和 count。规范模型保留更宽的表达式，Space 不能把其余种类放进运行时。
_QUERY_RUNTIME_EXPRESSION_KINDS = {"sum", "count"}


class SpaceCalendar(BaseModel):
    timezone: str
    fiscal_year_start_month: int = 1
    quarters: list[str] = Field(default_factory=list)


class SpaceDefinition(BaseModel):
    id: str
    domain_id: str
    name: str
    metric_ids: list[str] = Field(min_length=1)
    dimension_ids: list[str] = Field(min_length=1)
    dataset_ids: list[str] = Field(min_length=1)
    relationship_ids: list[str] = Field(default_factory=list)
    metric_aliases: dict[str, str] = Field(default_factory=dict)
    dimension_values: dict[str, dict[str, str]] = Field(default_factory=dict)
    calendar: SpaceCalendar | None = None
    filter_rules: list[str] = Field(default_factory=list)
    sql_examples: list[str] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)


class SpaceDefinitions(BaseModel):
    spaces: list[SpaceDefinition]


class ContextBusinessObject(BaseModel):
    id: str
    name: str


class ContextMetric(BaseModel):
    id: str
    name: str
    definition: str
    source_dataset_id: str
    expression_kind: str
    field_id: str | None = None
    base_grain: list[str] = Field(exclude=True)
    time_dimension_id: str | None = None
    compatible_dimension_ids: list[str]
    synonyms: list[str]
    unit: str | None = None


class ContextDimension(BaseModel):
    id: str
    name: str
    source_dataset_id: str
    field_id: str
    is_time: bool
    synonyms: list[str]
    allowed_values: dict[str, str]


class ContextDataset(BaseModel):
    id: str
    name: str
    dataset_type: str
    grain: list[str] = Field(exclude=True)
    primary_key: list[str] = Field(exclude=True)
    physical_object: str
    physical_object_type: str
    columns: dict[str, str]


class ContextRelationship(BaseModel):
    id: str
    source_dataset_id: str
    target_dataset_id: str
    source_fields: list[str]
    target_fields: list[str]
    cardinality: str
    join_type: str
    fanout_risk: bool


class BusinessSemanticContext(BaseModel):
    objects: list[ContextBusinessObject]
    metrics: list[ContextMetric]
    dimensions: list[ContextDimension]
    metric_aliases: dict[str, str]
    calendar: SpaceCalendar | None = None
    filter_rules: list[str]
    sql_examples: list[str]
    example_questions: list[str]


class SqlSemanticContext(BaseModel):
    datasets: list[ContextDataset]
    relationships: list[ContextRelationship]


class InternalSemanticContext(BaseModel):
    space_id: str
    allowed_metric_ids: list[str]
    allowed_dimension_ids: list[str]
    allowed_dataset_ids: list[str]
    allowed_relationship_ids: list[str]


class SemanticContext(BaseModel):
    model_id: str
    model_version: str
    domain_id: str
    space_id: str
    space_name: str
    business: BusinessSemanticContext
    sql: SqlSemanticContext
    internal: InternalSemanticContext = Field(exclude=True)


def build_semantic_context(
    domain_id: str,
    space_id: str,
    user_question: str | None = None,
    *,
    model: SemanticModel | None = None,
    model_path: Path | None = None,
    spaces_path: Path | None = None,
) -> SemanticContext:
    """Build a restricted semantic context without exposing connection information."""
    if model is None:
        root = Path(__file__).resolve().parents[2]
        loaded = load_semantic_model(model_path or root / "configs" / "semantic" / "jaffle_shop.yaml")
        registry = SemanticModelRegistry()
        registry.publish(loaded)
        model = registry.get(loaded.id, loaded.version)
    if spaces_path is None:
        spaces_path = Path(__file__).resolve().parents[2] / "configs" / "semantic" / "spaces.yaml"
    if domain_id not in {domain.id for domain in model.domains}:
        raise ValueError(f"Unknown semantic domain: {domain_id}")

    space = _load_space(spaces_path, space_id)
    if space.domain_id != domain_id:
        raise ValueError(f"Semantic space {space_id} is not available in domain {domain_id}")
    _validate_space_references(space, model)

    metrics_by_id = {metric.id: metric for metric in model.metrics}
    dimensions_by_id = {dimension.id: dimension for dimension in model.dimensions}
    # 待确认指标留在模型里，但不进入主题空间的可问指标。
    space = space.model_copy(
        update={
            "metric_ids": [
                identifier
                for identifier in space.metric_ids
                if not metrics_by_id[identifier].pending_confirmation
            ]
        }
    )
    selected_metric_ids, selected_dimension_ids = _select_terms(
        space, metrics_by_id, dimensions_by_id, user_question
    )
    selected_dimension_ids = _include_time_dimensions(selected_metric_ids, selected_dimension_ids, metrics_by_id)

    selected_metrics = [metrics_by_id[identifier] for identifier in space.metric_ids if identifier in selected_metric_ids]
    selected_dimensions = [
        dimensions_by_id[identifier]
        for identifier in space.dimension_ids
        if identifier in selected_dimension_ids and dimensions_by_id[identifier].user_visible
    ]
    # 空间点名的数据集进入投影。受限暴露只去掉敏感列，不再把整张已授权的表丢掉。
    # raw 与 staging 即使被误写入清单也不进入问数上下文。
    selected_dataset_ids = {
        dataset.id
        for dataset in model.datasets
        if dataset.id in space.dataset_ids and dataset.dataset_type not in {"staging", "source"}
    }
    selected_datasets = [dataset for dataset in model.datasets if dataset.id in selected_dataset_ids]
    approved_dataset_ids = {dataset.id for dataset in selected_datasets}
    selected_metrics = [metric for metric in selected_metrics if metric.source_dataset_id in approved_dataset_ids]
    selected_dimensions = [
        dimension for dimension in selected_dimensions if dimension.source_dataset_id in approved_dataset_ids
    ]
    selected_dataset_ids = approved_dataset_ids
    selected_objects = _context_objects(model, selected_dataset_ids)
    selected_relationships = [
        relationship
        for relationship in model.dataset_relationships
        if relationship.id in space.relationship_ids
        and relationship.verification_status in {"confirmed", "profiled"}
        and relationship.source_dataset_id in space.dataset_ids
        and relationship.target_dataset_id in space.dataset_ids
    ]

    return SemanticContext(
        model_id=model.id,
        model_version=model.version,
        domain_id=domain_id,
        space_id=space.id,
        space_name=space.name,
        business=BusinessSemanticContext(
            objects=selected_objects,
            metrics=[_context_metric(metric) for metric in selected_metrics],
            dimensions=[_context_dimension(dimension, space.dimension_values) for dimension in selected_dimensions],
            metric_aliases={alias: target for alias, target in space.metric_aliases.items() if target in selected_metric_ids},
            calendar=space.calendar,
            filter_rules=space.filter_rules,
            sql_examples=space.sql_examples,
            example_questions=space.example_questions,
        ),
        sql=SqlSemanticContext(
            datasets=[
                _context_dataset(dataset, selected_metrics, selected_dimensions, selected_relationships)
                for dataset in selected_datasets
            ],
            relationships=[_context_relationship(relationship) for relationship in selected_relationships],
        ),
        internal=InternalSemanticContext(
            space_id=space.id,
            allowed_metric_ids=space.metric_ids,
            allowed_dimension_ids=space.dimension_ids,
            allowed_dataset_ids=space.dataset_ids,
            allowed_relationship_ids=space.relationship_ids,
        ),
    )


def _load_space(path: Path, space_id: str) -> SpaceDefinition:
    payload = yaml.safe_load(path.read_text())
    definitions = SpaceDefinitions.model_validate(payload)
    for space in definitions.spaces:
        if space.id == space_id:
            return space
    raise ValueError(f"Unknown semantic space: {space_id}")


def _validate_space_references(space: SpaceDefinition, model: SemanticModel) -> None:
    references = {
        "metric": (space.metric_ids, {metric.id for metric in model.metrics}),
        "dimension": (space.dimension_ids, {dimension.id for dimension in model.dimensions}),
        "dataset": (space.dataset_ids, {dataset.id for dataset in model.datasets}),
        "relationship": (space.relationship_ids, {relationship.id for relationship in model.dataset_relationships}),
    }
    for kind, (identifiers, available) in references.items():
        missing = sorted(set(identifiers) - available)
        if missing:
            raise ValueError(f"Semantic space {space.id} references unknown {kind}: {missing[0]}")
    metrics_by_id = {metric.id: metric for metric in model.metrics}
    for metric_id in space.metric_ids:
        kind = metrics_by_id[metric_id].expression.kind
        if kind not in _QUERY_RUNTIME_EXPRESSION_KINDS:
            raise ValueError(f"Semantic space {space.id} authorizes unsupported expression kind: {kind}")
    for alias, metric_id in space.metric_aliases.items():
        if metric_id not in space.metric_ids:
            raise ValueError(f"Semantic space {space.id} maps alias {alias} to an unauthorized metric")
    for dimension_id in space.dimension_values:
        if dimension_id not in space.dimension_ids:
            raise ValueError(f"Semantic space {space.id} assigns values to an unauthorized dimension")


def _select_terms(
    space: SpaceDefinition,
    metrics: dict[str, Metric],
    dimensions: dict[str, Dimension],
    user_question: str | None,
) -> tuple[set[str], set[str]]:
    if not user_question:
        return set(space.metric_ids), set(space.dimension_ids)
    question = user_question.casefold()
    selected_metrics = {
        identifier for identifier in space.metric_ids
        if _matches_metric(question, metrics[identifier], space.metric_aliases)
    }
    selected_dimensions = {
        identifier for identifier in space.dimension_ids
        if _matches_dimension(question, dimensions[identifier])
    }
    if not selected_metrics and not selected_dimensions:
        return set(space.metric_ids), set(space.dimension_ids)
    return selected_metrics, selected_dimensions


def _matches_metric(question: str, metric: Metric, aliases: dict[str, str]) -> bool:
    terms = [metric.id, metric.name, *metric.synonyms]
    terms.extend(alias for alias, target in aliases.items() if target == metric.id)
    return any(term.casefold() in question for term in terms)


def _matches_dimension(question: str, dimension: Dimension) -> bool:
    return any(term.casefold() in question for term in [dimension.id, dimension.name, *dimension.synonyms])


def _include_time_dimensions(
    metric_ids: set[str],
    dimension_ids: set[str],
    metrics: dict[str, Metric],
) -> set[str]:
    return dimension_ids | {metrics[identifier].time_dimension_id for identifier in metric_ids if metrics[identifier].time_dimension_id}


def _context_objects(model: SemanticModel, dataset_ids: set[str]) -> list[ContextBusinessObject]:
    return [
        ContextBusinessObject(id=business_object.id, name=business_object.name)
        for business_object in model.business_objects
        if dataset_ids & set(business_object.dataset_ids)
    ]


def _context_metric(metric: Metric) -> ContextMetric:
    return ContextMetric(
        id=metric.id,
        name=metric.name,
        definition=metric.definition,
        source_dataset_id=metric.source_dataset_id,
        expression_kind=metric.expression.kind,
        field_id=metric.expression.field_id,
        base_grain=metric.base_grain,
        time_dimension_id=metric.time_dimension_id,
        compatible_dimension_ids=metric.compatible_dimension_ids,
        synonyms=metric.synonyms,
        unit=metric.unit,
    )


def _context_dimension(
    dimension: Dimension,
    dimension_values: dict[str, dict[str, str]],
) -> ContextDimension:
    return ContextDimension(
        id=dimension.id,
        name=dimension.name,
        source_dataset_id=dimension.source_dataset_id,
        field_id=dimension.field_id,
        is_time=dimension.is_time,
        synonyms=dimension.synonyms,
        allowed_values=dimension_values.get(dimension.id, {}),
    )


def _context_dataset(
    dataset: Dataset,
    metrics: list[Metric],
    dimensions: list[Dimension],
    relationships,
) -> ContextDataset:
    referenced = {dimension.field_id for dimension in dimensions if dimension.source_dataset_id == dataset.id}
    referenced.update(
        metric.expression.field_id
        for metric in metrics
        if metric.source_dataset_id == dataset.id and metric.expression.field_id
    )
    join_keys = set()
    for relationship in relationships:
        if relationship.source_dataset_id == dataset.id:
            join_keys.update(relationship.source_fields)
        if relationship.target_dataset_id == dataset.id:
            join_keys.update(relationship.target_fields)
    referenced.update(join_keys)
    # 支付明细没有单独发布的筛选维度，连接和汇总仍要看到金额、方式和日期。
    if dataset.id == "payment_mart":
        referenced.update(field.id for field in dataset.fields if not field.sensitive)
    fields = {field.id: field for field in dataset.fields}
    columns = {
        field_id: dataset.physical_mapping.column_mappings[field_id]
        for field_id in referenced
        if field_id in fields
        and not fields[field_id].sensitive
        and field_id in dataset.physical_mapping.column_mappings
        and (field_id not in dataset.primary_key or field_id in join_keys)
    }
    return ContextDataset(
        id=dataset.id,
        name=dataset.name,
        dataset_type=dataset.dataset_type,
        grain=dataset.grain,
        primary_key=dataset.primary_key,
        physical_object=dataset.physical_mapping.object_name,
        physical_object_type=dataset.physical_mapping.object_type,
        columns=columns,
    )


def _context_relationship(relationship) -> ContextRelationship:
    return ContextRelationship(
        id=relationship.id,
        source_dataset_id=relationship.source_dataset_id,
        target_dataset_id=relationship.target_dataset_id,
        source_fields=relationship.source_fields,
        target_fields=relationship.target_fields,
        cardinality=relationship.cardinality,
        join_type=relationship.join_type,
        fanout_risk=relationship.fanout_risk,
    )
