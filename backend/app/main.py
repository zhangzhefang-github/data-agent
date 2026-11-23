import asyncio
import duckdb
import fcntl
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_BASE_URL", "https://api.agicto.cn/v1")

from agents import Agent, RunConfig, Runner, function_tool, set_tracing_export_api_key
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.demo import initialize
from app import conversations
from app.order_clarification import decide, resume
from app.direct_sql import (
    SqlCorrection, SqlRejected, SqlTimeout, context_coverage_gap, execute_order_sql,
    order_database_context, prior_order_sql,
)
from app.query_scope import is_query_scope_question, render_query_scope
from app.semantic.query_ir_adapter import catalog_from_semantic_context
from app.semantic.retrieval import (
    MAX_CONTEXT_COVERAGE, expand_authorization, expansion_payload, include_datasets, is_small_space, short_catalog,
)
from app.semantic.snapshot import build_orders_snapshot, validation_inputs
from app.sql_prompt import authorized_tables, render_direct_sql_prompt, render_two_phase_prompt
from app.query import Query, TimeRange, execute_query, load_catalog, locate_source, normalize_query, question_catalog, space_instructions

if os.getenv("OPENAI_TRACING_API_KEY"):
    set_tracing_export_api_key(os.environ["OPENAI_TRACING_API_KEY"])

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_SQLITE_PATH = os.getenv("SALES_SQLITE_PATH")
DATABASE = Path(EXTERNAL_SQLITE_PATH) if EXTERNAL_SQLITE_PATH else ROOT / "data" / "demo" / "sales.db"
DATABASE_URL = os.getenv("SALES_DATABASE_URL") or None
DUCKDB_PATH = os.getenv("SALES_DUCKDB_PATH") or None
CONVERSATIONS = ROOT / "data" / "conversations.db"
CATALOG = load_catalog(ROOT, os.getenv("SALES_SPACE", "sales"))
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.on_event("startup")
def setup():
    if locate_source(DATABASE, DATABASE_URL, DUCKDB_PATH).engine == "sqlite" and not EXTERNAL_SQLITE_PATH:
        initialize(DATABASE)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/spaces")
def spaces():
    return [CATALOG]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal['correct', 'incorrect']


@app.post('/api/feedback')
def submit_feedback(request: FeedbackRequest):
    if not conversations.save_feedback(CONVERSATIONS, request.message_id, request.rating):
        raise HTTPException(status_code=404, detail='回答不存在')
    return {'message_id': request.message_id, 'rating': request.rating}


@app.get("/api/conversations")
def conversation_list():
    return conversations.list_conversations(CONVERSATIONS)


