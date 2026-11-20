from fastapi.testclient import TestClient

from app.main import app


def test_conversation_is_created_and_reopened(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")

    class Reply:
        async def stream_events(self):
            from types import SimpleNamespace
            yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.output_text.delta", delta="请明确问题"))

    monkeypatch.setattr(main.Runner, "run_streamed", lambda *args, **kwargs: Reply())
    import json
    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "你好"})
        parts = response.text.split("\n\n")
        session_event = next(part for part in parts if part.startswith("event: conversation"))
        conversation_id = json.loads(session_event.split("data: ", 1)[1])["conversation_id"]
        detail = client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        assert [item["content"] for item in messages] == ["你好", "请明确问题"]
        assert messages[1]["id"]
        assert client.get("/api/conversations").json()[0]["id"] == conversation_id


def test_followup_requires_previous_query_and_is_isolated(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    with TestClient(app) as client:
        first = client.post("/api/chat/stream", json={"message": "去年同期呢？"})
        assert "请先提供" in first.text
        assert "event: result" not in first.text


def test_followup_preserves_filter_and_shifts_leap_day(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from agents.tool_context import ToolContext
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    submitted = []

    def fake_execute(path, plan, today):
        submitted.append(plan.model_dump(mode="json"))
        return {"columns": ["sales_amount"], "rows": [[9000]], "sql": "SELECT", "source": "test", "elapsed_ms": 1}

    class Run:
        def __init__(self, agent):
            self.agent = agent

        async def stream_events(self):
            tool = next(tool for tool in self.agent.tools if tool.name == "execute_semantic_query")
            await tool.on_invoke_tool(ToolContext(None, tool_name="execute_semantic_query", tool_call_id="1", tool_arguments="{}"), json.dumps({"plan": {"metrics": ["sales_amount"], "dimensions": []}}))
            yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.output_text.delta", delta="9000元"))

    monkeypatch.setattr(main, "execute_query", fake_execute)
    monkeypatch.setattr(main.Runner, "run_streamed", lambda agent, *args, **kwargs: Run(agent))
    conversation_id = main.conversations.create(main.CONVERSATIONS, "北京")
    original = {"metrics": ["sales_amount"], "dimensions": [], "filters": [{"field": "city", "operator": "eq", "value": "北京"}], "time_range": {"start": "2024-01-01", "end": "2024-02-29"}, "order_by": None, "limit": 500}
    main.conversations.add_message(main.CONVERSATIONS, conversation_id, "assistant", "查询成功", query=original)
    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "去年同期呢？", "conversation_id": conversation_id})
        assert '"status": "completed"' in response.text
        assert submitted[0]["filters"] == original["filters"]
        assert submitted[0]["time_range"] == {"start": "2023-01-01", "end": "2023-02-28"}
        assert client.get(f"/api/conversations/{conversation_id}").json()["last_query"]["time_range"] == submitted[0]["time_range"]


def test_failed_run_keeps_previous_query_and_other_session_has_no_context(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    query = {"metrics": ["sales_amount"], "dimensions": [], "filters": [{"field": "city", "operator": "eq", "value": "北京"}], "time_range": {"start": "2024-01-01", "end": "2024-02-29"}, "order_by": None, "limit": 500}
    conversation_id = main.conversations.create(main.CONVERSATIONS, "北京")
    main.conversations.add_message(main.CONVERSATIONS, conversation_id, "assistant", "成功", query=query)

    class FailedRun:
        async def stream_events(self):
            raise RuntimeError("failed")
            yield

    monkeypatch.setattr(main.Runner, "run_streamed", lambda *args, **kwargs: FailedRun())
    with TestClient(app) as client:
        failed = client.post("/api/chat/stream", json={"message": "另一个问题", "conversation_id": conversation_id})
        assert '"status": "failed"' in failed.text
        assert client.get(f"/api/conversations/{conversation_id}").json()["last_query"] == query
        separate = client.post("/api/chat/stream", json={"message": "去年同期呢"})
        assert "请先提供" in separate.text


def test_health_and_catalog():
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/spaces").json()[0]["id"] == "sales"


def test_stream_reports_model_failure_without_exposing_exception(monkeypatch):
    from app import main

    class FailedRun:
        async def stream_events(self):
            raise RuntimeError("secret-value")
            yield

    monkeypatch.setattr(main.Runner, "run_streamed", lambda *args, **kwargs: FailedRun())
    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "查询今年各城市销售额"})
    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"status": "failed"' in response.text
    assert "secret-value" not in response.text
