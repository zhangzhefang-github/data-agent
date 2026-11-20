# Data Agent

An open-source, Genie-inspired enterprise Data Agent that enables users to explore and analyze enterprise data through natural language.

The project focuses on building a reliable data analytics agent with structured business semantics, intelligent query planning, and deterministic SQL execution.

### Core Features

- **Agent Runtime** — Powered by OpenAI Agents SDK.
- **Semantic Layer** — Structured business objects, metrics, dimensions, and relationships.
- **Schema Linking** — Map natural language questions to business semantics and data models.
- **Query Planning** — Convert natural language into structured Query IR.
- **SQL Generation** — Generate and validate SQL through deterministic execution pipelines.
- **Data Analytics** — Return analytical results, tables, and visualizations.
- **Evaluation** — Golden Dataset, benchmark testing, and continuous quality improvement.

### Technology Stack

- Frontend: React, TypeScript, Ant Design, ECharts
- Backend: Python, FastAPI
- Agent Runtime: OpenAI Agents SDK
- SQL Parsing: SQLGlot
- Data Layer: SQL databases
- Observability: Langfuse

### Design Philosophy

LLMs handle natural language understanding and reasoning. Structured semantic models provide business context. Deterministic services handle query planning, SQL validation, and execution.

The goal is to build a reliable, maintainable, and extensible enterprise Data Agent rather than another generic chatbot.

**Inspired by Databricks Genie.**
