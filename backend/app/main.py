import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_BASE_URL", "https://api.agicto.cn/v1")

import yaml
from agents import Agent, Runner, function_tool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.demo import initialize
from app.query import Query, execute_query

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "demo" / "sales.db"
CATALOG = yaml.safe_load((ROOT / "backend" / "configs" / "sales_space.yaml").read_text())["space"]
app = FastAPI()
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


def event(kind: str, payload: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
async def chat(request: ChatRequest):
    async def stream():
        result_data = []

        @function_tool
        def get_semantic_catalog() -> str:
            """Get allowed sales metrics and dimensions."""
            return json.dumps(CATALOG, ensure_ascii=False)

        @function_tool
        def resolve_business_terms(question: str) -> str:
            """Find sales terms and dimensions relevant to a user question."""
            return json.dumps({"sales_amount": "销售额", "city": "城市"}, ensure_ascii=False)

        @function_tool
        def execute_semantic_query(plan: Query) -> str:
            """Execute a validated structured query against the sales space."""
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            data = execute_query(DATABASE, plan, today)
            result_data.append(data)
            return json.dumps(data, ensure_ascii=False)

        agent = Agent(
            name="销售分析助手",
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            instructions=(
                "你是销售分析助手。只能回答销售分析问题。"
                "先读取目录，再通过结构化工具查询，禁止回答未经查询的数值。"
                "当前只支持按城市汇总销售额，今年截至今天。"
                "遇到写入、未授权表、未知指标或其他不支持的请求请拒绝并说明。"
                "按查询结果用中文简短回答，若无行则说明无数据。"
            ),
            tools=[get_semantic_catalog, resolve_business_terms, execute_semantic_query],
        )
        yield event("status", {"status": "running"})
        try:
            run = Runner.run_streamed(agent, request.message)
            async for item in run.stream_events():
                if item.type == "raw_response_event" and item.data.type == "response.output_text.delta":
                    yield event("text", {"delta": item.data.delta})
            if result_data:
                yield event("result", result_data[-1])
            yield event("done", {"status": "completed"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Agent request failed (%s)", type(exc).__name__)
            yield event("error", {"message": "查询失败，请检查模型连接和服务配置", "code": "query_failed"})
            yield event("done", {"status": "failed"})

    return StreamingResponse(stream(), media_type="text/event-stream")
