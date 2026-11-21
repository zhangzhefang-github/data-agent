"""Deterministic semantic model validation."""

from collections.abc import Iterable

from app.semantic.diagnostics import Diagnostic, ValidationResult
from app.semantic.models import Dataset, Dimension, Metric, SemanticModel


def validate_semantic_model(
    model: SemanticModel,
    catalog_objects: dict[str, set[str]] | None = None,
) -> ValidationResult:
    """Validate a model and optional physical catalog without executing queries."""
    diagnostics: list[Diagnostic] = []
    _validate_unique_ids(model, diagnostics)
    dataset_ids = {item.id for item in model.datasets}
    dimension_ids = {item.id for item in model.dimensions}
    metric_ids = {item.id for item in model.metrics}
    data_source_ids = {item.id for item in model.data_sources}
    object_ids = {item.id for item in model.business_objects}

    for dataset in model.datasets:
        field_ids = {field.id for field in dataset.fields}
        _require_members(dataset.id, "grain", dataset.grain, field_ids, "unknown_field", diagnostics)
        _require_members(dataset.id, "primary_key", dataset.primary_key, field_ids, "unknown_field", diagnostics)
        _require_member(dataset.id, "physical_mapping.data_source_id", dataset.physical_mapping.data_source_id,
                        data_source_ids, "unknown_data_source", diagnostics)
        if catalog_objects is not None:
            _validate_catalog_mapping(dataset, catalog_objects, diagnostics)

    for business_object in model.business_objects:
        _require_members(business_object.id, "dataset_ids", business_object.dataset_ids, dataset_ids,
                         "unknown_dataset", diagnostics)

    for relationship in model.business_relationships:
        _require_member(relationship.id, "source_object_id", relationship.source_object_id, object_ids,
                        "unknown_business_object", diagnostics)
        _require_member(relationship.id, "target_object_id", relationship.target_object_id, object_ids,
                        "unknown_business_object", diagnostics)

    for record in model.dataset_lineage:
        _require_member(record.id, "source_dataset_id", record.source_dataset_id, dataset_ids,
                        "unknown_dataset", diagnostics)
        _require_member(record.id, "target_dataset_id", record.target_dataset_id, dataset_ids,
                        "unknown_dataset", diagnostics)

    for relationship in model.dataset_relationships:
        _require_member(relationship.id, "source_dataset_id", relationship.source_dataset_id, dataset_ids,
                        "unknown_dataset", diagnostics)
        _require_member(relationship.id, "target_dataset_id", relationship.target_dataset_id, dataset_ids,
                        "unknown_dataset", diagnostics)
        _validate_relationship_fields(model, relationship.id, relationship.source_dataset_id, relationship.source_fields,
                                      "source_fields", diagnostics)
        _validate_relationship_fields(model, relationship.id, relationship.target_dataset_id, relationship.target_fields,
                                      "target_fields", diagnostics)
        if relationship.cardinality in {"many_to_many", "one_to_many"} and not relationship.fanout_risk:
            _error(diagnostics, relationship.id, "fanout_risk", "missing_fanout_risk",
                   "This cardinality can multiply rows.", "Set fanout_risk to true and restrict its use.")

    for dimension in model.dimensions:
        _validate_dimension_sources(model, dimension, dataset_ids, diagnostics)
        _require_members(dimension.id, "compatible_metric_ids", dimension.compatible_metric_ids, metric_ids,
                         "unknown_metric", diagnostics)

    for metric in model.metrics:
        _validate_metric(model, metric, dataset_ids, dimension_ids, metric_ids, diagnostics)
    _validate_metric_cycles(model.metrics, diagnostics)
    _validate_ambiguous_paths(model, diagnostics)

    errors = [item for item in diagnostics if item.severity == "error"]
    warnings = [item for item in diagnostics if item.severity == "warning"]
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def validate_dataset_join(
    model: SemanticModel,
    source_dataset_id: str,
    target_dataset_id: str,
    *,
    allowed_relationship_ids: set[str],
    preaggregated_source: bool = False,
) -> ValidationResult:
    """Validate one explicitly authorized dataset join before query planning.

    Profiling establishes evidence about a relationship. It does not make that
    relationship available to a query. A caller has to authorize a confirmed
    relationship and make a fanout-safe aggregation decision.
    """
    diagnostics: list[Diagnostic] = []
    direct_relationships = [
        relationship
        for relationship in model.dataset_relationships
        if {relationship.source_dataset_id, relationship.target_dataset_id}
        == {source_dataset_id, target_dataset_id}
    ]
    candidates = [
        relationship for relationship in direct_relationships if relationship.id in allowed_relationship_ids
    ]
    if not candidates:
        code = "unauthorized_relationship" if direct_relationships else "missing_relationship"
        reason = (
            "The requested datasets have a declared relationship, but it is not authorized for this query."
            if direct_relationships
            else "The requested datasets do not have a declared direct relationship."
        )
        _error(
            diagnostics,
            f"{source_dataset_id}->{target_dataset_id}",
            "relationship",
            code,
            reason,
            "Authorize one confirmed relationship for this query." if direct_relationships else "Declare and validate a relationship first.",
        )
        return _result(diagnostics)

    unconfirmed = [relationship for relationship in candidates if relationship.verification_status != "confirmed"]
    if unconfirmed:
        for relationship in unconfirmed:
            _error(
                diagnostics,
                relationship.id,
                "verification_status",
                "unconfirmed_relationship",
                "Only confirmed relationships may be used in a query.",
                "Obtain a trusted data-model definition or business confirmation before authorizing this join.",
            )
        return _result(diagnostics)

    highest_priority = min(relationship.priority for relationship in candidates)
    selected = [relationship for relationship in candidates if relationship.priority == highest_priority]
    if len(selected) != 1:
        for relationship in selected:
            _error(
                diagnostics,
                relationship.id,
                "priority",
                "ambiguous_join_path",
                "More than one authorized, confirmed relationship has the same highest priority.",
                "Choose one relationship or assign distinct priorities.",
            )
        return _result(diagnostics)

    relationship = selected[0]
    # 已声明的 fanout_risk 在任一方向都拒绝。未声明时，从“一”侧连到“多”侧同样会放大源行。
    if not preaggregated_source and (relationship.fanout_risk or _source_rows_multiply(relationship, source_dataset_id)):
        code = "fanout_risk" if relationship.fanout_risk else "potential_fanout"
        _error(
            diagnostics,
            relationship.id,
            "fanout_risk",
            code,
            "This relationship can multiply source rows before aggregation.",
            "Pre-aggregate the source to its declared grain before joining.",
        )
    return _result(diagnostics)


