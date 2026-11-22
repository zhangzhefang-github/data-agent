"""在空间授权之内定位语义资产。检索不决定授权。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.semantic.context_builder import SemanticContext
from app.semantic.snapshot import SemanticContextSnapshot


# 与本地 Wren 的 SCHEMA_DESCRIBE_THRESHOLD 相同：约 3 万字符，按英文粗算 8 千 token。
# 见 /home/zzf/WrenAI/core/wren/src/wren/memory/schema_indexer.py。
# 用字符数是因为生成文本后可以直接量，不必先接分词器。中文更密，会更早切到检索。
FULL_CONTEXT_CHAR_THRESHOLD = 30_000
MAX_CONTEXT_COVERAGE = 2


class CatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    id: str
    name: str
    synonyms: list[str] = []
    summary: str = ""


class RankedHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    item: CatalogItem
    score: int
    reason: str


class SemanticLocator(Protocol):
    def rank(self, question: str, catalog: list[CatalogItem]) -> list[RankedHit]:
        """按相关度返回候选。授权和关系判断不在这里做。"""


class LexicalSemanticLocator:
    """精确 ID、名称、同义词，然后才是关键词。后面可以换成 BM25 或向量检索。"""

    def rank(self, question: str, catalog: list[CatalogItem]) -> list[RankedHit]:
        folded = question.casefold()
        ranked = []
        for item in catalog:
            score, reason = _score_item(folded, item)
            if score:
                ranked.append(RankedHit(item=item, score=score, reason=reason))
        ranked.sort(key=lambda hit: (-hit.score, hit.item.kind, hit.item.id))
        return ranked


def is_small_space(context: SemanticContext) -> bool:
    """授权文本不超过 Wren 的全文阈值时，整份授权进入提示。"""
    return len(context.model_dump_json()) <= FULL_CONTEXT_CHAR_THRESHOLD


def short_catalog(context: SemanticContext) -> list[CatalogItem]:
    """短目录只来自已经投影过的授权，不带表达式、字段和连接键。"""
    datasets = {dataset.id: dataset for dataset in context.sql.datasets}
    items = [
        CatalogItem(kind="object", id=item.id, name=item.name)
        for item in context.business.objects
    ]
    items.extend(
        CatalogItem(kind="metric", id=item.id, name=item.name, synonyms=list(item.synonyms))
        for item in context.business.metrics
    )
    items.extend(
        CatalogItem(kind="dimension", id=item.id, name=item.name, synonyms=list(item.synonyms))
        for item in context.business.dimensions
    )
    items.extend(
        CatalogItem(kind="dataset", id=item.id, name=item.name)
        for item in context.sql.datasets
    )
    items.extend(
        CatalogItem(
            kind="relationship",
            id=item.id,
            name=item.id,
            summary=_relationship_summary(item, datasets),
        )
        for item in context.sql.relationships
    )
    return items


def present_context(
    snapshot: SemanticContextSnapshot,
    question: str,
    locator: SemanticLocator | None = None,
) -> SemanticContext:
    """小空间返回完整授权。大空间只展开定位到的资产。"""
    if is_small_space(snapshot.authorization):
        return snapshot.authorization
    return expand_authorization(
        snapshot.authorization,
        question,
        snapshot.object_datasets,
        locator or LexicalSemanticLocator(),
    )


def expand_authorization(
    authorization: SemanticContext,
    question: str,
    object_datasets: dict[str, tuple[str, ...]] | None = None,
    locator: SemanticLocator | None = None,
) -> SemanticContext:
    locator = locator or LexicalSemanticLocator()
    selected = {hit.item.id for hit in locator.rank(question, short_catalog(authorization))}
    metric_ids = {item.id for item in authorization.business.metrics if item.id in selected}
    metrics = [item for item in authorization.business.metrics if item.id in metric_ids]
    dimension_ids = {item.id for item in authorization.business.dimensions if item.id in selected}
    for metric in metrics:
        if metric.time_dimension_id:
            dimension_ids.add(metric.time_dimension_id)
        dimension_ids.update(metric.compatible_dimension_ids)
    dimensions = [item for item in authorization.business.dimensions if item.id in dimension_ids]
    dataset_ids = {metric.source_dataset_id for metric in metrics}
    dataset_ids.update(dimension.source_dataset_id for dimension in dimensions)
    dataset_ids.update(item.id for item in authorization.sql.datasets if item.id in selected)
    for relationship in authorization.sql.relationships:
        if relationship.id in selected:
            dataset_ids.add(relationship.source_dataset_id)
            dataset_ids.add(relationship.target_dataset_id)
    for object_id, members in (object_datasets or {}).items():
        if object_id in selected:
            dataset_ids.update(members)
    return _project(authorization, metrics, dimensions, dataset_ids, object_datasets or {})


def include_datasets(
    authorization: SemanticContext,
    presentation: SemanticContext,
    dataset_ids: list[str],
    object_datasets: dict[str, tuple[str, ...]],
) -> SemanticContext:
    """把已授权、但本次没展开的数据集补进同一份授权的投影。"""
    selected = {item.id for item in presentation.sql.datasets} | set(dataset_ids)
    unknown = selected - {item.id for item in authorization.sql.datasets}
    if unknown:
        raise ValueError(f"不能把未授权数据集补进上下文: {sorted(unknown)[0]}")
    metrics = list(presentation.business.metrics)
    seen_metrics = {item.id for item in metrics}
    for metric in authorization.business.metrics:
        if metric.source_dataset_id in selected and metric.id not in seen_metrics:
            metrics.append(metric)
            seen_metrics.add(metric.id)
    dimensions = list(presentation.business.dimensions)
    seen_dimensions = {item.id for item in dimensions}
    for dimension in authorization.business.dimensions:
        if dimension.source_dataset_id in selected and dimension.id not in seen_dimensions:
            dimensions.append(dimension)
            seen_dimensions.add(dimension.id)
    return _project(authorization, metrics, dimensions, selected, object_datasets)


def expansion_payload(authorization: SemanticContext, dataset_ids: list[str]) -> dict:
    selected = set(dataset_ids)
    datasets = [
        {
            "id": dataset.id,
            "name": dataset.name,
            "grain": list(dataset.grain),
            "physical_object": dataset.physical_object,
            "columns": dataset.columns,
        }
        for dataset in authorization.sql.datasets
        if dataset.id in selected
    ]
    relationships = [
        {
            "id": item.id,
            "source_dataset_id": item.source_dataset_id,
            "target_dataset_id": item.target_dataset_id,
            "source_fields": list(item.source_fields),
            "target_fields": list(item.target_fields),
            "cardinality": item.cardinality,
            "join_type": item.join_type,
            "fanout_risk": item.fanout_risk,
            "row_filtering_risk": item.join_type == "inner",
        }
        for item in authorization.sql.relationships
        if item.source_dataset_id in selected or item.target_dataset_id in selected
    ]
    return {"datasets": datasets, "relationships": relationships}


def _project(
    authorization: SemanticContext,
    metrics,
    dimensions,
    dataset_ids: set[str],
    object_datasets: dict[str, tuple[str, ...]],
) -> SemanticContext:
    datasets = [item for item in authorization.sql.datasets if item.id in dataset_ids]
    relationships = [
        item for item in authorization.sql.relationships
        if item.source_dataset_id in dataset_ids and item.target_dataset_id in dataset_ids
    ]
    if object_datasets:
        object_ids = {
            object_id for object_id, members in object_datasets.items() if dataset_ids.intersection(members)
        }
        objects = [item for item in authorization.business.objects if item.id in object_ids]
    else:
        objects = list(authorization.business.objects)
    metric_ids = {item.id for item in metrics}
    return authorization.model_copy(update={
        "business": authorization.business.model_copy(update={
            "objects": objects,
            "metrics": metrics,
            "dimensions": dimensions,
            "metric_aliases": {
                alias: target for alias, target in authorization.business.metric_aliases.items() if target in metric_ids
            },
        }),
        "sql": authorization.sql.model_copy(update={
            "datasets": datasets,
            "relationships": relationships,
        }),
        "internal": authorization.internal,
    })


def _relationship_summary(relationship, datasets: dict) -> str:
    source = datasets[relationship.source_dataset_id].name
    target = datasets[relationship.target_dataset_id].name
    return f"{source}与{target}的已授权关系"


def _score_item(question: str, item: CatalogItem) -> tuple[int, str]:
    if not question:
        return 0, ""
    if item.id.casefold() == question or item.id.casefold() in question:
        return 100, "id"
    if item.name.casefold() == question:
        return 80, "name"
    if item.name.casefold() in question:
        return 70, "name"
    for synonym in item.synonyms:
        if synonym.casefold() == question:
            return 60, "synonym"
        if synonym.casefold() in question:
            return 50, "synonym"
    for token in item.id.replace(".", " ").replace("_", " ").split():
        if len(token) >= 3 and token.casefold() in question:
            return 30, "keyword"
    return 0, ""
