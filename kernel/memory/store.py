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
from kernel.memory.schema import DDL, SCHEMA_VERSION, ensure_required_columns, format_db_error, get_user_version, schema_compatible
from kernel.memory.slim import slim_content as _slim_content

log = logging.getLogger(__name__)

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
            raise SystemExit(format_db_error(self._db_path, exc)) from exc
        self._db.row_factory = aiosqlite.Row
        await self._db.execute('PRAGMA journal_mode=WAL')
        await self._db.execute('PRAGMA foreign_keys=ON')
        try:
            await self._migrate()
            if not await schema_compatible(self._db):
                raise RuntimeError("DB schema check failed after migration")
        except Exception as exc:
            try:
                await self.close()
            except Exception:
                pass
            raise SystemExit(format_db_error(self._db_path, exc)) from exc

    async def _migrate(self) -> None:
        assert self._db
        version = await get_user_version(self._db)
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"DB schema version {version} is newer than supported {SCHEMA_VERSION}"
            )

        await self._db.executescript(DDL)
        await ensure_required_columns(self._db)

        now = _now_iso()
        await self._db.execute(
            "UPDATE sessions SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
            (now,),
        )
        await self._db.execute(
            "UPDATE sessions SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''",
            (now,),
        )
        await self._db.execute(
            "UPDATE messages SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
            (now,),
        )
        await self._db.execute(
            "UPDATE memories SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
            (now,),
        )

        if version != SCHEMA_VERSION:
            await self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        if version < SCHEMA_VERSION:
            self.fts5_available = await _try_fts5(self._db)
        else:
            self.fts5_available = await _check_fts5_exists(self._db)

        await self._db.commit()
        log.info(
            "Database ready at version %d (FTS5=%s)",
            SCHEMA_VERSION,
            self.fts5_available,
        )

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
        now = _now_iso()
        cur = await self._db.execute(
            'INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)',
            (title, now, now),
        )
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
        now = _now_iso()
        cur = await self._db.execute(
            'INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)',
            (session_id, role, content_json, now),
        )
        await self._db.execute(
            'UPDATE sessions SET updated_at = ? WHERE id = ?', (now, session_id)
        )
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