def _source_rows_multiply(relationship, source_dataset_id: str) -> bool:
    """从调用方的源数据集出发，这一跳会不会把源行变成多行。"""
    if relationship.cardinality == "many_to_many":
        return True
    if relationship.cardinality == "one_to_one":
        return False
    if relationship.cardinality == "many_to_one":
        return source_dataset_id == relationship.target_dataset_id
    if relationship.cardinality == "one_to_many":
        return source_dataset_id == relationship.source_dataset_id
    return False


def _validate_unique_ids(model: SemanticModel, diagnostics: list[Diagnostic]) -> None:
    groups = {
        "domains": model.domains,
        "data_sources": model.data_sources,
        "business_objects": model.business_objects,
        "business_relationships": model.business_relationships,
        "datasets": model.datasets,
        "dataset_lineage": model.dataset_lineage,
        "dataset_relationships": model.dataset_relationships,
        "metrics": model.metrics,
        "dimensions": model.dimensions,
    }
    for field, items in groups.items():
        identifiers = [item.id for item in items]
        for identifier in {item for item in identifiers if identifiers.count(item) > 1}:
            _error(diagnostics, identifier, field, "duplicate_id", "IDs must be unique within their object type.",
                   "Choose a stable, unused ID.")


def _validate_catalog_mapping(dataset: Dataset, catalog_objects: dict[str, set[str]], diagnostics: list[Diagnostic]) -> None:
    mapping = dataset.physical_mapping
    columns = catalog_objects.get(mapping.object_name)
    if columns is None:
        _error(diagnostics, dataset.id, "physical_mapping.object_name", "missing_physical_object",
               f"Physical object {mapping.object_name} is not in the supplied catalog.",
               "Correct the object name or refresh the physical catalog.")
        return
    for field_id, column in mapping.column_mappings.items():
        if column not in columns:
            _error(diagnostics, dataset.id, f"physical_mapping.column_mappings.{field_id}", "missing_physical_column",
                   f"Physical column {column} is not in {mapping.object_name}.",
                   "Correct the column mapping or refresh the physical catalog.")


