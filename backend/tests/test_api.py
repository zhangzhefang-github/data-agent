from fastapi.testclient import TestClient

from app.main import app


def test_feedback_is_saved_for_assistant_messages(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    conversation_id = main.conversations.create(main.CONVERSATIONS, "销售")
    message_id = main.conversations.add_message(main.CONVERSATIONS, conversation_id, "assistant", "结果")
    user_id = main.conversations.add_message(main.CONVERSATIONS, conversation_id, "user", "问题")
    with TestClient(app) as client:
        assert client.post("/api/feedback", json={"message_id": message_id, "rating": "correct"}).status_code == 200
        messages = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
        assert messages[0]["feedback"] == "correct"
        assert client.post("/api/feedback", json={"message_id": message_id, "rating": "incorrect"}).status_code == 200
        assert client.get(f"/api/conversations/{conversation_id}").json()["messages"][0]["feedback"] == "incorrect"
        assert client.post("/api/feedback", json={"message_id": "missing", "rating": "correct"}).status_code == 404
        assert client.post("/api/feedback", json={"message_id": user_id, "rating": "correct"}).status_code == 404
        assert client.post("/api/feedback", json={"message_id": message_id, "rating": "maybe"}).status_code == 422


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


def test_agent_receives_current_business_date(monkeypatch, tmp_path):
    from datetime import datetime
    from types import SimpleNamespace
    from app import main

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 1, 12, tzinfo=tz)

    class Reply:
        async def stream_events(self):
            yield SimpleNamespace(type='raw_response_event', data=SimpleNamespace(type='response.output_text.delta', delta='完成'))

    prompts = []
    def run(agent, prompt, **kwargs):
        prompts.append(prompt)
        return Reply()

    monkeypatch.setattr(main, 'datetime', FixedDatetime)
    monkeypatch.setattr(main, 'CONVERSATIONS', tmp_path / 'conversations.db')
    monkeypatch.setattr(main.Runner, 'run_streamed', run)
    with TestClient(app) as client:
        client.post('/api/chat/stream', json={'message': '查询今年销售额'})
    assert '2026-06-01' in prompts[0]
    assert 'Asia/Shanghai' in prompts[0]


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
        response = client.post("/api/chat/stream", json={"message": "那去年同期的销售额呢？", "conversation_id": conversation_id})
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


def test_same_conversation_lock_is_shared_between_requests(monkeypatch, tmp_path):
    import fcntl
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    conversation_id = main.conversations.create(main.CONVERSATIONS, "销售")
    lock_dir = tmp_path / ".conversation-locks"
    lock_dir.mkdir()
    with (lock_dir / conversation_id).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with TestClient(app) as client:
            response = client.post("/api/chat/stream", json={"message": "销售额", "conversation_id": conversation_id})
            assert response.status_code == 409
    assert main.conversations.get(main.CONVERSATIONS, conversation_id)["messages"] == []


def test_terms_use_catalog_synonyms(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from agents.tool_context import ToolContext
    from app import main

    monkeypatch.setattr(main, "CONVERSATIONS", tmp_path / "conversations.db")
    matches = []

    class Reply:
        def __init__(self, agent):
            self.agent = agent

        async def stream_events(self):
            tool = next(tool for tool in self.agent.tools if tool.name == "resolve_business_terms")
            result = await tool.on_invoke_tool(ToolContext(None, tool_name=tool.name, tool_call_id="1", tool_arguments="{}"), json.dumps({"question": "各地区净销售额"}))
            matches.append(json.loads(result))
            yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.output_text.delta", delta="完成"))

    monkeypatch.setattr(main.Runner, "run_streamed", lambda agent, *args, **kwargs: Reply(agent))
    with TestClient(app) as client:
        client.post("/api/chat/stream", json={"message": "各地区净销售额"})
    assert matches == [{"sales_amount": "销售额", "city": "城市"}]
