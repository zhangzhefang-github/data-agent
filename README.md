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

当前切片支持按城市查询今年截至今天的净销售额。销售主题空间配置在 `backend/configs/sales_space.yaml`。

模型调用可通过 `OPENAI_BASE_URL` 指向兼容服务。SDK Tracing 默认发送到 OpenAI，使用兼容服务密钥时可能出现 tracing 401。若没有可用的 OpenAI tracing 凭据，可以设置 `OPENAI_AGENTS_DISABLE_TRACING=true` 停止追踪导出。模型请求和本地查询仍可运行。启用追踪时，应用关闭 SDK 中的敏感内容采集。
