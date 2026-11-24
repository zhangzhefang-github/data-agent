import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import date
import logging
import sqlite3
from time import monotonic
from typing import Literal

import duckdb
from psycopg import errors
from pydantic import BaseModel, Field

from app.analysis.analyzer import AnalyzerConfig, Finding, analyze_evidence, drilldown_candidates
from app.analysis.evidence import Evidence
from app.analysis.models import (
    AnalysisBudget, AnalysisPlan, AnalysisTask, InterpretationFocus, TaskRun, TaskStatus, TaskType,
)
from app.analysis.plan_validator import (
    PlanRejected, ValidatedPlan, check_question, requested_dates, restore_omitted_dependency_edges, validate_plan,
)
from app.analysis.planner import AnalysisAgent
from app.analysis.query_executor import compile_task_query, execute_task_query
from app.analysis.report import (
    AnalysisReport, ReportValidation, available_interpretation_focuses, compose_report, validate_report,
)
from app.query import DataSource, TimeRange
from app.semantic.snapshot import content_hash


logger = logging.getLogger(__name__)

Planner = Callable[[str, dict, date, TimeRange | None], Awaitable[AnalysisPlan]]
Progress = Callable[[dict], None]
Interpreter = Callable[[AnalysisPlan, list[Finding], list[InterpretationFocus]], Awaitable[InterpretationFocus | None]]


class AnalysisResult(BaseModel):
    mode: Literal['analysis'] = 'analysis'
    status: Literal['completed', 'partial', 'failed'] = 'failed'
    plan: AnalysisPlan | None = None
    tasks: list[TaskRun] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    report: AnalysisReport | None = None
    report_validation: ReportValidation | None = None
    interpretation_focus: InterpretationFocus | None = None
    incomplete: list[str] = Field(default_factory=list)
    reason: str | None = None
    query_count: int = 0


def _retryable(error: Exception) -> bool:
    if isinstance(error, sqlite3.OperationalError):
        return getattr(error, 'sqlite_errorcode', None) in (
            sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_INTERRUPT,
        )
    return isinstance(error, (TimeoutError, duckdb.InterruptException, errors.QueryCanceled, errors.ConnectionFailure))