@app.get("/api/conversations/{conversation_id}")
def conversation_detail(conversation_id: str):
    conversation = conversations.get(CONVERSATIONS, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


def event(kind: str, payload: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def last_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


@app.post("/api/chat/stream")
async def chat(request: ChatRequest):
    if request.conversation_id:
        conversation_id = request.conversation_id
        conversation = conversations.get(CONVERSATIONS, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在")
    else:
        conversation_id = conversations.create(CONVERSATIONS, request.message)
        conversation = conversations.get(CONVERSATIONS, conversation_id)
    lock_dir = CONVERSATIONS.parent / ".conversation-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = (lock_dir / conversation_id).open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise HTTPException(status_code=409, detail="该会话正在查询，请稍后再试")
    # Refresh after acquiring the interprocess lock so the context cannot be stale.
    conversation = conversations.get(CONVERSATIONS, conversation_id)
    try:
        conversations.add_message(CONVERSATIONS, conversation_id, "user", request.message)
    except Exception:
        lock.close()
        raise

    async def stream():
        result_data = []
        query_data = []
        answer = []
        saved = False
        terminal = None
        context = conversation["last_query"] if conversation else None
        is_previous_period = bool(re.search(r"去年同期", request.message))
        source = locate_source(DATABASE, DATABASE_URL, DUCKDB_PATH)
        direct_sql = CATALOG["id"] == "orders" and source.engine == "duckdb"
        business_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        # 一次请求只发布一份快照。销售空间仍使用进程目录，不读这份订单快照。
        snapshot = build_orders_snapshot(ROOT) if direct_sql else None
        saved_sql = context.get("sql") if context and context.get("mode") == "orders_duckdb" else None
        previous_sql = None
        if direct_sql:
            if not isinstance(saved_sql, str):
                context = None
                saved_sql = None
            else:
                try:
                    prior_order_sql(
                        saved_sql, catalog_from_semantic_context(snapshot.authorization), business_today,
                        joins=validation_inputs(snapshot)[1],
                    )
                except ValueError:
                    context = None
                    saved_sql = None
        elif context and context.get("mode") == "orders_duckdb":
            context = None

        effective_question = request.message
        if direct_sql:
            pending = conversation.get("pending_clarification") if conversation else None
            resumed = resume(pending, request.message) if pending else None
            resolved = set()
            if resumed:
                effective_question, resolved = resumed
            if pending:
                conversations.set_pending(CONVERSATIONS, conversation_id, None)
            decision = decide(effective_question, resolved)
            if decision:
                status, message, kind = decision
                if kind:
                    conversations.set_pending(CONVERSATIONS, conversation_id, {
                        "question": effective_question, "kind": kind, "resolved": sorted(resolved), "reason": message,
                    })
                yield event("conversation", {"conversation_id": conversation_id})
                yield event("text", {"delta": message})
                outcome = {"status": status, "reason": message}
                message_id = conversations.add_message(CONVERSATIONS, conversation_id, "assistant", message, outcome=outcome)
                yield event("message", {"id": message_id})
                yield event("done", {"status": "completed", "outcome": outcome})
                return

        if direct_sql and is_query_scope_question(request.message):
            message = render_query_scope(snapshot.authorization)
            yield event("conversation", {"conversation_id": conversation_id})
            yield event("text", {"delta": message})
            outcome = {"status": "success", "reason": message}
            message_id = conversations.add_message(CONVERSATIONS, conversation_id, "assistant", message, outcome=outcome)
            yield event("message", {"id": message_id})
            yield event("done", {"status": "completed", "outcome": outcome})
            return

        if is_previous_period and not context:
            message = f"请先提供需要对比的{CATALOG['name']}查询，再询问去年同期。"
            yield event("conversation", {"conversation_id": conversation_id})
            yield event("text", {"delta": message})
            outcome = {"status": "clarification_required", "reason": message}
            message_id = conversations.add_message(CONVERSATIONS, conversation_id, "assistant", message, outcome=outcome)
            yield event("message", {"id": message_id})
            yield event("done", {"status": "completed", "outcome": outcome})
            return

        @function_tool
        def get_semantic_catalog() -> str:
            """Get allowed sales metrics and dimensions."""
            return json.dumps(question_catalog(ROOT, CATALOG["id"], request.message), ensure_ascii=False)

        @function_tool
        def resolve_business_terms(question: str) -> str:
            """Find sales terms and dimensions relevant to a user question."""
            catalog = question_catalog(ROOT, CATALOG["id"], question)
            matches = {}
            for category in ("metrics", "dimensions"):
                for identifier, item in catalog[category].items():
                    terms = [identifier, item["name"], *item.get("synonyms", [])]
                    if any(term.lower() in question.lower() for term in terms):
                        matches[identifier] = item["name"]
            return json.dumps(matches, ensure_ascii=False)

        @function_tool
        def execute_semantic_query(plan: Query) -> str:
            """Execute a validated structured query against the sales space."""
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            if is_previous_period and context:
                plan = Query.model_validate(context)
                previous_range = plan.time_range or TimeRange(start=today.replace(month=1, day=1), end=today)
                plan = plan.model_copy(update={"time_range": TimeRange(start=last_year(previous_range.start), end=last_year(previous_range.end))})
            elif plan.time_range is None:
                plan = plan.model_copy(update={"time_range": TimeRange(start=today.replace(month=1, day=1), end=today)})
            plan = normalize_query(plan, CATALOG)
            data = execute_query(DATABASE, plan, today, CATALOG, source=source)
            if source.engine == "sqlite" and EXTERNAL_SQLITE_PATH:
                data['source'] = 'SQLite ADS'
            result_data.append(data)
            query_data.append(plan.model_dump(mode="json"))
            return json.dumps(data, ensure_ascii=False)

        attempts = 0
        coverage_attempts = 0
        query_error = None
        query_lock = asyncio.Lock()
        request_catalog = CATALOG
        join_keys = set()
        presentation = None

        @function_tool(failure_error_function=None)
        async def execute_direct_sql(sql: str, clarification: str = "", options: list[str] | None = None) -> str:
            """Execute one DuckDB SELECT submitted as SQL, with no surrounding Markdown."""
            nonlocal attempts, coverage_attempts, query_error, presentation, terminal
            async with query_lock:
                if clarification:
                    if not result_data:
                        terminal = {"status": "clarification_required", "reason": clarification}
                        conversations.set_pending(CONVERSATIONS, conversation_id, {
                            "question": effective_question, "kind": "model", "resolved": sorted(resolved),
                            "reason": clarification, "options": [option.strip() for option in options or [] if option.strip()],
                        })
                    return json.dumps(terminal or {"status": "success"}, ensure_ascii=False)
                if terminal:
                    return json.dumps(terminal, ensure_ascii=False)
                if result_data:
                    return json.dumps(result_data[-1], ensure_ascii=False)
                if query_error or attempts >= 3:
                    raise SqlRejected("查询已结束")
                candidate = previous_sql if is_previous_period and previous_sql else sql
                # 补充只发生在大空间的检索遗漏。校验仍用完整授权，未授权表直接拒绝。
                if snapshot is not None and presentation is not None and not is_previous_period:
                    gap = context_coverage_gap(candidate, snapshot.authorization, presentation)
                    if gap:
                        if coverage_attempts >= MAX_CONTEXT_COVERAGE:
                            query_error = "已授权上下文补充次数已用尽"
                            terminal = {"status": "validator_error", "reason": query_error}
                            raise SqlRejected(query_error)
                        coverage_attempts += 1
                        presentation = include_datasets(
                            snapshot.authorization, presentation, gap, snapshot.object_datasets,
                        )
                        return json.dumps({
                            "error": "这些数据集已在空间授权内，但没有进入本次上下文。请根据补充定义重新生成 SQL。",
                            "added_datasets": gap,
                            "context": expansion_payload(snapshot.authorization, gap),
                            "remaining_coverage": MAX_CONTEXT_COVERAGE - coverage_attempts,
                        }, ensure_ascii=False)
                attempts += 1
                try:
                    data = await asyncio.to_thread(
                        execute_order_sql, Path(source.location), candidate, request_catalog, business_today, join_keys,
                    )
                except SqlCorrection as exc:
                    logger.error("Direct SQL needs repair (%s): %s", exc, candidate)
                    if attempts < 3 and not is_previous_period:
                        return json.dumps({"error": str(exc), "remaining_repairs": 3 - attempts}, ensure_ascii=False)
                    query_error = "SQL 修复后仍无法执行"
                    terminal = {"status": "validator_error", "reason": query_error}
                    raise
                except SqlRejected as exc:
                    logger.error("Direct SQL rejected (%s): %s", exc, candidate)
                    query_error = str(exc)
                    terminal = {"status": "refused", "reason": query_error}
                    raise
                result_data.append(data)
                query_data.append({"mode": "orders_duckdb", "sql": data["sql"]})
                return json.dumps(data, ensure_ascii=False)

        yield event("conversation", {"conversation_id": conversation_id})
        yield event("status", {"status": "running"})
        try:
            prompt = f"当前业务日期：{business_today.isoformat()}（Asia/Shanghai）。\n用户的问题：{effective_question}"
            tools = [get_semantic_catalog, resolve_business_terms, execute_semantic_query]
            instructions = space_instructions(CATALOG)
            run_options = {}
            if direct_sql:
                database = await asyncio.to_thread(
                    order_database_context, Path(source.location), authorized_tables(ROOT, snapshot.authorization),
                )
                snapshot = snapshot.with_schema(database)
                request_catalog, join_keys = validation_inputs(snapshot)
                if isinstance(saved_sql, str) and context:
                    previous_sql = prior_order_sql(
                        saved_sql, request_catalog, business_today,
                        shift=last_year if is_previous_period else None, joins=join_keys,
                    )
                if is_previous_period and previous_sql:
                    # 服务已根据最近成功查询确定同期 SQL；不能再由模型决定是否执行。
                    data = await asyncio.to_thread(
                        execute_order_sql, Path(source.location), previous_sql, request_catalog, business_today, join_keys,
                    )
                    dates = list(dict.fromkeys(re.findall(r"\d{4}-\d{2}-\d{2}", data["sql"])))
                    period = " 至 ".join(dates)
                    message = (f"去年同期（{period}）" if period else "去年同期") + (
                        "查询完成。" if data["rows"] else "没有可对比的订单。"
                    )
                    yield event("text", {"delta": message})
                    yield event("result", data)
                    outcome = {"status": "success", "reason": "查询完成"}
                    message_id = conversations.add_message(
                        CONVERSATIONS, conversation_id, "assistant", message, data,
                        {"mode": "orders_duckdb", "sql": data["sql"]}, outcome=outcome,
                    )
                    saved = True
                    yield event("message", {"id": message_id})
                    yield event("done", {"status": "completed", "outcome": outcome})
                    return
                if is_small_space(snapshot.authorization):
                    presentation = snapshot.authorization
                else:
                    presentation = expand_authorization(
                        snapshot.authorization, effective_question, snapshot.object_datasets,
                    )
                instructions = (
                    "你是订单主题空间助手。用 execute_direct_sql 提交 DuckDB SQL。"
                    "只使用本次授权的表和列，以及授权指标的表达式和结果别名。"
                    "经典示例只说明方言写法，示例中的表不代表已经授权。"
                    "用户说出时间时写入对应日期条件。用户没有说出时间或状态时查询全部日期或状态。"
                    "问题条件和口径完整明确时立即执行，不要再次确认已明确的条件；不要要求补充可选的筛选、分组、输出列、排序或行数限制。"
                    "未写 LIMIT 时使用默认行数上限。"
                    "有 SQL 修复反馈时按反馈调整，权限拒绝时结束。依据返回结果用中文解释。"
                    "需要用户澄清时，不要执行查询，调用 execute_direct_sql，sql 为空字符串，clarification 填写澄清说明，options 填写可供用户选择的选项文本列表。"
                    "去年同期的日期已经由服务算好，写在本次提示开头。解释时原样复述，不要再减一年，也不要改成当前业务日期。结果为空时说明该区间没有可对比的订单，不要把空结果说成查询失败。"
                    "针对去年同期，调用工具提交服务提供的同期 SQL，不要自行改写日期。"
                    "解释里的日期必须与本次执行 SQL 中的日期字面量一致。空结果也写出 SQL 里的起止日期，不能改用当前业务日期，也不能写成当前业务日期的上一年同一天。"
                    "用户询问可查询范围时，只回复提示中的可查询范围原文，不要调用工具。"
                )
                # 订单 Direct SQL 只保留执行器。目录工具会按问题裁剪授权，和本次提示不是同一份范围。
                tools = [execute_direct_sql]
                if presentation is snapshot.authorization:
                    prompt += "\n" + render_direct_sql_prompt(
                        ROOT, database, snapshot.authorization, snapshot.dialect,
                    )
                else:
                    prompt += "\n" + render_two_phase_prompt(
                        ROOT,
                        database,
                        presentation,
                        [item.model_dump() for item in short_catalog(snapshot.authorization)],
                        snapshot.dialect,
                    )
                if previous_sql:
                    prompt += f"\n{'已确定的同期 SQL' if is_previous_period else '最近一次成功的查询 SQL'}：{previous_sql}"
                    if is_previous_period:
                        executed_dates = list(dict.fromkeys(re.findall(r"\d{4}-\d{2}-\d{2}", previous_sql)))
                        if executed_dates:
                            prompt = (
                                f"解释只能复述这些已执行日期：{' 至 '.join(executed_dates)}。禁止使用当前业务日期。\n"
                                + prompt
                            )
                run_options["max_turns"] = 8
            elif context:
                prompt = f"当前业务日期：{business_today.isoformat()}（Asia/Shanghai）。\n最近一次成功查询的结构化条件：{json.dumps(context, ensure_ascii=False)}\n用户的新问题：{request.message}"
            agent = Agent(
                name=f"{CATALOG['name']}助手", model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                instructions=instructions, tools=tools,
            )
            run = Runner.run_streamed(
                agent, prompt,
                run_config=RunConfig(trace_include_sensitive_data=False, tracing_disabled=not bool(os.getenv("OPENAI_TRACING_API_KEY"))),
                **run_options,
            )
            async for item in run.stream_events():
                if item.type == "raw_response_event" and item.data.type == "response.output_text.delta":
                    answer.append(item.data.delta)
                    yield event("text", {"delta": item.data.delta})
            if terminal:
                raise SqlRejected(terminal["reason"])
            if direct_sql and not result_data:
                if attempts:
                    raise SqlCorrection("查询未返回已验证的结果")
                terminal = {"status": "invalid_output", "reason": "未返回查询结果或澄清说明"}
                raise ValueError(terminal["reason"])
            if result_data:
                yield event("result", result_data[-1])
            outcome = {"status": "success", "reason": "查询完成"}
            message_id = conversations.add_message(
                CONVERSATIONS, conversation_id, "assistant", "".join(answer),
                result_data[-1] if result_data else None, query_data[-1] if query_data else None, outcome=outcome,
            )
            saved = True
            yield event("message", {"id": message_id})
            yield event("done", {"status": "completed", "outcome": outcome})
        except asyncio.CancelledError:
            if not saved:
                outcome = {"status": "model_error", "reason": "响应流中断"}
                conversations.add_message(CONVERSATIONS, conversation_id, "assistant", "".join(answer), outcome=outcome)
            raise
        except Exception as exc:
            logger.error("Agent request failed (%s)", type(exc).__name__)
            if terminal:
                outcome = terminal
            elif isinstance(exc, SqlRejected):
                outcome = {"status": "refused", "reason": str(exc)}
            elif isinstance(exc, SqlTimeout):
                outcome = {"status": "timeout", "reason": str(exc)}
            elif isinstance(exc, SqlCorrection):
                outcome = {"status": "validator_error", "reason": str(exc)}
            elif isinstance(exc, duckdb.Error):
                outcome = {"status": "database_error", "reason": "数据库查询失败"}
            else:
                outcome = {"status": "model_error", "reason": "查询失败，请检查模型连接和服务配置"}
            message_id = conversations.add_message(CONVERSATIONS, conversation_id, "assistant", "".join(answer), outcome=outcome)
            yield event("message", {"id": message_id})
            if outcome["status"] != "clarification_required":
                code = "query_timeout" if outcome["status"] == "timeout" else "query_failed"
                yield event("error", {"message": outcome["reason"], "code": code})
            yield event("done", {"status": "completed" if outcome["status"] == "clarification_required" else "failed", "outcome": outcome})

    async def guarded_stream():
        try:
            async for item in stream():
                yield item
        finally:
            lock.close()

    return StreamingResponse(guarded_stream(), media_type="text/event-stream")