def _validate_relationship_fields(
    model: SemanticModel,
    object_id: str,
    dataset_id: str,
    field_ids: Iterable[str],
    field: str,
    diagnostics: list[Diagnostic],
) -> None:
    dataset = next((item for item in model.datasets if item.id == dataset_id), None)
    if dataset is None:
        return
    _require_members(object_id, field, field_ids, {item.id for item in dataset.fields}, "invalid_join_key", diagnostics)


def _validate_dimension_sources(
    model: SemanticModel, dimension: Dimension, dataset_ids: set[str], diagnostics: list[Diagnostic]
) -> None:
    _validate_recorded_sources(
        model,
        dimension.id,
        dimension.source_dataset_id,
        dimension.source_dataset_ids,
        dataset_ids,
        diagnostics,
        [("field_id", [dimension.field_id])],
    )


def _validate_metric_sources(
    model: SemanticModel, metric: Metric, dataset_ids: set[str], diagnostics: list[Diagnostic]
) -> None:
    field_checks = [("base_grain", list(metric.base_grain))]
    if metric.expression.field_id:
        field_checks.append(("expression.field_id", [metric.expression.field_id]))
    _validate_recorded_sources(
        model,
        metric.id,
        metric.source_dataset_id,
        metric.source_dataset_ids,
        dataset_ids,
        diagnostics,
        field_checks,
    )


def _validate_recorded_sources(
    model: SemanticModel,
    owner_id: str,
    source_dataset_id: str,
    source_dataset_ids: list[str],
    dataset_ids: set[str],
    diagnostics: list[Diagnostic],
    field_checks: list[tuple[str, list[str]]],
) -> None:
    """每个已记录来源都要能解析；问数仍以 source_dataset_id 这一处为准。"""
    recorded = list(source_dataset_ids)
    if source_dataset_id not in recorded:
        _require_recorded_source(
            model, owner_id, source_dataset_id, "source_dataset_id", dataset_ids, diagnostics, field_checks
        )
    for source_id in recorded:
        _require_recorded_source(
            model, owner_id, source_id, "source_dataset_ids", dataset_ids, diagnostics, field_checks
        )


def _require_recorded_source(
    model: SemanticModel,
    owner_id: str,
    source_id: str,
    field: str,
    dataset_ids: set[str],
    diagnostics: list[Diagnostic],
    field_checks: list[tuple[str, list[str]]],
) -> None:
    _require_member(owner_id, field, source_id, dataset_ids, "unknown_dataset", diagnostics)
    for field_name, members in field_checks:
        _validate_relationship_fields(model, owner_id, source_id, members, field_name, diagnostics)


def _validate_metric(
    model: SemanticModel,
    metric: Metric,
    dataset_ids: set[str],
    dimension_ids: set[str],
    metric_ids: set[str],
    diagnostics: list[Diagnostic],
) -> None:
    _validate_metric_sources(model, metric, dataset_ids, diagnostics)
    _require_member(metric.id, "time_dimension_id", metric.time_dimension_id, dimension_ids,
                    "unknown_dimension", diagnostics, optional=True)
    _require_members(metric.id, "compatible_dimension_ids", metric.compatible_dimension_ids, dimension_ids,
                     "unknown_dimension", diagnostics)
    _validate_grain_compatibility(model, metric, diagnostics)
    references = [item for item in [metric.expression.metric_id, metric.expression.numerator_metric_id,
                                     metric.expression.denominator_metric_id] if item]
    _require_members(metric.id, "expression", references, metric_ids, "unknown_metric", diagnostics)
    for filter_ in metric.default_filters:
        _require_member(metric.id, "default_filters.dimension_id", filter_.dimension_id, dimension_ids,
                        "unknown_dimension", diagnostics)
    if metric.published and metric.source_dataset_id not in dataset_ids:
        _error(diagnostics, metric.id, "published", "unpublishable_metric", "Published metrics require a valid source dataset.",
               "Correct the source dataset or set published to false.")


def _validate_grain_compatibility(model: SemanticModel, metric: Metric, diagnostics: list[Diagnostic]) -> None:
    metric_grain = tuple(metric.base_grain)
    for dimension_id in metric.compatible_dimension_ids:
        if _dimension_grain(model, dimension_id) not in {None, metric_grain}:
            _error(
                diagnostics,
                metric.id,
                "compatible_dimension_ids",
                "incompatible_grain",
                "A metric can only be grouped by dimensions at its base grain.",
                "Remove the dimension or define the metric at that dimension's grain.",
            )
    for filter_ in metric.default_filters:
        if _dimension_grain(model, filter_.dimension_id) not in {None, metric_grain}:
            _error(
                diagnostics,
                metric.id,
                "default_filters.dimension_id",
                "incompatible_grain",
                "A metric can only filter by dimensions at its base grain.",
                "Remove the default filter or define the metric at that dimension's grain.",
            )


