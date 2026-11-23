# LangSmith 与 Langfuse：OpenAI Agents SDK 0.25.0

日期：2026-11-22

范围：Python 后端，保持 `openai-agents==0.25.0`，使用 `Runner.run_streamed` 与 `RunConfig(trace_include_sensitive_data=False)`，并已有 OpenAI 官方 tracing。对照的是 GitHub tag `v0.25.0`（发布于 2026-11-18），不是更早的 tracing API。证据只来自官方文档、官方 SDK 源码和官方 API 说明。

## 结论

两家都能在不换成 LangChain 或 LangGraph 的前提下接上 OpenAI Agents SDK。LangSmith 的路径是注册一个 `TracingProcessor`；Langfuse 的路径是 OpenInference 的 OpenTelemetry instrumentor，再由 Langfuse 客户端把 span 导出。两边文档里的默认挂法都会换掉 Agents SDK 已经注册的 processor，因此会停掉现有的 OpenAI 官方上传；要并存，都得改成追加。`trace_include_sensitive_data=False` 是在 span 写入之前生效的，模型和函数工具的输入输出不会进入任何 processor，token 用量仍会留下。云上两家都会收到“span 里实际有的字段”；这些字段若含 SQL 或业务数据，只有自托或事先脱敏才能让它们留在自己的网络里。少改代码更接近 LangSmith；希望观测、数据集和实验能在无企业许可证的情况下自托，更接近 Langfuse。这是差异，不是只能留一家。

## Langfuse

