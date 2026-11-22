"""一次问数一份不可变的语义上下文快照。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.semantic.context_builder import SemanticContext, build_semantic_context
from app.semantic.loader import load_semantic_model
from app.semantic.models import SemanticModel
from app.semantic.query_ir_adapter import catalog_from_semantic_context
from app.semantic.registry import SemanticModelRegistry
from app.sql_prompt import authorized_join_keys, load_dialect


class AuthorizedField(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str
    field_id: str
    column: str
    physical_type: str


class SemanticContextSnapshot(BaseModel):
    """提示、校验和执行共用的授权快照。检索结果不能替换这里的授权。"""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    model_id: str
    model_version: str
    model_hash: str
    space_id: str
    authorization_version: str
    authorization_hash: str
    dialect_id: str
    dialect_version: str
    data_source_id: str
    schema_fingerprint: str
    authorization: SemanticContext
    object_datasets: dict[str, tuple[str, ...]]
    dialect: dict = Field(exclude=True)
    authorized_fields: tuple[AuthorizedField, ...] = ()

    def with_schema(self, database: dict) -> SemanticContextSnapshot:
        if not str(database["version"]).startswith(self.dialect_version):
            raise ValueError("连接的数据库版本与方言配置不一致")
        fingerprint = schema_fingerprint(database["tables"])
        fields = []
        for dataset in self.authorization.sql.datasets:
            described = database["tables"].get(f"main.{dataset.physical_object}", {})
            for field_id, column in dataset.columns.items():
                if column not in described:
                    continue
                fields.append(AuthorizedField(
                    dataset_id=dataset.id,
                    field_id=field_id,
                    column=column,
                    physical_type=described[column],
                ))
        snapshot_id = snapshot_identity(
            self.model_hash,
            self.authorization_hash,
            self.dialect_id,
            self.dialect_version,
            self.data_source_id,
            fingerprint,
        )
        return self.model_copy(update={
            "snapshot_id": snapshot_id,
            "schema_fingerprint": fingerprint,
            "authorized_fields": tuple(fields),
        })


def build_orders_snapshot(root: Path) -> SemanticContextSnapshot:
    """加载并发布一次订单语义，供本次请求的提示和校验共用。"""
    model_path = root / "backend" / "configs" / "semantic" / "jaffle_shop.yaml"
    spaces_path = root / "backend" / "configs" / "semantic" / "spaces.yaml"
    loaded = load_semantic_model(model_path)
    registry = SemanticModelRegistry()
    registry.publish(loaded)
    published = registry.get(loaded.id, loaded.version)
    context = build_semantic_context("sales", "orders", model=published, spaces_path=spaces_path)
    dialect = load_dialect(root)
    return assemble_snapshot(published, context, dialect)


def assemble_snapshot(model: SemanticModel, context: SemanticContext, dialect: dict) -> SemanticContextSnapshot:
    authorized_ids = {dataset.id for dataset in context.sql.datasets}
    source_ids = sorted({
        model.dataset(dataset.id).physical_mapping.data_source_id
        for dataset in context.sql.datasets
    })
    object_datasets = {
        item.id: tuple(dataset_id for dataset_id in item.dataset_ids if dataset_id in authorized_ids)
        for item in model.business_objects
        if any(dataset_id in authorized_ids for dataset_id in item.dataset_ids)
    }
    model_hash = content_hash(model.model_dump(mode="json"))
    authorization_hash = content_hash(authorization_payload(context))
    data_source_id = ",".join(source_ids)
    fingerprint = "unbound"
    return SemanticContextSnapshot(
        snapshot_id=snapshot_identity(
            model_hash, authorization_hash, dialect["engine"], dialect["version"], data_source_id, fingerprint,
        ),
        model_id=model.id,
        model_version=model.version,
        model_hash=model_hash,
        space_id=context.space_id,
        authorization_version=authorization_hash[:12],
        authorization_hash=authorization_hash,
        dialect_id=dialect["engine"],
        dialect_version=dialect["version"],
        data_source_id=data_source_id,
        schema_fingerprint=fingerprint,
        authorization=context.model_copy(deep=True),
        object_datasets=object_datasets,
        dialect=dialect,
    )


def validation_inputs(snapshot: SemanticContextSnapshot) -> tuple[dict, set]:
    """校验始终读取快照上的完整授权，不读取本次检索子集。"""
    return (
        catalog_from_semantic_context(snapshot.authorization),
        authorized_join_keys(snapshot.authorization),
    )


def authorization_payload(context: SemanticContext) -> dict:
    return {
        "space_id": context.space_id,
        "metrics": [
            {
                "id": metric.id,
                "dataset": metric.source_dataset_id,
                "kind": metric.expression_kind,
                "field": metric.field_id,
                "grain": metric.base_grain,
            }
            for metric in context.business.metrics
        ],
        "dimensions": [
            {"id": dimension.id, "dataset": dimension.source_dataset_id, "field": dimension.field_id}
            for dimension in context.business.dimensions
        ],
        "datasets": [
            {
                "id": dataset.id,
                "object": dataset.physical_object,
                "columns": dataset.columns,
                "grain": dataset.grain,
            }
            for dataset in context.sql.datasets
        ],
        "relationships": [item.model_dump() for item in context.sql.relationships],
    }


def schema_fingerprint(tables: dict) -> str:
    rows = []
    for table in sorted(tables):
        for column in sorted(tables[table]):
            rows.append(f"{table}.{column}:{tables[table][column]}")
    return content_hash(rows)


def snapshot_identity(*parts: str) -> str:
    return content_hash(list(parts))[:16]


def content_hash(payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=list).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
