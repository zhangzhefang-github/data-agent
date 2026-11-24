import re
from dataclasses import dataclass
from datetime import date, timedelta
from graphlib import CycleError, TopologicalSorter

from app.analysis.models import (
    SALES_DIMENSIONS, SALES_METRICS, AnalysisBudget, AnalysisDependency, AnalysisFilter, AnalysisPlan, AnalysisTask,
    TaskType,
)
from app.query import TimeRange, physical_value


class PlanRejected(ValueError):
    """A plan cannot run within the sales Space authorization and budgets."""


@dataclass(frozen=True)
class ValidatedPlan:
    plan: AnalysisPlan
    ordered_tasks: tuple[AnalysisTask, ...]


_UNSUPPORTED = re.compile(
    r'加盟|交易(?:明细|价格|单价|时间|时间戳|流水)|订单明细|商品(?:件数|数量|销量)|销量|'
    r'半天|日内|transaction[\s_]+(?:details?|prices?|timestamps?)|franchis(?:e|ee)(?:_id|s)?\b|\bquantity\b',
    re.IGNORECASE,
)
_TIME_MENTION = re.compile(
    r'\b(?:19|20)\d{2}\b|\d{4}[-年/]\d{1,2}|\d{4}年|\d{1,2}月|[今去前本]年|[本当上]月|'
    r'(?:上|前)个?季度|上个月|最近|近(?:期|来|[一二两三四五六七八九十\d]+[天周日月年])|过去|'
    r'[今昨前][天日]|[上本]周|[本当]季度|\b(?:today|yesterday|(?:last|previous|past|this)\s+'
    r'(?:\d+\s+)?(?:day|week|month|quarter|year)s?|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|'
    r'may(?!\s+(?:i|we|you|he|she|they)\b)|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|'
    r'nov(?:ember)?|dec(?:ember)?)\b',
    re.IGNORECASE,
)
_EXPLICIT_DATES = re.compile(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)')
_TASK_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}')
_SCOPE_DATES = re.compile(
    r'\d{4}-\d{2}-\d{2}(?:\s*(?:至|到|[-—–~～]|to|through|until|and)\s*\d{4}-\d{2}-\d{2})?'
    r'(?:\s*当天)?|今年|本月|这个月|上个月|上月|最近\s*\d+\s*天'
)
_GROUPING = re.compile(r'(?:按|各)(?:业务日期|城市|门店|产品)(?:分组|统计|分析|查看|进一步分析)?')
_METRIC = re.compile(r'净销售额|销售金额|销售额|订单量|客单价|销售趋势')
_DIMENSION_LABELS = {'城市': 'city', '门店': 'store', '产品': 'product'}
_BUILTIN_CITIES = ('北京', '上海', '广州', '深圳', '杭州', '成都', '重庆', '天津')


def requested_filters(question: str, catalog: dict) -> list[AnalysisFilter]:
    """Resolve explicit filter values independently of the model's grouping choices."""
    question = _SCOPE_DATES.sub('', question)
    question = _GROUPING.sub('', question)
    if re.search(r'排除|除外|以外|不含|不包含|非北京', question):
        raise PlanRejected('请确认筛选条件，目前仅支持等值筛选。')
    prefix, *suffix = _METRIC.split(question, maxsplit=1)
    text = re.sub(r'^(?:请问|请|查询|统计|分析|看看|查看|检查|为什么|想知道|帮我)+', '', prefix.strip())
    if suffix and re.search(r'(?:仅|只(?:看|查|分析)|限定|筛选|城市|产品|门店)', suffix[0]):
        raise PlanRejected('请在指标前明确城市、产品或门店筛选条件。')

    names: dict[str, set[str]] = {}
    for label, dimension in _DIMENSION_LABELS.items():
        for entry in catalog['dimensions'].get(dimension, {}).get('values', []):
            for value in (entry['name'], entry['value']):
                names.setdefault(str(value), set()).add(dimension)
    for city in _BUILTIN_CITIES:
        names.setdefault(city, set()).add('city')
    results = []
    while text := text.strip(' 的，,、'):
        quoted = re.match(r'(城市|门店|产品)\s*[「“"]([^」”"]+)[」”"]', text)
        tagged = re.match(r'([^的，,、]+?)(城市|门店|产品)', text)
        known = next((value for value in sorted(names, key=len, reverse=True) if text.startswith(value)), None)
        if quoted:
            label, value = quoted.groups()
            dimension, consumed = _DIMENSION_LABELS[label], quoted.end()
        elif tagged and (known is None or tagged[1] == known):
            value, label = tagged.groups()
            dimension, consumed = _DIMENSION_LABELS[label], tagged.end()
            if dimension == 'store' and re.search(r'[区县市]$', value):
                raise PlanRejected('请确认门店筛选条件并提供门店标识。')
        elif known and len(names[known]) == 1:
            value, dimension, consumed = known, next(iter(names[known])), len(known)
        else:
            raise PlanRejected('请确认筛选条件，明确取值对应的城市、门店或产品。')
        results.append(AnalysisFilter(dimension_id=dimension, value=value.strip()))
        text = text[consumed:]
    return results


