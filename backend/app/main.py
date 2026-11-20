import asyncio
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_BASE_URL", "https://api.agicto.cn/v1")

import yaml
from agents import Agent, RunConfig, Runner, function_tool
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.demo import initialize
from app import conversations
from app.query import Query, TimeRange, execute_query

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "demo" / "sales.db"
CONVERSATIONS = ROOT / "data" / "conversations.db"
CATALOG = yaml.safe_load((ROOT / "backend" / "configs" / "sales_space.yaml").read_text())["space"]
app = FastAPI()
active_conversations: set[str] = set()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.on_event("startup")
def setup():
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
    if conversation_id in active_conversations:
        raise HTTPException(status_code=409, detail="该会话正在查询，请稍后再试")
    active_conversations.add(conversation_id)
    try:
        conversations.add_message(CONVERSATIONS, conversation_id, "user", request.message)
    except Exception:
        active_conversations.discard(conversation_id)
        raise

    async def stream():
        result_data = []
        query_data = []
        answer = []
        context = conversation["last_query"] if conversation else None
        is_previous_period = bool(re.fullmatch(r"\s*去年同期(?:呢|是多少|怎么样)?[？?。！!\s]*", request.message))

        if is_previous_period and not context:
            message = "请先提供需要对比的销售查询，再询问去年同期。"
            yield event("conversation", {"conversation_id": conversation_id})
            yield event("text", {"delta": message})
            message_id = conversations.add_message(CONVERSATIONS, conversation_id, "assistant", message)
            yield event("message", {"id": message_id})
            yield event("done", {"status": "completed"})
            return

        @function_tool
        def get_semantic_catalog() -> str:
            """Get allowed sales metrics and dimensions."""
            return json.dumps(CATALOG, ensure_ascii=False)

        @function_tool
        def resolve_business_terms(question: str) -> str:
            """Find sales terms and dimensions relevant to a user question."""
            return json.dumps({"sales_amount": "销售额", "order_count": "订单量", "average_order_value": "客单价", "city": "城市", "store": "门店", "product": "产品", "stat_date": "业务日期"}, ensure_ascii=False)

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
            data = execute_query(DATABASE, plan, today)
            result_data.append(data)
            query_data.append(plan.model_dump(mode="json"))
            return json.dumps(data, ensure_ascii=False)

        agent = Agent(
            name="销售分析助手",
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            instructions=(
                "你是销售分析助手。只能回答销售分析问题。"
                "先读取目录，再通过结构化工具查询，禁止回答未经查询的数值。"
                "支持销售额、订单量和客单价，按城市、门店、产品及业务日期分组或过滤。"
                "无指定时间时查询今年截至今天。门店排名按销售额降序排列并设置数量上限。"
                "追问去年同期时，使用最近一次成功查询的指标、维度和过滤条件，仅调整时间。"
                "遇到写入、未授权表、未知指标或其他不支持的请求请拒绝并说明。"
                "按查询结果用中文简短回答，若无行则说明无数据，销售额为零时明确显示为0元，不能说成缺失或无数据。"
            ),
            tools=[get_semantic_catalog, resolve_business_terms, execute_semantic_query],
        )
        yield event("conversation", {"conversation_id": conversation_id})
        yield event("status", {"status": "running"})
        try:
            prompt = request.message
            if context:
                prompt = f"最近一次成功查询的结构化条件：{json.dumps(context, ensure_ascii=False)}\n用户的新问题：{request.message}"
            run = Runner.run_streamed(agent, prompt, run_config=RunConfig(trace_include_sensitive_data=False))
            async for item in run.stream_events():
                if item.type == "raw_response_event" and item.data.type == "response.output_text.delta":
                    answer.append(item.data.delta)
                    yield event("text", {"delta": item.data.delta})
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
        except Exception as exc:
            logger.error("Agent request failed (%s)", type(exc).__name__)
            yield event("error", {"message": "查询失败，请检查模型连接和服务配置", "code": "query_failed"})
            yield event("done", {"status": "failed"})

    async def guarded_stream():
        try:
            async for item in stream():
                yield item
        finally:
            active_conversations.discard(conversation_id)

    return StreamingResponse(guarded_stream(), media_type="text/event-stream")
