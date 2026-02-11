from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import aiosqlite
import jieba
log = logging.getLogger(__name__)
SCHEMA_VERSION = 4
_DDL = "CREATE TABLE IF NOT EXISTS sessions (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    title       TEXT,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),\n    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),\n    archived    INTEGER NOT NULL DEFAULT 0\n);\n\nCREATE TABLE IF NOT EXISTS messages (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,\n    role        TEXT NOT NULL,\n    content     TEXT NOT NULL,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))\n);\nCREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);\n\nCREATE TABLE IF NOT EXISTS settings (\n    key   TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n);\n\nCREATE TABLE IF NOT EXISTS memories (\n    id          INTEGER PRIMARY KEY AUTOINCREMENT,\n    text        TEXT NOT NULL,\n    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))\n);\n"
_FTS_DDL = "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='', content_rowid=id);\n"

def _tokenize(text: str) -> str:
    return ' '.join(jieba.cut_for_search(text))

class Store:

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self.fts5_available: bool = False

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
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
            self.fts5_available = await self._try_fts5()
            await self._db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
            await self._db.commit()
            log.info('Database migrated to version %d (FTS5=%s)', SCHEMA_VERSION, self.fts5_available)
        else:
            self.fts5_available = await self._check_fts5_exists()

    async def _try_fts5(self) -> bool:
        assert self._db
        try:
            await self._db.executescript('DROP TRIGGER IF EXISTS memories_ai;DROP TRIGGER IF EXISTS memories_ad;DROP TRIGGER IF EXISTS memories_au;DROP TABLE IF EXISTS memories_fts;')
            await self._db.executescript(_FTS_DDL)
            cur = await self._db.execute('SELECT id, text FROM memories')
            rows = await cur.fetchall()
            for row in rows:
                await self._db.execute('INSERT INTO memories_fts(rowid, text) VALUES (?, ?)', (row[0], _tokenize(row[1])))
            await self._db.commit()
            return True
        except Exception as exc:
            log.warning('FTS5 not available, falling back to LIKE: %s', exc)
            return False

    async def _check_fts5_exists(self) -> bool:
        assert self._db
        try:
            cur = await self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'")
            return await cur.fetchone() is not None
        except Exception:
            return False

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

    async def create_session(self, title: str | None=None) -> int:
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
        placeholders = ','.join(('?' for _ in session_ids))
        cur = await self._db.execute(f'DELETE FROM sessions WHERE id IN ({placeholders})', session_ids)
        await self._db.commit()
        return cur.rowcount

    async def list_sessions(self, limit: int=20) -> list[dict[str, Any]]:
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

    async def get_messages(self, session_id: int, *, limit: int | None=None) -> list[dict[str, Any]]:
        assert self._db
        sql = 'SELECT id, session_id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC'
        params: tuple = (session_id,)
        if limit:
            sql += ' LIMIT ?'
            params = (session_id, limit)
        if limit:
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
        cur = await self._db.execute('INSERT INTO memories (text) VALUES (?)', (text,))
        mid = cur.lastrowid
        if self.fts5_available:
            await self._db.execute('INSERT INTO memories_fts(rowid, text) VALUES (?, ?)', (mid, _tokenize(text)))
        await self._db.commit()
        return mid

    async def memory_search(self, query: str, limit: int=5) -> list[dict[str, Any]]:
        assert self._db
        if self.fts5_available:
            tokenized = _tokenize(query)
            try:
                cur = await self._db.execute('SELECT m.id, m.text, m.created_at FROM memories m JOIN memories_fts f ON m.id = f.rowid WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?', (tokenized, limit))
                rows = await cur.fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except Exception:
                pass
        cur = await self._db.execute('SELECT id, text, created_at FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT ?', (f'%{query}%', limit))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def memory_list(self, limit: int=200) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute('SELECT id, text, created_at FROM memories ORDER BY id DESC LIMIT ?', (limit,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def memory_delete(self, memory_id: int) -> bool:
        assert self._db
        if self.fts5_available:
            cur = await self._db.execute('SELECT text FROM memories WHERE id = ?', (memory_id,))
            row = await cur.fetchone()
            if row:
                await self._db.execute("INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', ?, ?)", (memory_id, _tokenize(row[0])))
        cur = await self._db.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
        await self._db.commit()
        return cur.rowcount > 0
    _SLIM_THRESHOLD = 200

    @staticmethod
    def slim_content(role: str, content: Any) -> Any:
        if not isinstance(content, list):
            return content
        slimmed: list[Any] = []
        for block in content:
            if not isinstance(block, dict):
                slimmed.append(block)
                continue
            btype = block.get('type')
            if btype == 'image':
                slimmed.append({'type': 'text', 'text': '[图片已处理]'})
                continue
            if btype == 'text':
                text = block.get('text', '')
                if text.startswith('[文件: ') and '\n```\n' in text:
                    fname = text.split(']', 1)[0].removeprefix('[文件: ')
                    slimmed.append({'type': 'text', 'text': f'[文件 {fname} 已处理]'})
                    continue
                if text.startswith('[语音: ') and text.endswith(']'):
                    slimmed.append({'type': 'text', 'text': '[语音已处理]'})
                    continue
            if btype == 'tool_result':
                raw = block.get('content', '')
                if isinstance(raw, str):
                    should_slim = Store._should_slim_tool_result(raw)
                    if should_slim:
                        summary = Store._summarise_tool_result(raw)
                        slimmed.append({**block, 'content': summary})
                        continue
            slimmed.append(block)
        return slimmed

    @staticmethod
    def _should_slim_tool_result(raw: str) -> bool:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get('output_path'):
                return True
        except (json.JSONDecodeError, TypeError):
            pass
        return len(raw) > Store._SLIM_THRESHOLD

    @staticmethod
    def _summarise_tool_result(raw: str) -> str:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                ok = data.get('ok')
                cli = data.get('cli', '')
                exit_code = data.get('exit_code')
                output_path = data.get('output_path', '')
                if ok is not None and output_path:
                    status = '成功' if ok else f'失败(exit={exit_code})'
                    return f'[{cli} 任务{status}，详见 {output_path}]'
                keys = ', '.join(list(data.keys())[:5])
                return f'[工具结果: {{{keys}...}}，{len(raw)} 字符已省略]'
        except (json.JSONDecodeError, TypeError):
            pass
        preview = raw[:80].replace('\n', ' ')
        return f'[工具结果: {preview}… ({len(raw)} 字符已省略)]'

    async def add_message_slimmed(self, session_id: int, role: str, content: Any) -> int:
        return await self.add_message(session_id, role, self.slim_content(role, content))

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