def check_question(question: str) -> None:
    if _UNSUPPORTED.search(question):
        raise PlanRejected('销售 ADS 只能分析业务日期、城市、门店、产品及已授权指标，无法判断所请求的业务口径或日内数据。')


def requested_dates(question: str, today: date) -> TimeRange | None:
    dates = _EXPLICIT_DATES.findall(question)
    try:
        if len(dates) == 2:
            return TimeRange(start=date.fromisoformat(dates[0]), end=date.fromisoformat(dates[1]))
        if len(dates) == 1:
            day = date.fromisoformat(dates[0])
            return TimeRange(start=day, end=day)
        if re.search(r'本月|这个月', question):
            return TimeRange(start=today.replace(day=1), end=today)
        if re.search(r'上个月|上月', question):
            end = today.replace(day=1) - timedelta(days=1)
            return TimeRange(start=end.replace(day=1), end=end)
        recent = re.search(r'最近\s*(\d+)\s*天', question)
        if recent and int(recent.group(1)) > 0:
            return TimeRange(start=today - timedelta(days=int(recent.group(1)) - 1), end=today)
        if '今年' in question:
            return TimeRange(start=today.replace(month=1, day=1), end=today)
    except (ValueError, OverflowError) as exc:
        raise PlanRejected('需要确认所请求的分析起止日期。') from exc
    if _TIME_MENTION.search(question):
        raise PlanRejected('需要确认所请求的分析起止日期。')
    return None


def _resolve_range(
    planned: TimeRange | None, requested: TimeRange | None, question: str, today: date, max_days: int,
) -> TimeRange:
    parsed = requested_dates(question, today)
    if parsed and planned is None and requested is None:
        raise PlanRejected('需要确认所请求的分析起止日期。')
    if parsed and requested and parsed != requested:
        raise PlanRejected('分析计划改变了用户指定的时间范围。')
    requested = requested or parsed
    if requested and planned and planned != requested:
        raise PlanRejected('分析计划改变了用户指定的时间范围。')
    default = TimeRange(start=today.replace(month=1, day=1), end=today)
    if requested is None:
        has_time_mention = bool(_TIME_MENTION.search(question))
        if (has_time_mention and planned is None) or (not has_time_mention and planned not in (None, default)):
            raise PlanRejected('需要确认所请求的分析起止日期。')
    span = requested or planned or default
    explicit = list(_EXPLICIT_DATES.finditer(question))
    if len(explicit) > 2:
        raise PlanRejected('当前销售分析需要一个连续的日期范围。')
    if len(explicit) == 2:
        connector = question[explicit[0].end():explicit[1].start()].strip()
        if not re.fullmatch(r'至|到|[-—–~～]|to|through|until|and', connector, re.IGNORECASE):
            raise PlanRejected('当前销售分析需要一个连续的日期范围。')
        if (span.start.isoformat(), span.end.isoformat()) != tuple(match.group() for match in explicit):
            raise PlanRejected('分析计划未保留用户指定的起止日期。')
    if len(explicit) == 1 and not span.start.isoformat() <= explicit[0].group() <= span.end.isoformat():
        raise PlanRejected('分析计划未保留用户指定的业务日期。')
    if span.end > today or (span.end - span.start).days + 1 > max_days:
        raise PlanRejected(f'分析时间需截至当前业务日期，且范围最多包含 {max_days} 天。')
    return span


