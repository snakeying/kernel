from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from kernel.memory.memories import (
    check_fts5_exists as _check_fts5_exists,
    memory_add as _memory_add,
    memory_delete as _memory_delete,
    memory_list as _memory_list,
    memory_search as _memory_search,
    try_fts5 as _try_fts5,
)
from kernel.memory.slim import slim_content as _slim_content

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4
_DDL = "CREATE TABLE IF NOT EXISTS sessions (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    title       TEXT,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),\n    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),\n    archived    INTEGER NOT NULL DEFAULT 0\n);\n\nCREATE TABLE IF NOT EXISTS messages (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,\n    role        TEXT NOT NULL,\n    content     TEXT NOT NULL,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))\n);\nCREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);\n\nCREATE TABLE IF NOT EXISTS settings (\n    key   TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n);\n\nCREATE TABLE IF NOT EXISTS memories (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    text        TEXT NOT NULL,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))\n);\n"

_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "sessions": {"id", "title", "created_at", "updated_at", "archived"},
    "messages": {"id", "session_id", "role", "content", "created_at"},
    "settings": {"key", "value"},
    "memories": {"id", "text", "created_at"},
}


async def _get_user_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cur = await db.execute(f'PRAGMA table_info("{table}")')
    rows = await cur.fetchall()
    return {r["name"] for r in rows}


async def _schema_compatible(db: aiosqlite.Connection) -> bool:
    version = await _get_user_version(db)
    if version != SCHEMA_VERSION:
        return False
    for table, required in _REQUIRED_COLUMNS.items():
        cols = await _table_columns(db, table)
        if not required.issubset(cols):
            return False
    return True


def _delete_db_files(db_path: Path) -> None:
    candidates = [
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
        db_path.with_name(db_path.name + "-journal"),
    ]
    errors: list[str] = []
    for p in candidates:
        try:
            p.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{p}: {exc}")
            log.warning("Failed to remove old DB file %s: %s", p, exc)
    if db_path.exists():
        msg = f"Failed to remove old DB file: {db_path}"
        if errors:
            msg += "\n" + "\n".join(errors)
        raise RuntimeError(msg)


class Store:

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self.fts5_available: bool = False

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        exists_before = self._db_path.exists()
        try:
            self._db = await aiosqlite.connect(str(self._db_path))
        except Exception as exc:
            if not exists_before:
                raise
            log.warning("Failed to open DB %s (%s) - rebuilding", self._db_path, exc)
            _delete_db_files(self._db_path)
            self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute('PRAGMA journal_mode=WAL')
        await self._db.execute('PRAGMA foreign_keys=ON')

        if exists_before:
            needs_rebuild = False
            try:
                needs_rebuild = not await _schema_compatible(self._db)
            except Exception as exc:
                log.warning("DB schema check failed (%s) - rebuilding", exc)
                needs_rebuild = True
            if needs_rebuild:
                log.warning("DB schema mismatch - rebuilding database (data will be lost)")
                await self.close()
                _delete_db_files(self._db_path)
                self._db = await aiosqlite.connect(str(self._db_path))
                self._db.row_factory = aiosqlite.Row
                await self._db.execute('PRAGMA journal_mode=WAL')
                await self._db.execute('PRAGMA foreign_keys=ON')
        await self._migrate()

    async def _migrate(self) -> None:
        assert self._db
        cur = await self._db.execute('PRAGMA user_version')
        row = await cur.fetchone()
        version = row[0] if row else 0
        if version < SCHEMA_VERSION:
            await self._db.executescript(_DDL)
            self.fts5_available = await _try_fts5(self._db)
            await self._db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
            await self._db.commit()
            log.info('Database migrated to version %d (FTS5=%s)', SCHEMA_VERSION, self.fts5_available)
        else:
            self.fts5_available = await _check_fts5_exists(self._db)

    async def close(self) -> None:
        if self._db:
            try:
                await self._db.close()
            except BaseException:
                try:
                    if self._db._conn:
                        self._db._conn.close()
                except BaseException:
                    pass
            self._db = None

    async def get_setting(self, key: str) -> str | None:
        assert self._db
        cur = await self._db.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        assert self._db
        await self._db.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))
        await self._db.commit()

    async def create_session(self, title: str | None = None) -> int:
        assert self._db
        cur = await self._db.execute('INSERT INTO sessions (title) VALUES (?)', (title,))
        await self._db.commit()
        return cur.lastrowid

    async def update_session_title(self, session_id: int, title: str) -> None:
        assert self._db
        await self._db.execute('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?', (title, _now_iso(), session_id))
        await self._db.commit()

    async def archive_session(self, session_id: int) -> None:
        assert self._db
        await self._db.execute('UPDATE sessions SET archived = 1, updated_at = ? WHERE id = ?', (_now_iso(), session_id))
        await self._db.commit()

    async def delete_sessions(self, session_ids: list[int]) -> int:
        assert self._db
        if not session_ids:
            return 0
        placeholders = ','.join(('?' for _ in session_ids))
        cur = await self._db.execute(f'DELETE FROM sessions WHERE id IN ({placeholders})', session_ids)
        await self._db.commit()
        return cur.rowcount

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute('SELECT id, title, created_at, updated_at, archived FROM sessions ORDER BY updated_at DESC LIMIT ?', (limit,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_session(self, session_id: int) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute('SELECT id, title, created_at, updated_at, archived FROM sessions WHERE id = ?', (session_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def add_message(self, session_id: int, role: str, content: Any) -> int:
        assert self._db
        content_json = json.dumps(content, ensure_ascii=False)
        cur = await self._db.execute('INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)', (session_id, role, content_json))
        await self._db.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (_now_iso(), session_id))
        await self._db.commit()
        return cur.lastrowid

    async def get_messages(self, session_id: int, *, limit: int | None = None) -> list[dict[str, Any]]:
        assert self._db
        sql = 'SELECT id, session_id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC'
        params: tuple = (session_id,)
        if limit is not None:
            if limit <= 0:
                return []
            sql = 'SELECT * FROM (  SELECT id, session_id, role, content, created_at   FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?) sub ORDER BY id ASC'
            params = (session_id, limit)
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['content'] = json.loads(d['content'])
            result.append(d)
        return result

    async def count_messages(self, session_id: int) -> int:
        assert self._db
        cur = await self._db.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

    async def memory_add(self, text: str) -> int:
        assert self._db
        return await _memory_add(self._db, text, fts5_available=self.fts5_available)

    async def memory_search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        assert self._db
        return await _memory_search(self._db, query, limit=limit, fts5_available=self.fts5_available)

    async def memory_list(self, limit: int = 200) -> list[dict[str, Any]]:
        assert self._db
        return await _memory_list(self._db, limit=limit)

    async def memory_delete(self, memory_id: int) -> bool:
        assert self._db
        return await _memory_delete(self._db, memory_id, fts5_available=self.fts5_available)

    @staticmethod
    def slim_content(role: str, content: Any) -> Any:
        return _slim_content(role, content)

    async def add_message_slimmed(self, session_id: int, role: str, content: Any) -> int:
        return await self.add_message(session_id, role, self.slim_content(role, content))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
