import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def initialize(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    with sqlite3.connect(path) as db:
        db.execute("""
            CREATE TABLE sales_summary (
                stat_date TEXT NOT NULL, city TEXT NOT NULL,
                store_id TEXT NOT NULL, product TEXT NOT NULL,
                sales_amount INTEGER NOT NULL, order_count INTEGER NOT NULL
            )
        """)
        db.executemany(
            "INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)",
            [
                (f"{year}-01-01", "北京", "BJ001", "商品A", 12000, 30),
                (f"{year}-01-01", "上海", "SH001", "商品B", 8500, 20),
                (f"{year}-01-01", "广州", "GZ001", "商品A", 0, 0),
                (f"{year - 1}-01-01", "北京", "BJ001", "商品A", 9000, 25),
            ],
        )