def _validate_scope(plan: AnalysisPlan, catalog: dict, question: str) -> None:
    if catalog.get('id') != 'sales' or plan.scope.space_id != catalog['id']:
        raise PlanRejected('当前主题空间未授权销售探索分析。')
    if plan.scope.scope_issue == 'unsupported':
        raise PlanRejected('所请求的指标、维度或筛选方式超出销售 ADS 分析范围。')
    if plan.scope.scope_issue == 'clarification_required':
        raise PlanRejected('需要确认分析的时间或业务口径。')
    filters = set()
    for item in plan.scope.filters:
        dimension = catalog['dimensions'].get(item.dimension_id)
        if (
            item.dimension_id not in SALES_DIMENSIONS - {'stat_date'}
            or not dimension or dimension.get('model') != 'sales_summary'
        ):
            raise PlanRejected('分析筛选包含未授权维度。')
        if not item.value.strip() or item.value not in question:
            raise PlanRejected('分析筛选必须准确保留用户明确指定的取值。')
        try:
            value = physical_value(dimension, item.value)
        except ValueError as exc:
            raise PlanRejected('分析筛选取值超出维度字典。') from exc
        key = (item.dimension_id, value)
        if key in filters:
            raise PlanRejected('分析计划包含重复筛选。')
        filters.add(key)
    expected = requested_filters(question, catalog)
    try:
        requested = {(item.dimension_id, physical_value(catalog['dimensions'][item.dimension_id], item.value))
                     for item in expected}
    except (ValueError, KeyError) as exc:
        raise PlanRejected('筛选取值超出授权维度字典。') from exc
    if filters != requested:
        raise PlanRejected('分析计划未准确保留用户指定的筛选条件。')


def _check_task(task: AnalysisTask, tasks: dict[str, AnalysisTask], plan: AnalysisPlan, catalog: dict) -> None:
    metrics, dimensions = set(task.metric_ids), set(task.dimension_ids)
    if not task.title.strip() or not task.objective.strip() or not task.rationale.strip():
        raise PlanRejected('分析任务需要标题、目的和业务理由。')
    if len(metrics) != len(task.metric_ids) or len(dimensions) != len(task.dimension_ids):
        raise PlanRejected('分析任务包含重复指标或维度。')
    for identifier in metrics:
        metric = catalog['metrics'].get(identifier)
        if identifier not in SALES_METRICS or not metric or metric.get('model') != 'sales_summary':
            raise PlanRejected('分析任务包含未授权指标。')
        filters = [item.dimension_id for item in plan.scope.filters]
        if task.task_type == TaskType.STORE_PRODUCT_DRILLDOWN:
            filters.append('store')
        if any(field not in metric.get('allowed_filters', filters) for field in filters):
            raise PlanRejected('指标不支持所请求的筛选维度。')
    if not dimensions <= SALES_DIMENSIONS or any(
        catalog['dimensions'].get(item, {}).get('model') != 'sales_summary' for item in dimensions
    ):
        raise PlanRejected('分析任务包含未授权维度。')
    if task.task_type == TaskType.PROFILE_DATA_RANGE:
        if metrics or dimensions or task.depends_on:
            raise PlanRejected('数据范围检查需要作为基础任务执行。')
        return
    if not metrics:
        raise PlanRejected('分析查询至少需要一个已授权指标。')
    if task.task_type == TaskType.DAILY_TREND and dimensions != {'stat_date'}:
        raise PlanRejected('每日趋势需要使用业务日期维度。')
    if task.task_type == TaskType.DIMENSION_BREAKDOWN and dimensions not in ({'city'}, {'store'}, {'product'}):
        raise PlanRejected('维度分布需要选择城市、门店或产品中的一项。')
    if task.task_type == TaskType.STORE_PRODUCT_DRILLDOWN:
        if dimensions != {'product'} or len(task.depends_on) != 1:
            raise PlanRejected('门店产品下钻需要依赖一项门店分布任务。')
        parent = tasks[task.depends_on[0]]
        if (
            parent.task_type != TaskType.DIMENSION_BREAKDOWN
            or parent.dimension_ids != ['store']
            or metrics != set(parent.metric_ids)
            or 'sales_amount' not in metrics
        ):
            raise PlanRejected('门店产品下钻仅能复用门店销售额分布所用指标。')