Langfuse 对 Python 版 OpenAI Agents SDK 的官方集成页，要求安装 `openai-agents`、`langfuse`、`nest_asyncio` 和 `openinference-instrumentation-openai-agents`，然后调用 `OpenAIAgentsInstrumentor().instrument()`，再用 `langfuse.get_client()`。页内写明这是第三方 OpenInference 仪器，把 Agents 操作导出为 OpenTelemetry span。[集成页](https://langfuse.com/integrations/frameworks/openai-agents)

集成页没有写 `openai-agents` 的最低版本。OpenInference 仪器包把被插桩依赖声明为 `openai-agents >= 0.11.0`。0.25.0 高于这个下限。该包没有在这份声明里把 0.25.0 列成已测版本。[package.py](https://github.com/Arize-ai/openinference/blob/9683d60dc2aaeb704607fb9b6c2efe2ba13d2698/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/package.py)

挂载不是 Langfuse 自己的装饰器。`instrument()` 默认 `exclusive_processor=True`：能读到已注册 processor 时，用替换列表的方式装上 `OpenInferenceTracingProcessor`；读不到时调用 `set_trace_processors([processor])`。传入 `exclusive_processor=False` 时才调用 `add_trace_processor`。Langfuse 集成页的示例没有这个参数，因此按文档照抄会走替换路径。[仪器源码](https://github.com/Arize-ai/openinference/blob/9683d60dc2aaeb704607fb9b6c2efe2ba13d2698/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/__init__.py)

`@observe()` 和 `start_as_current_observation()` 出现在同一页的“与 Python SDK 互操作”一节，用来给已经产生的 observation 加属性，不是 Agents 运行本身的挂载方式。[集成页](https://langfuse.com/integrations/frameworks/openai-agents)

集成页的示例只调用了 `Runner.run`，没有 `run_streamed`。仪器本身是 Agents SDK 的 `TracingProcessor`，收到的是 SDK 发出的 span，不区分是哪一个 Runner 入口。SDK 文档写明 `Runner.run`、`run_sync`、`run_streamed` 都会包进 `trace()`。因此 `run_streamed` 走同一条处理器，但 Langfuse 集成页没有单独的 streamed 示例。[集成页](https://langfuse.com/integrations/frameworks/openai-agents) [v0.25.0 tracing 文档](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md) [run_streamed 创建 trace](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/run.py)

工具调用：集成页用 `@function_tool` 的 `get_weather` 示例，并写明 trace 里能看到函数名和参数。参数是否出现，还取决于下面的敏感数据开关；开关为 False 时，SDK 不把参数写进 span。[集成页](https://langfuse.com/integrations/frameworks/openai-agents) [函数 span 写入条件](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/run_internal/tool_execution.py)

Token：OpenInference 把 Responses 与 Chat Completions 的 usage 写成 `llm.token_count.*` 属性，包括 input、output、total，以及 cache、reasoning 等明细（字段存在时）。[仪器属性映射](https://github.com/Arize-ai/openinference/blob/9683d60dc2aaeb704607fb9b6c2efe2ba13d2698/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py)

集成页写查看 trace 时可以看到 token usage。Langfuse 的成本文档写：usage 由集成或调用方提交；未直接提交 cost 时，用 generation 的 `model` 匹配模型价格来推算。Langfuse 不再根据输入输出文本估算 token。自托实例使用该版本自带的默认价格。[集成页](https://langfuse.com/integrations/frameworks/openai-agents) [Token 与成本](https://langfuse.com/docs/observability/features/token-and-cost-tracking)

评测：同一集成页的后续步骤包括打分、LLM-as-a-judge、数据集。官方评测示例对 Agents 使用同一套 OpenInference 仪器，并用 `dataset.run_experiment`。[集成页](https://langfuse.com/integrations/frameworks/openai-agents) [官方评测示例](https://langfuse.com/guides/cookbook/example_evaluating_openai_agents)

自托是一等部署，不是只读云的附属模式。核心代码在 `ee/` 目录之外是 MIT；`ee/` 下是另一份许可证。自托定价页写明开源版包含观测、评测、prompt、数据集等核心能力，可用 Docker Compose 或 Kubernetes（Helm），以及 AWS、Azure、GCP 模板。自托时 trace、observation、score 存在部署者自己的 ClickHouse，事务数据在 PostgreSQL，队列在 Redis 或 Valkey，原始事件在 S3 或兼容的 blob。[LICENSE](https://github.com/langfuse/langfuse/blob/main/LICENSE) [自托定价](https://langfuse.com/pricing-self-host) [自托架构](https://langfuse.com/self-hosting)

云不是必须的。若使用 Langfuse Cloud，数据进入所选区域：EU `https://cloud.langfuse.com`（AWS `eu-west-1`）、US `https://us.cloud.langfuse.com`（`us-west-2`）、日本 `https://jp.cloud.langfuse.com`（`ap-northeast-1`）、HIPAA `https://hipaa.cloud.langfuse.com`。区域之间账户和数据隔离。SDK 用 `LANGFUSE_BASE_URL` 选择区域。[数据区域](https://langfuse.com/security/data-regions)

集成页示例调用了 `nest_asyncio.apply()`。官方评测示例写明它与 FastAPI 常用的 `uvloop` 不兼容。仪器源码的 `instrument()` 本身不导入 `nest_asyncio`；这个调用属于笔记本示例，不是仪器构造函数的参数。[集成页](https://langfuse.com/integrations/frameworks/openai-agents) [评测示例中的 uvloop 说明](https://langfuse.com/guides/cookbook/example_evaluating_openai_agents)

## LangSmith

LangSmith 有单独的 OpenAI Agents SDK 集成页，标题覆盖 Python 和 JavaScript，不要求把应用改成 LangChain 或 LangGraph。Python 安装是 `pip install "langsmith[openai-agents]"`，文档写最低 `langsmith>=0.3.15`。快速开始是 `from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor`，然后 `set_trace_processors([OpenAIAgentsTracingProcessor()])`。文档同时写：把 trace 发到 agent 需要 `langsmith>=0.14.9`，并配置 `LANGSMITH_AGENT_ID` 与 `LANGSMITH_AGENT_ENVIRONMENT`。[集成页](https://docs.langchain.com/langsmith/trace-with-openai-agents-sdk)

SDK 的 extra 把依赖写成 `openai-agents>=0.0.3`。0.25.0 满足这个下限。extra 没有把 0.25.0 写成已测版本。[pyproject.toml](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/pyproject.toml)

`OpenAIAgentsTracingProcessor` 实现 `tracing.TracingProcessor`，在 `on_span_end` 里区分 `ResponseSpanData`、`GenerationSpanData` 和 `FunctionSpanData`，把 span 转成 LangSmith run。函数 span 写入 inputs 和 outputs；generation 与 response 在 span 带 usage 时调用 `_create_usage_metadata`。官方示例只演示了 `Runner.run`。覆盖 `run_streamed` 的依据同样是：处理器吃的是全部 span，而 SDK 对 `run_streamed` 也会 `create_trace_for_run`。LangSmith 集成页没有 streamed 示例。[处理器](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/langsmith/integrations/openai_agents_sdk/_openai_agents.py) [span 提取](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/langsmith/integrations/openai_agents_sdk/_openai_agent_utils.py) [集成页](https://docs.langchain.com/langsmith/trace-with-openai-agents-sdk)

`langsmith.wrappers.OpenAIAgentsTracingProcessor` 仍能导入，但 `wrappers/_openai_agents.py` 已标成废弃，转发到 `langsmith.integrations.openai_agents_sdk`。当前文档用的是新路径。[废弃模块](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/langsmith/wrappers/_openai_agents.py)

LangSmith 成本文档写：平台会为主要提供商记录 token 用量和费用，也可以提交自定义成本。Agents 处理器把 usage 放进 `usage_metadata`，这是费用计算的输入；集成页本身没有单独的“成本”小节。函数工具 span 的提取函数不读 usage。[成本文档](https://docs.langchain.com/langsmith/cost-tracking) [span 提取](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/langsmith/integrations/openai_agents_sdk/_openai_agent_utils.py)

LangChain 和 LangGraph 另有自己的自动追踪文档。那是另一条路径。Agents SDK 这条不经过它们。[Agents 集成页](https://docs.langchain.com/langsmith/trace-with-openai-agents-sdk)

OpenAI 自己的生态列表把 LangSmith 链到 `trace-openai`，把 Langfuse 链到上面的 Agents 集成页。`trace-openai` 不是本调研使用的 Agents processor 页。[v0.25.0 生态列表](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md)

自托存在，但是 Enterprise 附加项：文档写需要许可证密钥，在自己的 Kubernetes 里运行，trace 和运行数据留在该环境。基础安装包含观测、评测和 prompt。存储可以是集群内的 ClickHouse（0.17 起可选 SmithDB）、PostgreSQL、Redis 或 Valkey，以及 blob。这不是公开的 MIT 平台发行版。[自托说明](https://docs.langchain.com/langsmith/self-hosted)

`langsmith-sdk` 仓库的 LICENSE 是 MIT。那是客户端，不是上面的自托平台。[SDK LICENSE](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/LICENSE)

云区域有五套：GCP US `https://api.smith.langchain.com`，GCP EU `https://eu.api.smith.langchain.com`，GCP APAC `https://apac.api.smith.langchain.com`，AWS US，Azure US。trace 存在该区域的 ClickHouse Cloud 或 SmithDB。使用云就意味着 run 载荷发到对应的 LangChain 托管 API。[Cloud 区域与存储](https://docs.langchain.com/langsmith/cloud)

## OpenAI Agents tracing 扩展

tag `v0.25.0` 发布于 2026-11-18。下文的 API 都来自这个 tag。[Release](https://github.com/openai/openai-agents-python/releases/tag/v0.25.0)

默认启用 tracing。第一次取全局 provider 时，会 `register_processor(default_processor())`。默认 processor 是 `BatchTraceProcessor`，导出器是 `BackendSpanExporter`，端点为 `https://api.openai.com/v1/traces/ingest`。导出器用的密钥是构造参数、`set_tracing_export_api_key()`，或环境变量 `OPENAI_API_KEY`。单次运行可以用 `RunConfig(tracing={"api_key": ...})`。[setup.py](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/setup.py) [导出器](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/processors.py) [配置文档](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/config.md) [tracing 文档中的单次密钥](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md)

在 v0.25.0 仓库内检索不到 `OPENAI_TRACING_API_KEY`。官方 tracing 不读这个变量名。若现有进程只设置了该变量、没有 `OPENAI_API_KEY` 也没有 `set_tracing_export_api_key`，按这份源码，默认导出会跳过上传并记录 `OPENAI_API_KEY is not set`。[导出器读取的环境变量](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/processors.py)

`add_trace_processor()` 追加一个 processor，默认的 OpenAI 导出器还在。`set_trace_processors()` 替换整个列表；除非列表里仍有会发往 OpenAI 的 processor，否则不再上传到 OpenAI。文档把前者写成“额外目的地”，把后者写成“替换默认”。[tracing 文档](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md) [函数实现](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/__init__.py) [替换列表](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/provider.py)

`RunConfig.trace_include_sensitive_data` 默认 True。环境变量 `OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA` 可在导入配置前改默认值，`false`、`0`、`no`、`off` 为关闭。关闭时仍创建 span，但不写入敏感字段。文档点名 `generation_span()` 的模型输入输出和 `function_span()` 的工具输入输出。Responses 路径只在 `tracing.include_data()` 为真时写入 `response` 和 `input`，usage 在这之前就会写入。Chat Completions 的 generation span 同样只在 `include_data()` 时写 output，usage 仍写入。函数工具只在该标志为真时写 `span_data.input` 和 `output`。[RunConfig](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/run_config.py) [敏感数据文档](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md) [Responses 写入点](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/models/openai_responses.py) [Chat Completions usage](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/models/_trace.py) [工具输入输出](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/run_internal/tool_execution.py)

因此第三方 processor 看到的是已经按这个标志裁过的 span。标志为 False 时，LangSmith 和 Langfuse 都拿不到模型输入输出和函数工具的参数、返回值；token 计数仍然在 span 上。Agent span 的字段是名称、handoff、工具名和输出类型，不含指令正文。[AgentSpanData](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/span_data.py)

并存方式是 SDK 自己的：用 `add_trace_processor` 追加，不要用 `set_trace_processors` 把默认导出器换掉。文档还写明各 processor 独立，一个失败不会挡住其他 processor；`add_trace_processor` 不会卸下默认 OpenAI 导出器。若必须先脱敏再上传，文档要求把脱敏和发送放进同一个自有 exporter，并用 `set_trace_processors` 换掉默认列表。那是替换路径，不是和默认上传并存。[tracing 文档的 processor 与脱敏节](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md)

`run_streamed` 在后台任务开始前调用 `create_trace_for_run`。流式运行会进入同一套 processor。[run.py](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/run.py)

## 评测

Langfuse 的数据集是带 input 和 expected output 的条目。Python SDK 用 `create_dataset` 和 `create_dataset_item`；文档写 input 与 expected output 可以是任意 Python 对象。实验用 `dataset.run_experiment` 或 `langfuse.run_experiment`，task 是调用方自己的函数，evaluator 返回 `Evaluation(name=..., value=...)`。分数也可以用 `create_score` 单独写入。UI 另支持 CSV 导入条目。没有一份文档把“本地金标 JSON 文件”定义成平台专用格式；金标 JSON 需要调用方读入后写成 dataset item。[数据集](https://langfuse.com/docs/evaluation/experiments/datasets) [SDK 实验](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk) [分数](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk)

LangSmith 的离线评测同样是数据集加实验。`Client.create_dataset` 与 `create_examples` 接受任意 dict 作为 `inputs`、`outputs` 和 `metadata`。`evaluate()` 的对象是一个普通 Python 函数，示例使用 OpenAI 客户端，不要求 LangChain。evaluator 接收 `inputs`、`outputs` 和 `reference_outputs`，返回值成为 feedback score。文档把带参考输出的 example 定义为离线评测的对照。[数据集编程接口](https://docs.langchain.com/langsmith/manage-datasets-programmatically) [评测概念](https://docs.langchain.com/langsmith/evaluation-concepts) [evaluate 指南](https://docs.langchain.com/langsmith/evaluate-llm-application)

两边都能承载金标集：输入、期望输出、一次运行、一个分数。差异在运行位置和许可证，不在“有没有 dataset / experiment / score”。Langfuse 自托开源版包含这些核心能力。LangSmith 自托的基础安装也包含评测，但自托本身是 Enterprise 附加项。[Langfuse 自托定价](https://langfuse.com/pricing-self-host) [LangSmith 自托](https://docs.langchain.com/langsmith/self-hosted)

## 自托与数据出境

使用 Langfuse Cloud 或 LangSmith Cloud 时，进程会把要记录的 trace 发到对方托管端点。云不是接入 SDK 的前提。

Langfuse：不自托就发到所选 Cloud 区域；自托则落在自己的 PostgreSQL、ClickHouse、Redis 或 Valkey、blob。开源版不需要企业许可证即可使用观测、评测、数据集。导出前可以用 Python SDK 的 `mask_otel_spans` 改 OpenTelemetry 属性；该钩子只影响这个 Langfuse 客户端导出的 span。[区域](https://langfuse.com/security/data-regions) [自托](https://langfuse.com/self-hosting) [脱敏](https://langfuse.com/docs/observability/features/masking)

LangSmith：Cloud 发到对应区域的 API，trace 存在该区域的 ClickHouse Cloud 或 SmithDB。自托时文档写 trace 留在自己的环境，但要 Enterprise 许可证和 Kubernetes。客户端支持 `LANGSMITH_HIDE_INPUTS` 与 `LANGSMITH_HIDE_OUTPUTS`；`Client` 在组 run 时调用 `_hide_run_inputs`，`RunTree.post` 会走到 `create_run`。Agents processor 通过 `RunTree.post()` 提交，因此这条脱敏作用在它提交的 inputs 和 outputs 上。这是第二层；第一层仍是 Agents SDK 在写 span 时就省略敏感字段。[Cloud](https://docs.langchain.com/langsmith/cloud) [自托](https://docs.langchain.com/langsmith/self-hosted) [脱敏文档](https://docs.langchain.com/langsmith/mask-inputs-outputs) [Client 隐藏输入](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/langsmith/client.py) [RunTree.post](https://github.com/langchain-ai/langsmith-sdk/blob/290f06a628dffb2ca690e389dc48b629f5623312/python/langsmith/run_trees.py)

在 `trace_include_sensitive_data=False` 时，两家云都不会从函数工具 span 或模型 span 收到 SQL 文本和工具返回值，因为那些字段没有被写进 span。它们仍会收到 span 上剩下的内容：trace 名、agent 名、工具名、token 用量，以及调用方放进 trace metadata 的内容。若为了调试把标志改回 True，则云上两家都会收到模型和工具的输入输出，除非自托或再做脱敏。

继续保留默认 OpenAI 导出器时，同一份已裁剪的 span 仍会发到 `https://api.openai.com/v1/traces/ingest`。追加 Langfuse 或 LangSmith 是再加一个目的地，不会减少已经发往 OpenAI 的字段。[导出端点](https://github.com/openai/openai-agents-python/blob/v0.25.0/src/agents/tracing/processors.py)

## 适配判断

约束是：不换掉 `openai-agents`，少改代码，数据里可能有 SQL 和业务数据，并且已经打开了官方 tracing 和敏感数据关闭开关。

两家都满足“不换框架”。LangSmith 不是只服务 LangChain 的集成；Langfuse 也不是要求改用 LangChain。

少改代码：LangSmith 是一个 extra 加一次 processor 注册。Langfuse 要再装 OpenInference 仪器，并让 Langfuse 客户端作为 OTEL 导出端。LangSmith 文档示例用 `set_trace_processors`，Langfuse 文档示例用无参 `instrument()`，两者都会拿掉默认 OpenAI 导出器。要保留官方 tracing，LangSmith 应改为 `add_trace_processor`，Langfuse 应改为 `OpenAIAgentsInstrumentor().instrument(exclusive_processor=False)`。后一个参数在 Langfuse 集成页没有出现，只在 OpenInference 源码里。这一步两边都要偏离各自的快速开始。[LangSmith 集成页](https://docs.langchain.com/langsmith/trace-with-openai-agents-sdk) [Langfuse 集成页](https://langfuse.com/integrations/frameworks/openai-agents) [追加与替换](https://github.com/openai/openai-agents-python/blob/v0.25.0/docs/tracing.md) [exclusive_processor](https://github.com/Arize-ai/openinference/blob/9683d60dc2aaeb704607fb9b6c2efe2ba13d2698/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/__init__.py)

数据：保持 `trace_include_sensitive_data=False` 时，SQL 和工具输出本来就不会进这两家。这个开关比选厂商更决定业务数据会不会出境。若以后需要在 trace 里看 SQL，Langfuse 可以用 MIT 自托把 ClickHouse 留在自己的机器上；LangSmith 也能把 trace 留在自己的集群，但文档把自托写成 Enterprise 附加项，需要许可证和 Kubernetes。只为看 trace 就上 LangSmith 自托，门槛更高。只把已裁剪的 trace 发到云，两家都会把剩余字段送到各自的云区域，另外 OpenAI 默认导出器还会再送一份到 `api.openai.com`。

评测：金标 JSON 两边都能写成数据集条目再跑实验。LangSmith 的 `evaluate()` 可以直接包一层现有的 runner。Langfuse 的 `run_experiment` 同样接收自己的 task。评测能力本身不分胜负；自托许可证才分。

因此：优先少改代码、并且可以接受云上只存放已裁剪 span，LangSmith 的 processor 更贴这个 SDK。优先以后可能打开敏感数据、又要避免企业许可证才能把 SQL 留在内网，Langfuse 自托更贴这个约束。不适合把 LangSmith 说成“必须改用 LangChain”。不适合按 Langfuse 集成页无参 `instrument()` 或按 LangSmith 快速开始的 `set_trace_processors` 直接接上，那两步都会替换掉现有的 OpenAI 上传。

## 未能确认

未在 openai-agents 0.25.0 源码或该 tag 的文档中确认环境变量 `OPENAI_TRACING_API_KEY`。源码使用的是 `OPENAI_API_KEY`、`set_tracing_export_api_key()` 和 `RunConfig.tracing.api_key`。

未在 LangSmith 或 Langfuse 的 Agents 集成页看到 `Runner.run_streamed` 的专门示例或测试。流式覆盖是从 SDK 的 trace 创建点和 processor 接口推出来的。

未在 OpenInference 或 langsmith extra 的版本声明里确认它们对 `openai-agents==0.25.0` 做过兼容测试。只确认 0.25.0 高于两边写下的最低版本，且 0.25.0 仍提供这些集成所调用的 `TracingProcessor`、`set_trace_processors`、`add_trace_processor` 和 span 数据类型。

未在 LangSmith 当前集成页确认：只设置 `LANGSMITH_PROJECT`、不设置 `LANGSMITH_AGENT_ID` 时，`langsmith>=0.14.9` 是否仍接受 `OpenAIAgentsTracingProcessor` 的写入。页内 Python 快速开始要求 agent id；处理器构造函数仍有 `project_name` 参数。

未在 Langfuse 集成页确认 OpenInference 的 `llm.token_count.*` 在 Agents 这条链上如何逐字段映射成 Langfuse 的 `usage_details`。集成页写可以看到 token usage；成本文档写 usage 到位后才按模型价格推算 cost。

未确认 Langfuse 或 LangSmith 有“上传一份任意结构的金标 JSON 文件”的专用接口。确认的是 SDK 接受任意对象或 dict 作为数据集条目。

未把 LangSmith 平台服务器的源代码许可证对上某一份 OSI 文本。确认的是客户端 SDK 为 MIT，自托文档要求 Enterprise 许可证密钥。
