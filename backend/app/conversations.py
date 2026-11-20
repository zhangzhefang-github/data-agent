"""Local conversation and query context storage."""
import json
import sqlite3
import uuid
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            last_query TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, result TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        );
    ''')
    return db


def create(path: Path, title: str) -> str:
    conversation_id = str(uuid.uuid4())
    with connect(path) as db:
        db.execute('INSERT INTO conversations (id, title) VALUES (?, ?)', (conversation_id, title[:80]))
    return conversation_id


def list_conversations(path: Path) -> list[dict]:
    with connect(path) as db:
        rows = db.execute('SELECT id, title, created_at FROM conversations ORDER BY created_at DESC, rowid DESC').fetchall()
    return [dict(row) for row in rows]


def get(path: Path, conversation_id: str) -> dict | None:
    with connect(path) as db:
        row = db.execute('SELECT * FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
        if row is None:
            return None
        messages = db.execute('SELECT id, role, content, result, created_at FROM messages WHERE conversation_id = ? ORDER BY rowid', (conversation_id,)).fetchall()
    return {
        'id': row['id'], 'title': row['title'], 'created_at': row['created_at'],
        'last_query': json.loads(row['last_query']) if row['last_query'] else None,
        'messages': [{**dict(message), 'result': json.loads(message['result']) if message['result'] else None} for message in messages],
    }


def add_message(path: Path, conversation_id: str, role: str, content: str, result: dict | None = None, query: dict | None = None) -> str:
    message_id = str(uuid.uuid4())
    with connect(path) as db:
        db.execute('INSERT INTO messages (id, conversation_id, role, content, result) VALUES (?, ?, ?, ?, ?)',
                   (message_id, conversation_id, role, content, json.dumps(result, ensure_ascii=False) if result is not None else None))
        if query is not None:
            db.execute('UPDATE conversations SET last_query = ? WHERE id = ?', (json.dumps(query, ensure_ascii=False), conversation_id))
    return message_id
