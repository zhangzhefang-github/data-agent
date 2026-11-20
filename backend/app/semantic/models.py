"""Canonical semantic model contracts independent from query runtimes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


DatasetType = Literal["fact", "dimension", "aggregate", "bridge", "staging", "source"]
VerificationStatus = Literal["proposed", "profiled", "confirmed"]
AggregationKind = Literal["sum", "count", "count_distinct", "avg", "min", "max", "ratio", "metric"]


class DataSource(BaseModel):
    id: str
    name: str
    dialect: str
    description: str = ""


class PhysicalMapping(BaseModel):
    data_source_id: str
    schema_name: str = "main"
    object_name: str
    object_type: Literal["table", "view"]
    column_mappings: dict[str, str]


class DatasetField(BaseModel):
    id: str
    name: str
    data_type: str
    description: str = ""
    user_visible: bool = False
    sensitive: bool = False


class Dataset(BaseModel):
    id: str
    name: str
    domain_id: str
    dataset_type: DatasetType
    grain: list[str] = Field(min_length=1)
    primary_key: list[str] = Field(min_length=1)
    fields: list[DatasetField]
    physical_mapping: PhysicalMapping
    exposure_policy: Literal["internal", "restricted", "approved"] = "internal"
    description: str = ""


class BusinessAttribute(BaseModel):
    id: str
    name: str
    data_type: str
    restricted: bool = False


class BusinessObject(BaseModel):
    id: str
    name: str
    domain_id: str
    candidate_key: str
    attributes: list[BusinessAttribute] = Field(default_factory=list)
    dataset_ids: list[str] = Field(default_factory=list)


class BusinessRelationship(BaseModel):
    id: str
    source_object_id: str
    target_object_id: str
    cardinality: Literal["one_to_one", "one_to_many", "many_to_many"]
    verification_status: VerificationStatus
    description: str = ""


class DatasetLineage(BaseModel):
    id: str
    source_dataset_id: str
    target_dataset_id: str
    verification_status: VerificationStatus


class DatasetRelationship(BaseModel):
    id: str
    source_dataset_id: str
    target_dataset_id: str
    source_fields: list[str] = Field(min_length=1)
    target_fields: list[str] = Field(min_length=1)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
    join_type: Literal["inner", "left"] = "left"
    priority: int = 100
    fanout_risk: bool = False
    verification_status: VerificationStatus
    match_rate: float | None = None
    orphan_count: int | None = None
    description: str = ""

    @model_validator(mode="after")
    def join_keys_have_matching_lengths(self):
        if len(self.source_fields) != len(self.target_fields):
            raise ValueError("source_fields and target_fields must have matching lengths")
        return self


class MetricExpression(BaseModel):
    kind: AggregationKind
    field_id: str | None = None
    metric_id: str | None = None
    numerator_metric_id: str | None = None
    denominator_metric_id: str | None = None
    zero_denominator: Literal["null", "zero"] = "null"

    @model_validator(mode="after")
    def validate_operands(self):
        if self.kind in {"sum", "count_distinct", "avg", "min", "max"} and not self.field_id:
            raise ValueError(f"{self.kind} requires field_id")
        if self.kind == "count" and self.field_id:
            raise ValueError("count does not accept field_id")
        if self.kind == "metric" and not self.metric_id:
            raise ValueError("metric requires metric_id")
        if self.kind == "ratio" and (not self.numerator_metric_id or not self.denominator_metric_id):
            raise ValueError("ratio requires numerator_metric_id and denominator_metric_id")
        return self


class MetricFilter(BaseModel):
    dimension_id: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: str


class Metric(BaseModel):
    id: str
    name: str
    definition: str
    expression: MetricExpression
    source_dataset_id: str
    base_grain: list[str] = Field(min_length=1)
    time_dimension_id: str | None = None
    default_filters: list[MetricFilter] = Field(default_factory=list)
    compatible_dimension_ids: list[str] = Field(default_factory=list)
    additive: bool = True
    unit: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    published: bool = False


class Dimension(BaseModel):
    id: str
    name: str
    source_dataset_id: str
    field_id: str
    is_time: bool = False
    synonyms: list[str] = Field(default_factory=list)
    compatible_metric_ids: list[str] = Field(default_factory=list)
    user_visible: bool = False


class Domain(BaseModel):
    id: str
    name: str
    description: str = ""


class SemanticModel(BaseModel):
    id: str
    version: str
    domains: list[Domain]
    data_sources: list[DataSource]
    business_objects: list[BusinessObject]
    business_relationships: list[BusinessRelationship]
    datasets: list[Dataset]
    dataset_lineage: list[DatasetLineage] = Field(default_factory=list)
    dataset_relationships: list[DatasetRelationship]
    metrics: list[Metric]
    dimensions: list[Dimension]

    def dataset(self, identifier: str) -> Dataset:
        return next(item for item in self.datasets if item.id == identifier)

    def metric(self, identifier: str) -> Metric:
        return next(item for item in self.metrics if item.id == identifier)
