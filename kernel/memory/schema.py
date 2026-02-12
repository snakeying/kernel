from __future__ import annotations
from pathlib import Path
import aiosqlite

SCHEMA_VERSION = 4

DDL = "CREATE TABLE IF NOT EXISTS sessions (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    title       TEXT,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),\n    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),\n    archived    INTEGER NOT NULL DEFAULT 0\n);\n\nCREATE TABLE IF NOT EXISTS messages (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,\n    role        TEXT NOT NULL,\n    content     TEXT NOT NULL,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))\n);\nCREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);\n\nCREATE TABLE IF NOT EXISTS settings (\n    key   TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n);\n\nCREATE TABLE IF NOT EXISTS memories (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    text        TEXT NOT NULL,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))\n);\n"

_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "sessions": {"id", "title", "created_at", "updated_at", "archived"},
    "messages": {"id", "session_id", "role", "content", "created_at"},
    "settings": {"key", "value"},
    "memories": {"id", "text", "created_at"},
}

_ADD_COLUMN_DDL: dict[tuple[str, str], str] = {
    ("sessions", "title"): 'ALTER TABLE sessions ADD COLUMN title TEXT',
    ("sessions", "created_at"): "ALTER TABLE sessions ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
    ("sessions", "updated_at"): "ALTER TABLE sessions ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    ("sessions", "archived"): "ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
    ("messages", "created_at"): "ALTER TABLE messages ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
    ("memories", "created_at"): "ALTER TABLE memories ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
    ("settings", "value"): "ALTER TABLE settings ADD COLUMN value TEXT NOT NULL DEFAULT ''",
}

async def get_user_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    return int(row[0]) if row else 0

async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cur = await db.execute(f'PRAGMA table_info("{table}")')
    rows = await cur.fetchall()
    return {r["name"] for r in rows}

async def schema_compatible(db: aiosqlite.Connection) -> bool:
    version = await get_user_version(db)
    if version != SCHEMA_VERSION:
        return False
    for table, required in _REQUIRED_COLUMNS.items():
        cols = await _table_columns(db, table)
        if not required.issubset(cols):
            return False
    return True

def format_db_error(db_path: Path, exc: BaseException) -> str:
    msg = str(exc)
    low = msg.lower()
    hint = ""
    if "database is locked" in low or "database is busy" in low:
        hint = "Hint: database is locked. Make sure only one Kernel instance is running."
    elif "disk i/o error" in low:
        hint = "Hint: disk I/O error. Check storage health and permissions."
    elif "malformed" in low or "not a database" in low:
        hint = "Hint: database file looks corrupted. Restore from backup if possible."
    parts = [
        f"Database error: {msg}",
        f"DB path: {db_path}",
    ]
    if hint:
        parts.append(hint)
    parts.append("Refusing to rebuild automatically to preserve data.")
    return "\n".join(parts)

async def ensure_required_columns(db: aiosqlite.Connection) -> None:
    for table, required in _REQUIRED_COLUMNS.items():
        cols = await _table_columns(db, table)
        missing = sorted(required - cols)
        for col in missing:
            ddl = _ADD_COLUMN_DDL.get((table, col))
            if not ddl:
                raise RuntimeError(
                    f"DB schema incompatible: missing {table}.{col} (no safe migration)"
                )
            await db.execute(ddl)
