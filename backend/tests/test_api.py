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
