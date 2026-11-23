"""Local conversation and query context storage."""
import json
from contextlib import closing, contextmanager
import sqlite3
import uuid
from pathlib import Path


@contextmanager
def connect(path: Path):
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
        CREATE TABLE IF NOT EXISTS feedback (
            message_id TEXT PRIMARY KEY,
            rating TEXT NOT NULL CHECK (rating IN ('correct', 'incorrect')),
            FOREIGN KEY (message_id) REFERENCES messages(id)
        );
    ''')
    with closing(db), db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
        if 'process' not in columns:
            db.execute('ALTER TABLE messages ADD COLUMN process TEXT')
        yield db


def create(path: Path, title: str) -> str:
    conversation_id = str(uuid.uuid4())
    with connect(path) as db:
        db.execute('INSERT INTO conversations (id, title) VALUES (?, ?)', (conversation_id, title[:80]))
    return conversation_id


def delete(path: Path, conversation_id: str) -> bool:
    with connect(path) as db:
        row = db.execute('SELECT id FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
        if row is None:
            return False
        db.execute('DELETE FROM feedback WHERE message_id IN (SELECT id FROM messages WHERE conversation_id = ?)', (conversation_id,))
        db.execute('DELETE FROM messages WHERE conversation_id = ?', (conversation_id,))
        db.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
    return True


def list_conversations(path: Path) -> list[dict]:
    with connect(path) as db:
        rows = db.execute('SELECT id, title, created_at FROM conversations ORDER BY created_at DESC, rowid DESC').fetchall()
    return [dict(row) for row in rows]


def get(path: Path, conversation_id: str) -> dict | None:
    with connect(path) as db:
        row = db.execute('SELECT * FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
        if row is None:
            return None
        messages = db.execute('''SELECT messages.id, messages.role, messages.content, messages.result,
            messages.process, messages.created_at, feedback.rating AS feedback FROM messages
            LEFT JOIN feedback ON feedback.message_id = messages.id
            WHERE messages.conversation_id = ? ORDER BY messages.rowid''', (conversation_id,)).fetchall()
    return {
        'id': row['id'], 'title': row['title'], 'created_at': row['created_at'],
        'last_query': json.loads(row['last_query']) if row['last_query'] else None,
        'messages': [_message_row(message) for message in messages],
    }


def _message_row(message) -> dict:
    item = dict(message)
    item['result'] = json.loads(item['result']) if item['result'] else None
    item['process'] = _load_process(item.get('process'))
    return item


def _load_process(raw: str | None):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def add_message(path: Path, conversation_id: str, role: str, content: str, result: dict | None = None, query: dict | None = None, process: list | None = None) -> str:
    message_id = str(uuid.uuid4())
    with connect(path) as db:
        db.execute(
            'INSERT INTO messages (id, conversation_id, role, content, result, process) VALUES (?, ?, ?, ?, ?, ?)',
            (
                message_id, conversation_id, role, content,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                json.dumps(process, ensure_ascii=False) if process else None,
            ),
        )
        if query is not None:
            db.execute('UPDATE conversations SET last_query = ? WHERE id = ?', (json.dumps(query, ensure_ascii=False), conversation_id))
    return message_id


def save_feedback(path: Path, message_id: str, rating: str) -> bool:
    with connect(path) as db:
        message = db.execute("SELECT id FROM messages WHERE id = ? AND role = 'assistant'", (message_id,)).fetchone()
        if message is None:
            return False
        db.execute('''INSERT INTO feedback (message_id, rating) VALUES (?, ?)
            ON CONFLICT(message_id) DO UPDATE SET rating = excluded.rating''', (message_id, rating))
    return True
