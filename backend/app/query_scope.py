"""把一次语义上下文投影成可查询范围说明。"""

import re

from app.semantic.context_builder import ContextDataset, SemanticContext

_SCOPE_QUESTIONS = {
    "我能查询哪些信息",
    "能查询哪些信息",
    "可以查询哪些信息",
    "我能查哪些信息",
    "能查哪些信息",
    "可以查哪些信息",
    "我能查什么",
    "能查什么",
    "可以查什么",
    "你能查询什么",
    "你能查什么",
    "可查询的数据范围",
    "有哪些数据可以查",
    "能问什么",
}
# 「我能查询什么」「我们查询什么」这类说法不在固定清单里，但仍是能力问题。
_SCOPE_PATTERN = re.compile(
    r"^(?:我|你|我们|咱们)?(?:想)?(?:能|可以)?(?:查询|查)(?:哪些|什么)(?:信息|数据|内容)?$"
)
_CARDINALITY = {
    "many_to_one": "多对一",
    "one_to_many": "一对多",
    "one_to_one": "一对一",
    "many_to_many": "多对多",
}
_DATASET_ORDER = {"fact": 0, "dimension": 1}


def is_query_scope_question(message: str) -> bool:
    """精确的能力问题走原文，不交给模型改写。"""
    normalized = _normalize_scope_question(message)
    return normalized in _SCOPE_QUESTIONS or _SCOPE_PATTERN.fullmatch(normalized) is not None


def looks_like_scope_answer(text: str) -> bool:
    """模型已经写出授权范围时，保留这段说明，不改成错误或内部指令。"""
    stripped = text.strip()
    return stripped.startswith("可查询的数据范围") or stripped.startswith("这是订单主题空间")


def _normalize_scope_question(message: str) -> str:
    normalized = re.sub(r"\s+", "", message.strip()).strip("？?。.!！")
    for prefix in ("请问", "想问一下", "告诉我"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized


def render_query_scope(context: SemanticContext) -> str:
    """按授权数据集、关系和示例问题生成用户可见的范围说明。"""
    datasets = sorted(
        context.sql.datasets,
        key=lambda dataset: (_DATASET_ORDER.get(dataset.dataset_type, 2), dataset.name),
    )
    by_id = {dataset.id: dataset for dataset in datasets}
    lines = [
        "可查询的数据范围",
        _intro(context),
    ]
    lines.extend(_dataset_line(index, dataset, context) for index, dataset in enumerate(datasets, start=1))
    lines.extend(_relationship_line(relationship, by_id, context) for relationship in context.sql.relationships)
    lines.extend(rule for rule in context.business.filter_rules if "姓名" in rule)
    if context.business.example_questions:
        lines.append("可以这样问")
        lines.extend(context.business.example_questions)
    lines.append("直接说出要看的时间、状态或客户即可查询。")
    return "\n".join(lines)


def _intro(context: SemanticContext) -> str:
    dimensions = "、".join(dimension.name for dimension in context.business.dimensions)
    sentences = [f"这是{context.space_name}"]
    if dimensions:
        sentences.append(f"可以按{dimensions}筛选或分组")
    if context.sql.relationships:
        sentences.append("也可以按已授权关系关联后再汇总")
    return "。".join(sentences) + "。"


def _dataset_line(index: int, dataset: ContextDataset, context: SemanticContext) -> str:
    metrics = [metric for metric in context.business.metrics if metric.source_dataset_id == dataset.id]
    dimensions = [dimension for dimension in context.business.dimensions if dimension.source_dataset_id == dataset.id]
    sentences = [f"{index}. {dataset.name}（{dataset.physical_object}）"]
    if metrics:
        sentences.append("可查询" + "、".join(metric.name for metric in metrics))
        sentences.extend(
            metric.definition
            for metric in metrics
            if any(token in metric.definition for token in ("不要", "全部状态"))
        )
    if dimensions:
        sentences.append("可按" + "、".join(dimension.name for dimension in dimensions) + "筛选和分组")
        for dimension in dimensions:
            if dimension.allowed_values:
                sentences.append(f"{dimension.name}包括{'、'.join(dimension.allowed_values)}")
    if not metrics and not dimensions:
        # 列来自集合，显示顺序必须固定，否则同一范围说明每次不同。
        columns = "、".join(sorted(_field_name(context, field_id) for field_id in dataset.columns))
        sentences.append(f"当前可关联的列是{columns}" if columns else "当前没有可直接筛选的列")
    return "。".join(part.strip("。") for part in sentences if part) + "。"


def _relationship_line(relationship, datasets: dict[str, ContextDataset], context: SemanticContext) -> str:
    source = datasets[relationship.source_dataset_id]
    target = datasets[relationship.target_dataset_id]
    left = "、".join(_field_name(context, field_id) for field_id in relationship.source_fields)
    right = "、".join(_field_name(context, field_id) for field_id in relationship.target_fields)
    keys = left if left == right else f"{source.name}的{left}与{target.name}的{right}"
    join = "等值内连接" if relationship.join_type == "inner" else relationship.join_type
    cardinality = _CARDINALITY.get(relationship.cardinality, relationship.cardinality)
    fanout = "不会放大行数" if not relationship.fanout_risk else "关联可能放大行数"
    return f"{source.name}与{target.name}按{keys}{join}，{cardinality}，{fanout}。"


def _field_name(context: SemanticContext, field_id: str) -> str:
    for dimension in context.business.dimensions:
        if dimension.field_id == field_id:
            return dimension.name
    return field_id