def _dimension_grain(model: SemanticModel, dimension_id: str) -> tuple[str, ...] | None:
    dimension = next((item for item in model.dimensions if item.id == dimension_id), None)
    if dimension is None:
        return None
    dataset = next((item for item in model.datasets if item.id == dimension.source_dataset_id), None)
    if dataset is None:
        return None
    return tuple(dataset.grain)


def _validate_metric_cycles(metrics: list[Metric], diagnostics: list[Diagnostic]) -> None:
    graph = {
        metric.id: {item for item in [metric.expression.metric_id, metric.expression.numerator_metric_id,
                                      metric.expression.denominator_metric_id] if item}
        for metric in metrics
    }
    visited: set[str] = set()
    active: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in active:
            _error(diagnostics, identifier, "expression", "derived_metric_cycle",
                   "A derived metric depends on itself through metric references.",
                   "Remove the cyclic metric reference.")
            return
        if identifier in visited:
            return
        visited.add(identifier)
        active.add(identifier)
        for dependency in graph.get(identifier, set()):
            if dependency in graph:
                visit(dependency)
        active.remove(identifier)

    for identifier in graph:
        visit(identifier)


def _validate_ambiguous_paths(model: SemanticModel, diagnostics: list[Diagnostic]) -> None:
    graph: dict[str, list[tuple[str, str]]] = {}
    for relationship in model.dataset_relationships:
        graph.setdefault(relationship.source_dataset_id, []).append((relationship.target_dataset_id, relationship.id))
        graph.setdefault(relationship.target_dataset_id, []).append((relationship.source_dataset_id, relationship.id))
    warned: set[str] = set()
    nodes = list(graph)
    for index, start in enumerate(nodes):
        for end in nodes[index + 1:]:
            paths = _simple_paths(graph, start, end)
            if len(paths) < 2:
                continue
            for path in paths:
                for identifier in path:
                    if identifier in warned:
                        continue
                    warned.add(identifier)
                    _warning(
                        diagnostics,
                        identifier,
                        "priority",
                        "ambiguous_join_path",
                        "More than one join path connects the same datasets.",
                        "Assign a unique priority and authorize the intended path explicitly.",
                    )


def _simple_paths(graph: dict[str, list[tuple[str, str]]], start: str, end: str) -> list[list[str]]:
    paths: list[list[str]] = []

    def walk(node: str, visited: set[str], path: list[str]) -> None:
        if node == end and path:
            paths.append(list(path))
            return
        for neighbor, identifier in graph.get(node, []):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            path.append(identifier)
            walk(neighbor, visited, path)
            path.pop()
            visited.remove(neighbor)

    walk(start, {start}, [])
    return paths


def _require_member(
    object_id: str, field: str, value: str | None, allowed: set[str], code: str,
    diagnostics: list[Diagnostic], optional: bool = False,
) -> None:
    if value is None and optional:
        return
    if value not in allowed:
        _error(diagnostics, object_id, field, code, f"Reference {value} does not exist.", "Use an existing stable ID.")


def _require_members(
    object_id: str, field: str, values: Iterable[str], allowed: set[str], code: str,
    diagnostics: list[Diagnostic],
) -> None:
    for value in values:
        _require_member(object_id, field, value, allowed, code, diagnostics)


def _result(diagnostics: list[Diagnostic]) -> ValidationResult:
    errors = [item for item in diagnostics if item.severity == "error"]
    warnings = [item for item in diagnostics if item.severity == "warning"]
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _error(diagnostics: list[Diagnostic], object_id: str, field: str, code: str, reason: str, suggestion: str) -> None:
    diagnostics.append(Diagnostic(severity="error", object_id=object_id, field=field, code=code,
                                  reason=reason, suggestion=suggestion))


def _warning(diagnostics: list[Diagnostic], object_id: str, field: str, code: str, reason: str, suggestion: str) -> None:
    diagnostics.append(Diagnostic(severity="warning", object_id=object_id, field=field, code=code,
                                  reason=reason, suggestion=suggestion))
