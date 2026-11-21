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

订单主题空间连接 DuckDB 时默认使用 Direct SQL。Agent 直接生成 SQL，后端根据规范语义模型检查获准的订单表、字段与指标口径，随后只读执行。历史会话中的「去年同期」追问可继承已保存的日期和筛选条件。[ADR 0003](docs/adr/0003-direct-sql-duckdb-first.md)

要连接 PostgreSQL，在 `.env` 设置 `SALES_DATABASE_URL=postgresql://用户名:密码@主机:端口/数据库`，并为销售模型在 YAML 的 `models.sales_summary` 下添加 `schema`，同时配置允许的表、字段和指标表达式。使用仅授予该 schema 和表 SELECT 权限的数据库账户。服务使用只读事务、5 秒连接和语句超时、参数绑定及表字段白名单。三者同时设置时，PostgreSQL 优先，其次是 DuckDB，最后是 SQLite。重启后端生效。

接入前需验证业务日期与物理字段类型、销售额和退款口径、订单粒度，以及订单量在每个启用维度上可加总。当前实现支持 PostgreSQL、DuckDB 与 SQLite，生产数据接入需要使用具体目标库完成连通性和口径验收。

模型调用可通过 `OPENAI_BASE_URL` 指向兼容服务。追踪导出默认关闭。需要 OpenAI Tracing 时，单独设置 `OPENAI_TRACING_API_KEY` 为有效的 OpenAI 密钥。模型服务密钥独立保存在 `OPENAI_API_KEY`。启用追踪时，应用关闭 SDK 中的敏感内容采集。

Golden Dataset 使用固定的 2026-06-01 业务日期和独立 SQLite 测试数据，运行以下命令可验证 11 条标准查询。输出包含每条实际 Query IR、SQL、执行状态、结果及失败环节。

```sh
PYTHONPATH=backend python -m app.evaluation.runner
```

要评测真实模型的自然语言解析，先加载 `.env`，然后执行：

```sh
PYTHONPATH=backend python -m app.evaluation.runner --live
```

真实模型评测会调用服务并可能产生费用。未设置密钥时逐条标记为 skipped，失败会返回非零退出码。默认输出为 JSON，可重定向到工作区以外的文件以保存每次实测记录。
