"""Agents SDK 的 span 只在配置了 LangSmith 时创建，并且不采集敏感字段。"""

import os
import threading

from agents import RunConfig, set_trace_processors

_install_lock = threading.Lock()
_langsmith_installed = False


def agent_run_config() -> RunConfig:
    if os.getenv('LANGSMITH_API_KEY') or os.getenv('LANGCHAIN_API_KEY'):
        _install_langsmith_processor()
        return RunConfig(trace_include_sensitive_data=False, tracing_disabled=False)
    return RunConfig(trace_include_sensitive_data=False, tracing_disabled=True)


def _install_langsmith_processor() -> None:
    global _langsmith_installed
    if _langsmith_installed:
        return
    with _install_lock:
        if _langsmith_installed:
            return
        from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor

        # 替换默认列表，避免 span 再发往 api.openai.com/v1/traces/ingest。
        set_trace_processors([OpenAIAgentsTracingProcessor()])
        _langsmith_installed = True