def restore_omitted_dependency_edges(plan: AnalysisPlan) -> AnalysisPlan:
    """任务依赖和依赖清单只写了一边时，用已写的一边补齐。两边都写了但不一致则保持原样。"""
    task_edges = [(task.task_id, parent) for task in plan.tasks for parent in task.depends_on]
    listed = [(edge.task_id, edge.depends_on) for edge in plan.dependencies]
    if task_edges and not listed:
        return plan.model_copy(update={'dependencies': [
            AnalysisDependency(task_id=task_id, depends_on=parent) for task_id, parent in task_edges
        ]})
    if listed and not task_edges:
        grouped: dict[str, list[str]] = {}
        for task_id, parent in listed:
            grouped.setdefault(task_id, []).append(parent)
        return plan.model_copy(update={'tasks': [
            task.model_copy(update={'depends_on': grouped.get(task.task_id, [])}) for task in plan.tasks
        ]})
    return plan


def validate_plan(
    plan: AnalysisPlan, catalog: dict, today: date, budget: AnalysisBudget, *,
    question: str, requested_time_range: TimeRange | None = None,
) -> ValidatedPlan:
    check_question(question)
    _validate_scope(plan, catalog, question)
    if not _TASK_ID.fullmatch(plan.analysis_id) or not plan.goal.strip():
        raise PlanRejected('分析计划需要有效标识和目标。')
    span = _resolve_range(plan.scope.time_range, requested_time_range, question, today, budget.max_range_days)
    if not 1 <= len(plan.tasks) <= budget.max_tasks:
        raise PlanRejected(f'分析计划须包含 1 至 {budget.max_tasks} 项任务。')
    tasks = {task.task_id: task for task in plan.tasks}
    if len(tasks) != len(plan.tasks) or not all(_TASK_ID.fullmatch(identifier) for identifier in tasks):
        raise PlanRejected('分析任务标识必须有效且唯一。')
    graph = {task.task_id: tuple(task.depends_on) for task in tasks.values()}
    for parents in graph.values():
        if len(set(parents)) != len(parents) or set(parents) - tasks.keys():
            raise PlanRejected('分析计划包含重复或不存在的依赖任务。')
    try:
        order = tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise PlanRejected('分析任务不能有循环依赖。') from exc
    expected_edges = [(identifier, parent) for identifier, parents in graph.items() for parent in parents]
    supplied_edges = [(edge.task_id, edge.depends_on) for edge in plan.dependencies]
    if set(supplied_edges) != set(expected_edges) or len(supplied_edges) != len(expected_edges):
        raise PlanRejected('分析计划的依赖清单与任务依赖不一致。')
    operations = set()
    for task in tasks.values():
        _check_task(task, tasks, plan, catalog)
        if task.task_type != TaskType.STORE_PRODUCT_DRILLDOWN and any(
            tasks[parent].task_type == TaskType.STORE_PRODUCT_DRILLDOWN for parent in task.depends_on
        ):
            raise PlanRejected('固定分析查询不能依赖尚未确定的门店下钻。')
        signature = (task.task_type, frozenset(task.dimension_ids))
        if signature in operations:
            raise PlanRejected('分析计划包含重复查询。')
        operations.add(signature)
    if re.search(r'销售额.*(?:异常|反常|离群)', question):
        present = {task.task_type for task in tasks.values()}
        required = {TaskType.PROFILE_DATA_RANGE, TaskType.DAILY_TREND, TaskType.DIMENSION_BREAKDOWN}
        if not required <= present:
            raise PlanRejected('销售额异常分析缺少必需任务。')
        for kind in (TaskType.DAILY_TREND, TaskType.DIMENSION_BREAKDOWN):
            if not any(task.task_type == kind and 'sales_amount' in task.metric_ids for task in tasks.values()):
                raise PlanRejected('销售额异常分析的趋势和维度任务必须包含销售额。')
    scope = plan.scope.model_copy(update={'time_range': span})
    normalized = plan.model_copy(update={
        'scope': scope,
        'stop_conditions': [
            f'最多执行 {budget.max_queries} 次 SQL 查询（含重试）。',
            f'达到 {budget.timeout_seconds:g} 秒的分析时间上限时停止查询。',
            f'最多下钻 {budget.max_diagnostic_stores} 个门店，深度不超过 {budget.max_drilldown_depth}。',
            '只有完成查询并获得可验证的门店销售额统计候选时才下钻。',
        ],
    })
    ordered_tasks = sorted(
        (tasks[identifier] for identifier in order), key=lambda task: task.task_type != TaskType.PROFILE_DATA_RANGE,
    )
    return ValidatedPlan(normalized, tuple(ordered_tasks))
