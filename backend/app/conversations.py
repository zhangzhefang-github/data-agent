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
    with closing(db), db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            last_query TEXT, pending_clarification TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, result TEXT,
            process TEXT, outcome TEXT, analysis TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
            message_id TEXT PRIMARY KEY,
            rating TEXT NOT NULL CHECK (rating IN ('correct', 'incorrect')),
            FOREIGN KEY (message_id) REFERENCES messages(id)
        );
    ''')
        _migrate(db)
        yield db


def _migrate(db: sqlite3.Connection) -> None:
    required = {'conversations': ('pending_clarification',), 'messages': ('process', 'outcome', 'analysis')}

    def missing_columns():
        for table, names in required.items():
            columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            yield from ((table, name) for name in names if name not in columns)

    if next(missing_columns(), None) is not None:
        # Recheck under the write lock because another connection may have migrated already.
        db.execute('BEGIN IMMEDIATE')
        for table, name in missing_columns():
            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} TEXT')
        db.commit()


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
            messages.process, messages.created_at, messages.outcome, messages.analysis,
            feedback.rating AS feedback FROM messages
            LEFT JOIN feedback ON feedback.message_id = messages.id
            WHERE messages.conversation_id = ? ORDER BY messages.rowid''', (conversation_id,)).fetchall()
    return {
        'id': row['id'], 'title': row['title'], 'created_at': row['created_at'],
        'last_query': json.loads(row['last_query']) if row['last_query'] else None,
        'pending_clarification': json.loads(row['pending_clarification']) if row['pending_clarification'] else None,
        'messages': [_message_row(message) for message in messages],
    }


def _message_row(message) -> dict:
    item = dict(message)
    item['result'] = json.loads(item['result']) if item['result'] else None
    item['process'] = _load_process(item.get('process'))
    item['outcome'] = json.loads(item['outcome']) if item.get('outcome') else None
    item['analysis'] = json.loads(item['analysis']) if item.get('analysis') else None
    return item


def _load_process(raw: str | None):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def add_message(
    path: Path, conversation_id: str, role: str, content: str, result: dict | None = None,
    query: dict | None = None, process: list | None = None, outcome: dict | None = None,
    *, analysis: dict | None = None,
) -> str:
    message_id = str(uuid.uuid4())
    with connect(path) as db:
        db.execute(
            'INSERT INTO messages (id, conversation_id, role, content, result, process, outcome, analysis) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (
                message_id, conversation_id, role, content,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                json.dumps(process, ensure_ascii=False) if process else None,
                json.dumps(outcome, ensure_ascii=False) if outcome is not None else None,
                json.dumps(analysis, ensure_ascii=False) if analysis is not None else None,
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


def set_pending(path: Path, conversation_id: str, pending: dict | None):
    with connect(path) as db:
        db.execute('UPDATE conversations SET pending_clarification = ? WHERE id = ?',
                   (json.dumps(pending, ensure_ascii=False) if pending else None, conversation_id))