class _Execution:
    def __init__(
        self, plan: ValidatedPlan, catalog: dict, source: DataSource, source_label: str | None,
        budget: AnalysisBudget, analyzer_config: AnalyzerConfig, deadline: float, progress: Progress,
    ):
        self.plan = plan.plan
        self.catalog = catalog
        self.source = source
        self.source_label = source_label
        self.snapshot_id = content_hash(catalog)
        self.budget = budget
        self.analyzer_config = analyzer_config
        self.deadline = deadline
        self.progress = progress
        self.result = AnalysisResult(plan=self.plan)
        self.order = plan.ordered_tasks
        self.runs = {}
        self.findings_by_task: dict[str, list[Finding]] = {}
        self.evidence_by_task: dict[str, Evidence] = {}
        for task in self.order:
            self.add_task(task)

    def add_task(self, task: AnalysisTask) -> TaskRun:
        run = TaskRun(
            task_id=task.task_id,
            task_type=task.task_type,
            title=task.title,
            depends_on=task.depends_on,
        )
        self.runs[task.task_id] = run
        self.result.tasks.append(run)
        return run

    def emit_task(self, kind: str, run: TaskRun) -> None:
        self.progress({'kind': kind, 'task': run.model_dump(mode='json')})

    def mark_incomplete(self, run: TaskRun, reason: str) -> None:
        self.result.incomplete.append(f'{run.title}：{reason}')

    def skip(self, run: TaskRun, reason: str, *, incomplete: bool = True) -> None:
        run.status, run.reason = TaskStatus.SKIPPED, reason
        if incomplete:
            self.mark_incomplete(run, reason)
        self.emit_task('task_skipped', run)

    def fail(self, run: TaskRun, reason: str) -> None:
        run.status, run.reason = TaskStatus.FAILED, reason
        self.mark_incomplete(run, reason)
        self.emit_task('task_failed', run)

    def stop_reason(self) -> str | None:
        if asyncio.get_running_loop().time() >= self.deadline:
            return '达到分析时间上限。'
        if self.result.query_count >= self.budget.max_queries:
            return '达到 SQL 查询次数上限。'
        return None

    async def execute(self, task: AnalysisTask, run: TaskRun, *, store_value: str | int | None = None) -> None:
        stop = self.stop_reason()
        if stop:
            self.skip(run, stop)
            return
        if any(self.runs[parent].status != TaskStatus.COMPLETED for parent in task.depends_on):
            self.skip(run, '前置任务未完成。')
            return
        run.status = TaskStatus.RUNNING
        self.emit_task('task_started', run)
        try:
            query = compile_task_query(
                task, self.plan, self.catalog, self.source, self.budget.result_limit, store_value=store_value,
            )
        except Exception as exc:
            logger.warning('Analysis SQL compilation rejected (%s)', type(exc).__name__)
            self.fail(run, '查询未通过销售数据源的授权和配置校验。')
            return

        for _ in range(self.budget.max_repairs + 1):
            stop = self.stop_reason()
            if stop:
                self.fail(run, stop)
                return
            self.result.query_count += 1
            run.attempts += 1
            try:
                remaining = self.deadline - asyncio.get_running_loop().time()
                evidence = await asyncio.to_thread(
                    execute_task_query, query, task=task, plan=self.plan, source=self.source,
                    snapshot_id=self.snapshot_id, store_id=run.store_id, source_label=self.source_label,
                    deadline=monotonic() + remaining,
                )
            except Exception as exc:
                if self.stop_reason() == '达到分析时间上限。':
                    self.fail(run, '查询在分析时间上限内未完成。')
                    return
                if _retryable(exc) and run.attempts <= self.budget.max_repairs:
                    continue
                logger.warning('Analysis query failed (%s)', type(exc).__name__)
                self.fail(run, '数据查询失败。')
                return
            break

        self.evidence_by_task[task.task_id] = evidence
        self.result.evidence.append(evidence)
        run.status, run.evidence_id = TaskStatus.COMPLETED, evidence.evidence_id
        if evidence.possible_truncation:
            run.reason = '查询结果达到行数上限，分组可能不完整。'
            self.mark_incomplete(run, run.reason)
        self.emit_task('task_completed', run)
        try:
            findings = analyze_evidence(evidence, self.analyzer_config)
        except (ArithmeticError, ValueError) as exc:
            logger.warning('Analysis statistics failed (%s)', type(exc).__name__)
            self.mark_incomplete(run, '查询已保存，统计计算未完成。')
        else:
            self.findings_by_task[task.task_id] = findings
            self.result.findings.extend(findings)

    async def drilldown(self, task: AnalysisTask, template: TaskRun) -> None:
        parent_id = task.depends_on[0]
        evidence = self.evidence_by_task.get(parent_id)
        if evidence is None:
            self.skip(template, '门店分布未完成。')
            return
        candidates = drilldown_candidates(self.findings_by_task.get(parent_id, []))
        column = evidence.columns.index('store')
        observed: dict[str, list[str | int]] = {}
        for row in evidence.rows:
            value = row[column]
            if isinstance(value, (str, int)):
                observed.setdefault(str(value), []).append(value)
        stores = [(label, observed[label][0]) for label in candidates if len(observed.get(label, ())) == 1]
        if not stores:
            self.skip(template, '本轮未获得可用于下钻的门店销售额统计候选。', incomplete=False)
            return
        if self.budget.max_drilldown_depth == 0 or self.budget.max_diagnostic_stores == 0:
            self.skip(template, '达到门店下钻预算。')
            return
        for index, (store_id, store_value) in enumerate(stores[:self.budget.max_diagnostic_stores], start=1):
            stop = self.stop_reason()
            if stop:
                if index == 1:
                    self.skip(template, stop)
                else:
                    self.mark_incomplete(template, stop)
                return
            if index == 1:
                run = template
                task_instance = task
            else:
                # The separator is outside the planner's task ID alphabet.
                task_instance = task.model_copy(update={'task_id': f'{task.task_id}~{index}'})
                run = self.add_task(task_instance)
            run.store_id, run.depth = store_id, 1
            self.emit_task('drilldown', run)
            await self.execute(task_instance, run, store_value=store_value)
        if len(stores) > self.budget.max_diagnostic_stores:
            self.mark_incomplete(template, '达到门店下钻数量上限。')

    async def run(self, interpreter: Interpreter | None) -> AnalysisResult:
        scope = self.plan.scope.model_dump(mode='json', exclude={'scope_issue'})
        self.progress({'kind': 'plan', 'plan': {
            'analysis_id': self.plan.analysis_id,
            'goal': self.plan.goal,
            'scope': scope,
            'tasks': [task.model_dump(mode='json') for task in self.result.tasks],
        }})
        for task in self.order:
            if task.task_type != TaskType.STORE_PRODUCT_DRILLDOWN:
                await self.execute(task, self.runs[task.task_id])
        for task in self.order:
            if task.task_type == TaskType.STORE_PRODUCT_DRILLDOWN:
                await self.drilldown(task, self.runs[task.task_id])
        await self.finish_report(interpreter)
        return self.result

    async def interpreted_report(
        self, interpreter: Interpreter,
    ) -> tuple[InterpretationFocus, AnalysisReport, ReportValidation] | None:
        result = self.result
        choices = available_interpretation_focuses(result.findings)
        loop = asyncio.get_running_loop()
        deadline = min(self.deadline, loop.time() + self.budget.interpretation_timeout_seconds)
        if not choices or loop.time() >= deadline:
            return None
        try:
            async with asyncio.timeout_at(deadline):
                focus = await interpreter(self.plan, result.findings, choices)
            if focus not in choices:
                return None
            report = compose_report(self.plan, result.evidence, result.findings, result.incomplete, focus=focus)
            validation = validate_report(
                report, self.plan, result.evidence, result.findings, result.incomplete, self.analyzer_config,
                focus=focus,
            )
            return (focus, report, validation) if validation.valid else None
        except Exception as exc:
            logger.debug('Using the validated base report after interpretation ended (%s)', type(exc).__name__)
            return None

    async def finish_report(self, interpreter: Interpreter | None) -> None:
        self.progress({'kind': 'report', 'status': 'RUNNING'})
        result = self.result
        try:
            report = compose_report(self.plan, result.evidence, result.findings, result.incomplete)
            validation = validate_report(
                report, self.plan, result.evidence, result.findings, result.incomplete, self.analyzer_config,
            )
        except (ArithmeticError, ValueError) as exc:
            logger.warning('Analysis report validation failed (%s)', type(exc).__name__)
            validation = ReportValidation(status='failed', errors=['分析报告未完成证据一致性验证。'])
        result.report_validation = validation
        if not validation.valid:
            result.reason = '报告未通过证据一致性验证。'
            result.findings = []
            self.progress({'kind': 'report', 'status': 'FAILED'})
            return
        result.report = report
        if interpreter is not None:
            interpreted = await self.interpreted_report(interpreter)
            if interpreted is not None:
                result.interpretation_focus, result.report, result.report_validation = interpreted
        if not result.evidence:
            result.reason = '本次分析没有取得成功查询的证据。'
        elif result.incomplete:
            result.status = 'partial'
            result.reason = '部分探索任务未完成，报告仅覆盖已获得的证据。'
        else:
            result.status = 'completed'
        self.progress({'kind': 'report', 'status': 'COMPLETED'})


