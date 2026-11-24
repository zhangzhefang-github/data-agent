from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _clear_tracing_keys(monkeypatch):
    for name in ('LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY', 'OPENAI_TRACING_API_KEY'):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize('key_name', ['LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY'])
def test_agent_run_sends_spans_only_to_langsmith(monkeypatch, key_name):
    from agents.tracing import get_trace_provider

    from app.tracing import agent_run_config

    _clear_tracing_keys(monkeypatch)
    monkeypatch.setenv(key_name, 'lsv2_tr_test_key_not_used')
    monkeypatch.setenv('OPENAI_TRACING_API_KEY', 'sk-official-must-not-export')
    config = agent_run_config()
    assert config.tracing_disabled is False
    assert config.trace_include_sensitive_data is False
    processors = get_trace_provider()._multi_processor._processors
    assert [type(processor).__name__ for processor in processors] == ['OpenAIAgentsTracingProcessor']


def test_agent_run_stays_quiet_without_langsmith_key(monkeypatch):
    from app.tracing import agent_run_config

    _clear_tracing_keys(monkeypatch)
    monkeypatch.setenv('OPENAI_TRACING_API_KEY', 'sk-official-must-not-export')
    config = agent_run_config()
    assert config.tracing_disabled is True
    assert config.trace_include_sensitive_data is False


def test_analysis_agent_uses_shared_trace_config(monkeypatch):
    _clear_tracing_keys(monkeypatch)
    monkeypatch.setenv('OPENAI_TRACING_API_KEY', 'sk-official-must-not-export')
    from app.analysis.planner import AnalysisAgent

    config = AnalysisAgent().run_config
    assert config.tracing_disabled is True
    assert config.trace_include_sensitive_data is False


def test_chat_stream_uses_shared_trace_config(monkeypatch, tmp_path):
    _clear_tracing_keys(monkeypatch)
    monkeypatch.setenv('OPENAI_TRACING_API_KEY', 'sk-official-must-not-export')
    from app import main

    captured = {}

    class Reply:
        async def stream_events(self):
            yield SimpleNamespace(
                type='raw_response_event',
                data=SimpleNamespace(type='response.output_text.delta', delta='好的'),
            )

    def run_streamed(*args, **kwargs):
        captured['config'] = kwargs['run_config']
        return Reply()

    monkeypatch.setattr(main.Runner, 'run_streamed', run_streamed)
    monkeypatch.setattr(main, 'CONVERSATIONS', tmp_path / 'conversations.db')
    with TestClient(main.app) as client:
        response = client.post('/api/chat/stream', json={'message': '你好'})
    assert response.status_code == 200
    config = captured['config']
    assert config.tracing_disabled is True
    assert config.trace_include_sensitive_data is False
