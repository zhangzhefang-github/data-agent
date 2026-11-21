import asyncio
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
from app.direct_sql import SqlCorrection, SqlRejected, SqlTimeout, execute_order_sql, order_database_context, prior_order_sql
from app.sql_prompt import authorized_tables, render_direct_sql_prompt
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
        context = conversation["last_query"] if conversation else None
        is_previous_period = bool(re.search(r"去年同期", request.message))
        source = locate_source(DATABASE, DATABASE_URL, DUCKDB_PATH)
        direct_sql = CATALOG["id"] == "orders" and source.engine == "duckdb"
        business_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        previous_sql = None
        if direct_sql:
            if context and context.get("mode") == "orders_duckdb" and isinstance(context.get("sql"), str):
                try:
                    previous_sql = prior_order_sql(
                        context["sql"], CATALOG, business_today, shift=last_year if is_previous_period else None,
                    )
                except ValueError:
                    context = None
            else:
                context = None
        elif context and context.get("mode") == "orders_duckdb":
            context = None

        if is_previous_period and not context:
            message = f"请先提供需要对比的{CATALOG['name']}查询，再询问去年同期。"
            yield event("conversation", {"conversation_id": conversation_id})
            yield event("text", {"delta": message})
            message_id = conversations.add_message(CONVERSATIONS, conversation_id, "assistant", message)
            yield event("message", {"id": message_id})
            yield event("done", {"status": "completed"})
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
        query_error = None
        query_lock = asyncio.Lock()

        @function_tool(failure_error_function=None)
        async def execute_direct_sql(sql: str) -> str:
            """Execute one DuckDB SELECT submitted as SQL, with no surrounding Markdown."""
            nonlocal attempts, query_error
            async with query_lock:
                if result_data:
                    return json.dumps(result_data[-1], ensure_ascii=False)
                if query_error or attempts >= 3:
                    raise SqlRejected("查询已结束")
                attempts += 1
                candidate = previous_sql if is_previous_period and previous_sql else sql
                try:
                    data = await asyncio.to_thread(
                        execute_order_sql, Path(source.location), candidate, CATALOG, business_today,
                    )
                except SqlCorrection as exc:
                    if attempts < 3 and not is_previous_period:
                        return json.dumps({"error": str(exc), "remaining_repairs": 3 - attempts}, ensure_ascii=False)
                    query_error = "SQL 修复后仍无法执行"
                    raise
                except SqlRejected as exc:
                    query_error = str(exc)
                    raise
                result_data.append(data)
                query_data.append({"mode": "orders_duckdb", "sql": data["sql"]})
                return json.dumps(data, ensure_ascii=False)

        yield event("conversation", {"conversation_id": conversation_id})
        yield event("status", {"status": "running"})
        try:
            prompt = f"当前业务日期：{business_today.isoformat()}（Asia/Shanghai）。\n用户的问题：{request.message}"
            tools = [get_semantic_catalog, resolve_business_terms, execute_semantic_query]
            instructions = space_instructions(CATALOG)
            run_options = {}
            if direct_sql:
                database = await asyncio.to_thread(
                    order_database_context, Path(source.location), authorized_tables(ROOT),
                )
                instructions = (
                    "你是订单主题空间助手。用 execute_direct_sql 提交 DuckDB SQL。"
                    "只使用本次授权的表和列，以及授权指标的表达式和结果别名。"
                    "经典示例只说明方言写法，示例中的表不代表已经授权。"
                    "用户说出时间时写入对应日期条件。用户没有说出时间时不要添加日期条件。"
                    "未写 LIMIT 时使用默认行数上限。"
                    "有 SQL 修复反馈时按反馈调整，权限拒绝时结束。依据返回结果用中文解释，日期以结果 SQL 为准。"
                    "针对去年同期，调用工具提交服务提供的同期 SQL。"
                )
                tools = [get_semantic_catalog, resolve_business_terms, execute_direct_sql]
                prompt += "\n" + render_direct_sql_prompt(ROOT, database)
                if previous_sql:
                    prompt += f"\n{'已确定的同期 SQL' if is_previous_period else '最近一次成功的查询 SQL'}：{previous_sql}"
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
            if direct_sql and not result_data and attempts:
                raise SqlCorrection("查询未返回已验证的结果")
            if result_data:
                yield event("result", result_data[-1])
            message_id = conversations.add_message(
                CONVERSATIONS, conversation_id, "assistant", "".join(answer),
                result_data[-1] if result_data else None, query_data[-1] if query_data else None,
            )
            yield event("message", {"id": message_id})
            yield event("done", {"status": "completed"})
        except asyncio.CancelledError:
            raise
        except SqlTimeout as exc:
            yield event("error", {"message": str(exc), "code": "query_timeout"})
            yield event("done", {"status": "failed"})
        except Exception as exc:
            logger.error("Agent request failed (%s)", type(exc).__name__)
            yield event("error", {"message": query_error or "查询失败，请检查模型连接和服务配置", "code": "query_failed"})
            yield event("done", {"status": "failed"})

    async def guarded_stream():
        try:
            async for item in stream():
                yield item
        finally:
            lock.close()

    return StreamingResponse(guarded_stream(), media_type="text/event-stream")
