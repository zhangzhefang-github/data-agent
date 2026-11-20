# Data Agent

Sales Space 的本地问数工作台使用 OpenAI Agents SDK、FastAPI、React 与 SQLite 示例数据。

使用 Python 3.11 或更高版本和 Node.js 20 或更高版本。

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
export OPENAI_API_KEY=你的密钥
export OPENAI_MODEL=gpt-4.1-mini
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

```sh
PYTHONPATH=backend pytest backend/tests
npm --prefix frontend run build
```

当前切片支持按城市查询今年截至今天的净销售额。销售主题空间配置在 `backend/configs/sales_space.yaml`。
