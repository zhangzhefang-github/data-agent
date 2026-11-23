# 观测选型：Langfuse

2026-11-22，对照 `openai-agents==0.25.0` 的官方 tracing，以及 LangSmith、Langfuse 的官方集成，判断这个仓库接 Langfuse 更合适。用户尚未把这条判断收成 ADR。依据是 [调研笔记](../research/langsmith-vs-langfuse.md)。

## 2026-11-22 的判断

可以接，不必换成 LangChain 或 LangGraph。两家都有面向 OpenAI Agents SDK 的官方集成。

更合适的是 Langfuse 自托。这个进程是本机 uvicorn，仓库在 `data-agent@53a930d` 没有 Kubernetes 部署。Langfuse 核心是 MIT，Docker Compose 即可，trace 落在自己的 PostgreSQL 和 ClickHouse。LangSmith 的 Agents processor 挂载更短，但自托文档写成 Enterprise 附加项，要许可证和 Kubernetes。订单 SQL 和客户标识会进入模型上下文；一旦为了排错打开 span 里的输入输出，自托门槛决定数据留在哪。

少改代码、并且只把已裁剪的 span 发到云上时，LangSmith 的 `TracingProcessor` 更贴。那不是本仓库的约束。

同日晚些时候确认：`.env` 只有 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`，没有 `OPENAI_TRACING_API_KEY`。模型走兼容中转站。Agents SDK 的追踪导出不跟随 `OPENAI_BASE_URL`，固定发往 `https://api.openai.com/v1/traces/ingest`。中转站密钥不能用于这条导出。云端 LangSmith 与云端 Langfuse 都由本进程直接投递，不经过中转站，也不经过 OpenAI 追踪接口。`RunConfig.tracing_disabled` 目前绑在 `OPENAI_TRACING_API_KEY` 上；该变量未设置时整段 trace 不创建，只注册云端 processor 也收不到 span。若接受 SQL 和结果行存到对方云区域，自托不再构成选择 Langfuse 的理由；云端 LangSmith 的挂载更短。

同日检索公开材料后的边界：没有覆盖中国大陆企业的普查能支持「数据智能体大多数选 LangSmith 或 Langfuse」。问数产品的公开案例更多是云厂商或行业平台里的智能问数。自建且要求数据留在内网时，中文技术文章反复写的是自托 Langfuse；已经在阿里云上的材料写的是 ARMS，并指出 Dify 里的 Langfuse 与 LangSmith 看不到完整业务链路。这两条都是文章里的建议，不是市场份额。

同日接着判断：不要为了上传改用 LangGraph。LangSmith 和 Langfuse 都已经有 OpenAI Agents SDK 的官方挂载，挂载点是进程启动时的 processor，不是编排框架。`data-agent@53a930d` 的对话是一次 `Runner.run_streamed` 加函数工具；金标真实模型也打这条路径。换成 LangGraph 会重写这条循环、SSE 和评测入口。并排看 SQL 要打开 `trace_include_sensitive_data`，并把 span 只送到自托的 Langfuse。LangGraph 适合以后需要显式图状态、检查点或人工中断时再评估。

## 接入时不要照抄快速开始

2026-11-22 的官方快速开始都会换掉 Agents SDK 的默认 processor，从而停掉发往 `https://api.openai.com/v1/traces/ingest` 的上传。

- LangSmith 示例使用 `set_trace_processors`。要与 OpenAI 上传并存，应改成 `add_trace_processor`。
- Langfuse 集成页示例是无参 `OpenAIAgentsInstrumentor().instrument()`。源码里 `exclusive_processor=True` 会替换已有 processor；并存时要传 `exclusive_processor=False`。
- 集成页示例里的 `nest_asyncio.apply()` 与 FastAPI 常用的 uvloop 不兼容。仪器本身不需要这一句。

在 `data-agent@53a930d`，`backend/app/main.py` 用应用自己的 `OPENAI_TRACING_API_KEY` 调用 `set_tracing_export_api_key`。Agents SDK 0.25.0 不读取这个变量名，它读取的是 `OPENAI_API_KEY`、`set_tracing_export_api_key()` 或 `RunConfig.tracing.api_key`。同一处 `RunConfig(trace_include_sensitive_data=False)` 在 span 写入前去掉模型和工具的输入输出，token 用量仍会留下。保持这个开关时，两家都看不到 SQL 和工具返回值。

挂载点是进程启动处，紧挨 `set_tracing_export_api_key`。对话和金标的真实模型路径都走 `POST /api/chat/stream` 的 `Runner.run_streamed`。两家集成页都没有 `run_streamed` 的专门示例；覆盖是从 SDK 对 `run_streamed` 同样创建 trace 推出来的。

## 尚未确认

- 两家都没有把 `openai-agents==0.25.0` 写成已测版本，只确认它高于各自的最低版本。
- 未确认 `langsmith>=0.14.9` 在不设置 `LANGSMITH_AGENT_ID` 时是否仍接受 processor 写入。
- 未确认 OpenInference 的 token 属性如何逐字段变成 Langfuse 的 usage。
- 金标 JSON 没有专用上传格式。两边都是由调用方写成数据集条目再跑实验。
