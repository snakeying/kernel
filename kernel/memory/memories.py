from __future__ import annotations

import logging
from typing import Any

import aiosqlite
import jieba

log = logging.getLogger(__name__)

_FTS_DDL = "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='', content_rowid=id);\n"


def _tokenize(text: str) -> str:
    return ' '.join(jieba.cut_for_search(text))


async def try_fts5(db: aiosqlite.Connection) -> bool:
    try:
        await db.executescript('DROP TRIGGER IF EXISTS memories_ai;DROP TRIGGER IF EXISTS memories_ad;DROP TRIGGER IF EXISTS memories_au;DROP TABLE IF EXISTS memories_fts;')
        await db.executescript(_FTS_DDL)
        cur = await db.execute('SELECT id, text FROM memories')
        rows = await cur.fetchall()
        for row in rows:
            await db.execute('INSERT INTO memories_fts(rowid, text) VALUES (?, ?)', (row[0], _tokenize(row[1])))
        await db.commit()
        return True
    except Exception as exc:
        log.warning('FTS5 not available, falling back to LIKE: %s', exc)
        return False


async def check_fts5_exists(db: aiosqlite.Connection) -> bool:
    try:
        cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'")
        return await cur.fetchone() is not None
    except Exception:
        return False


async def memory_add(db: aiosqlite.Connection, text: str, *, fts5_available: bool) -> int:
    cur = await db.execute('INSERT INTO memories (text) VALUES (?)', (text,))
    mid = cur.lastrowid
    if fts5_available:
        await db.execute('INSERT INTO memories_fts(rowid, text) VALUES (?, ?)', (mid, _tokenize(text)))
    await db.commit()
    return mid


async def memory_search(db: aiosqlite.Connection, query: str, *, limit: int = 5, fts5_available: bool) -> list[dict[str, Any]]:
    if fts5_available:
        tokenized = _tokenize(query)
        try:
            cur = await db.execute('SELECT m.id, m.text, m.created_at FROM memories m JOIN memories_fts f ON m.id = f.rowid WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?', (tokenized, limit))
            rows = await cur.fetchall()
            if rows:
                return [dict(r) for r in rows]
        except Exception:
            pass
    cur = await db.execute('SELECT id, text, created_at FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT ?', (f'%{query}%', limit))
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def memory_list(db: aiosqlite.Connection, *, limit: int = 200) -> list[dict[str, Any]]:
    cur = await db.execute('SELECT id, text, created_at FROM memories ORDER BY id DESC LIMIT ?', (limit,))
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def memory_delete(db: aiosqlite.Connection, memory_id: int, *, fts5_available: bool) -> bool:
    if fts5_available:
        cur = await db.execute('SELECT text FROM memories WHERE id = ?', (memory_id,))
        row = await cur.fetchone()
        if row:
            await db.execute("INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', ?, ?)", (memory_id, _tokenize(row[0])))
    cur = await db.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
    await db.commit()
    return cur.rowcount > 0

