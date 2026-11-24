# Data Agent

Sales Space 的本地问数工作台使用 OpenAI Agents SDK、FastAPI、React 与 SQLite 示例数据。

使用 Python 3.11 或更高版本和 Node.js 20 或更高版本。

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
# 编辑 .env，填写 OPENAI_API_KEY 和服务商提供的 OPENAI_MODEL
set -a
source .env
set +a
cd backend
uvicorn app.main:app --reload
```

另开终端启动前端。

```sh
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173，输入“查询今年各城市的销售额”。服务自动创建 SQLite 示例数据，业务查询以只读模式打开数据库。

API 地址由 `OPENAI_BASE_URL` 配置，默认使用 `https://api.agicto.cn/v1`。`.env` 文件不会自动加载，每次在启动后端的终端执行上面的 `source .env`。模型名称须使用该地址支持的模型。`.env` 已加入 Git 忽略列表，请将密钥保存在此文件中。

```sh
PYTHONPATH=backend pytest backend/tests
npm --prefix frontend run build
```

支持销售额、订单量和客单价，按城市、门店、产品和业务日期分组或过滤。销售主题空间和物理字段映射配置在 `backend/configs/sales_space.yaml`。

## 接入已授权 ADS

默认使用自动创建的本地 SQLite 示例数据。要读取已有 SQLite 文件，在 `.env` 设置 `SALES_SQLITE_PATH=/绝对路径/销售数据.db`。该文件以只读模式打开，不会自动创建。

要读取 DuckDB 文件，在 `.env` 设置 `SALES_DUCKDB_PATH=/绝对路径/销售数据.duckdb`。服务以只读方式打开该文件，不会根据文件名猜测引擎。文件需包含当前主题空间声明的单表和字段。`SALES_SPACE=orders` 时使用订单主题空间，物理表为 `orders`，适合 Wren 的 `jaffle_shop.duckdb`。默认 `sales` 仍使用销售汇总表。`SALES_DATABASE_URL` 优先于 DuckDB，DuckDB 优先于 `SALES_SQLITE_PATH`。

订单主题空间连接 DuckDB 时默认使用 Direct SQL，不退回结构化查询。Agent 直接生成 SQL，后端用已发布规范语义模型检查获准的表、字段、指标和客户–订单等值内连接，随后只读执行。DuckDB 是这条路径的开发和验收数据库。SQLite 与 PostgreSQL 仍可按下文接入。「去年同期」追问以同一 Direct SQL 会话的最近一次成功查询为依据。[ADR 0003](docs/adr/0003-direct-sql-duckdb-first.md) 查询形状以 [ADR 0007](docs/adr/0007-readonly-sql-with-fanout.md) 为准：一条只读语句可以包含 WITH 和空间内的连接，订单指标仍按订单粒度校验。

要连接 PostgreSQL，在 `.env` 设置 `SALES_DATABASE_URL=postgresql://用户名:密码@主机:端口/数据库`，并为销售模型在 YAML 的 `models.sales_summary` 下添加 `schema`，同时配置允许的表、字段和指标表达式。使用仅授予该 schema 和表 SELECT 权限的数据库账户。服务使用只读事务、5 秒连接和语句超时、参数绑定及表字段白名单。三者同时设置时，PostgreSQL 优先，其次是 DuckDB，最后是 SQLite。重启后端生效。

接入前需验证业务日期与物理字段类型、销售额和退款口径、订单粒度，以及订单量在每个启用维度上可加总。当前实现支持 PostgreSQL、DuckDB 与 SQLite，生产数据接入需要使用具体目标库完成连通性和口径验收。

模型调用可通过 `OPENAI_BASE_URL` 指向兼容服务。模型服务密钥保存在 `OPENAI_API_KEY`。追踪只在设置 `LANGSMITH_API_KEY` 或 `LANGCHAIN_API_KEY` 时发往 LangSmith，不发往 OpenAI 的追踪地址。启用追踪时不采集模型和工具的输入输出。

销售 Golden Dataset 仍覆盖原来的结构化查询，使用固定的 2026-06-01 业务日期和独立 SQLite 测试数据。它不是这轮订单验收。运行以下命令可验证 11 条标准查询。输出包含每条实际 Query IR、SQL、执行状态、结果及失败环节。

```sh
PYTHONPATH=backend python -m app.evaluation.runner
```

要评测真实模型的自然语言解析，先加载 `.env`，然后执行：

```sh
PYTHONPATH=backend python -m app.evaluation.runner --live
```

真实模型评测会调用服务并可能产生费用。未设置密钥时逐条标记为 skipped，失败会返回非零退出码。默认输出为 JSON，可重定向到工作区以外的文件以保存每次实测记录。

订单主题空间的当前评测单独存放在 `backend/app/evaluation/orders_golden.json`。先加载 `.env`，并把 `SALES_DUCKDB_PATH` 指到 Jaffle Shop DuckDB，然后执行：

```sh
PYTHONPATH=backend python -m app.evaluation.runner --orders
```

这条命令调用真实模型。问数题只比对结果行，不要求 SQL 原文一致；拒绝题不能返回结果行；「去年同期」沿用同一会话，核对日期被平移且结果为空。「能查什么」不调用模型，回复当前空间授权原文。未设置密钥或 DuckDB 路径时，对应用例记为 skipped。执行器和校验器的回归仍是 `backend/tests/test_direct_sql_chat.py`。