async def run_analysis(
    *, question: str, catalog: dict, source: DataSource, today: date,
    planner: Planner | None = None, budget: AnalysisBudget = AnalysisBudget(),
    on_progress: Progress | None = None, time_range: TimeRange | None = None,
    source_label: str | None = None, analyzer_config: AnalyzerConfig = AnalyzerConfig(),
    interpreter: Interpreter | None = None,
) -> AnalysisResult:
    deadline = asyncio.get_running_loop().time() + budget.timeout_seconds
    catalog = deepcopy(catalog)
    try:
        if catalog.get('id') != 'sales':
            raise PlanRejected('当前主题空间未授权销售探索分析。')
        check_question(question)
        time_range = time_range or requested_dates(question, today)
        if planner is None:
            agent = AnalysisAgent()
            planner = agent.plan
            interpreter = agent.interpret if interpreter is None else interpreter
        async with asyncio.timeout_at(deadline):
            plan = AnalysisPlan.model_validate(await planner(question, catalog, today, time_range))
        plan = restore_omitted_dependency_edges(plan)
        validated = validate_plan(
            plan, catalog, today, budget, question=question, requested_time_range=time_range,
        )
    except PlanRejected as exc:
        return AnalysisResult(reason=str(exc))
    except TimeoutError:
        return AnalysisResult(reason='分析规划超时。')
    except Exception as exc:
        logger.warning('Analysis planner failed (%s)', type(exc).__name__)
        return AnalysisResult(reason='未能生成可执行的销售分析计划。')
    return await _Execution(
        validated, catalog, source, source_label, budget, analyzer_config, deadline,
        on_progress if on_progress is not None else lambda _: None,
    ).run(interpreter)
