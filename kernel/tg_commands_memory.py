from __future__ import annotations
import html
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes
from kernel.tg_common import BotState, _check_user, _send_text

_memory_map: dict[int, int] = {}


def _build_memory_map(memories: list[dict[str, Any]]) -> dict[int, int]:
    global _memory_map
    _memory_map = {i + 1: m['id'] for i, m in enumerate(memories)}
    return _memory_map


def _resolve_memory_num(num_str: str) -> int | None:
    try:
        n = int(num_str.lstrip('#'))
    except ValueError:
        return None
    return _memory_map.get(n)

async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    text = ' '.join(context.args) if context.args else ''
    if not text:
        await _send_text(update, '用法：/remember <要记住的内容>', parse_mode=None)
        return
    mid = await state.store.memory_add(text)
    await _send_text(update, f'已记住 (id={mid})', parse_mode=None)

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    memories = await state.store.memory_list()
    if not memories:
        await _send_text(update, '暂无长期记忆。', parse_mode=None)
        return
    mmap = _build_memory_map(memories)
    lines: list[str] = []
    for n, mid in sorted(mmap.items()):
        m = next((m for m in memories if m['id'] == mid))
        date = state.format_dt(m['created_at'])
        lines.append(f"<b>#{n}</b> {date} {html.escape(m['text'])}")
    await _send_text(update, '\n'.join(lines))

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if not context.args:
        await _send_text(update, '用法：/forget #n [#n2 ...]\n先用 /memory 查看序号。', parse_mode=None)
        return
    mids: list[int] = []
    for a in context.args:
        for part in a.split('/'):
            mid = _resolve_memory_num(part.strip())
            if mid is None:
                await _send_text(update, f'无效序号：{part}。先用 /memory 查看。', parse_mode=None)
                return
            mids.append(mid)
    deleted = 0
    for mid in mids:
        if await state.store.memory_delete(mid):
            deleted += 1
    await _send_text(update, f'已删除 {deleted} 条记忆。', parse_mode=None)
