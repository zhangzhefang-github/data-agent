from fastapi.testclient import TestClient

from app.main import app


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
