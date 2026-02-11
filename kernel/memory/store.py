"""SQLite session/message storage with history slimming.

Schema
------
- ``sessions``  — one row per conversation session
- ``messages``  — ordered messages within a session (stored as JSON)

History slimming:
- Phase 1: image base64 → ``[图片已处理]``
- Phase 2: tool_result content → one-line summary + artifact path
- Phase 3: file content → ``[文件 xxx.py 已处理]``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

_DDL = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    archived    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    """Async SQLite store for sessions and messages."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def _migrate(self) -> None:
        assert self._db
        cur = await self._db.execute("PRAGMA user_version")
        row = await cur.fetchone()
        version = row[0] if row else 0
        if version < SCHEMA_VERSION:
            await self._db.executescript(_DDL)
            await self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            await self._db.commit()
            log.info("Database migrated to version %d", SCHEMA_VERSION)

    async def close(self) -> None:
        if self._db:
            # Try async close first; if event loop is being torn down,
            # fall back to closing the underlying sync connection directly.
            try:
                await self._db.close()
            except BaseException:
                try:
                    if self._db._conn:
                        self._db._conn.close()
                except BaseException:
                    pass
            self._db = None

    # -- Settings --------------------------------------------------------

    async def get_setting(self, key: str) -> str | None:
        assert self._db
        cur = await self._db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._db.commit()

    # -- Sessions --------------------------------------------------------

    async def create_session(self, title: str | None = None) -> int:
        assert self._db
        cur = await self._db.execute(
            "INSERT INTO sessions (title) VALUES (?)", (title,)
        )
        await self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def update_session_title(self, session_id: int, title: str) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now_iso(), session_id),
        )
        await self._db.commit()

    async def archive_session(self, session_id: int) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE sessions SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )
        await self._db.commit()

    async def delete_sessions(self, session_ids: list[int]) -> int:
        assert self._db
        placeholders = ",".join("?" for _ in session_ids)
        cur = await self._db.execute(
            f"DELETE FROM sessions WHERE id IN ({placeholders})", session_ids
        )
        await self._db.commit()
        return cur.rowcount

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent sessions (newest first), including archived."""
        assert self._db
        cur = await self._db.execute(
            "SELECT id, title, created_at, updated_at, archived "
            "FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_session(self, session_id: int) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT id, title, created_at, updated_at, archived "
            "FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    # -- Messages --------------------------------------------------------

    async def add_message(self, session_id: int, role: str, content: Any) -> int:
        """Store a message.  ``content`` is JSON-serialised."""
        assert self._db
        content_json = json.dumps(content, ensure_ascii=False)
        cur = await self._db.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content_json),
        )
        # Touch session updated_at
        await self._db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )
        await self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_messages(
        self, session_id: int, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return messages for a session, oldest first."""
        assert self._db
        sql = (
            "SELECT id, session_id, role, content, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC"
        )
        params: tuple = (session_id,)
        if limit:
            sql += " LIMIT ?"
            params = (session_id, limit)
        # If we want the *latest* N in chronological order:
        if limit:
            sql = (
                "SELECT * FROM ("
                "  SELECT id, session_id, role, content, created_at "
                "  FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") sub ORDER BY id ASC"
            )
            params = (session_id, limit)
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["content"] = json.loads(d["content"])
            result.append(d)
        return result

    async def count_messages(self, session_id: int) -> int:
        assert self._db
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    # -- History slimming ------------------------------------------------

    _SLIM_THRESHOLD = 200  # characters — generic tool_result below this is kept as-is

    @staticmethod
    def slim_content(role: str, content: Any) -> Any:
        """Apply history slimming rules before persisting.

        - Image base64 → ``[图片已处理]``
        - delegate_to_cli tool_result (has output_path) → always slim
        - Other large tool_result content (>200 chars) → one-line summary
        """
        if not isinstance(content, list):
            return content
        slimmed: list[Any] = []
        for block in content:
            if not isinstance(block, dict):
                slimmed.append(block)
                continue

            btype = block.get("type")

            # Image slimming (Phase 1)
            if btype == "image":
                slimmed.append({"type": "text", "text": "[图片已处理]"})
                continue

            # File content slimming (Phase 3)
            if btype == "text":
                text = block.get("text", "")
                if text.startswith("[文件: ") and "\n```\n" in text:
                    # Extract filename from "[文件: xxx.py]\n```\n...\n```"
                    fname = text.split("]", 1)[0].removeprefix("[文件: ")
                    slimmed.append({"type": "text", "text": f"[文件 {fname} 已处理]"})
                    continue

            # Tool result slimming (Phase 2)
            if btype == "tool_result":
                raw = block.get("content", "")
                if isinstance(raw, str):
                    should_slim = Store._should_slim_tool_result(raw)
                    if should_slim:
                        summary = Store._summarise_tool_result(raw)
                        slimmed.append({**block, "content": summary})
                        continue

            slimmed.append(block)
        return slimmed

    @staticmethod
    def _should_slim_tool_result(raw: str) -> bool:
        """Decide whether a tool_result should be slimmed.

        - delegate_to_cli results (have output_path): always slim
        - Other results: slim if over threshold
        """
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("output_path"):
                return True  # CLI result — always slim (full output on disk)
        except (json.JSONDecodeError, TypeError):
            pass
        return len(raw) > Store._SLIM_THRESHOLD

    @staticmethod
    def _summarise_tool_result(raw: str) -> str:
        """Generate a rule-based one-line summary for a tool_result."""
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                ok = data.get("ok")
                cli = data.get("cli", "")
                exit_code = data.get("exit_code")
                output_path = data.get("output_path", "")

                # delegate_to_cli result
                if ok is not None and output_path:
                    status = "成功" if ok else f"失败(exit={exit_code})"
                    return f"[{cli} 任务{status}，详见 {output_path}]"

                # Generic dict summary
                keys = ", ".join(list(data.keys())[:5])
                return f"[工具结果: {{{keys}...}}，{len(raw)} 字符已省略]"
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: plain text truncation notice
        preview = raw[:80].replace("\n", " ")
        return f"[工具结果: {preview}… ({len(raw)} 字符已省略)]"

    async def add_message_slimmed(self, session_id: int, role: str, content: Any) -> int:
        """Add a message with history slimming applied."""
        return await self.add_message(session_id, role, self.slim_content(role, content))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
