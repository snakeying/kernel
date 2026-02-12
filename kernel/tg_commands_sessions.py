from __future__ import annotations
import html
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes
from kernel.tg_common import BotState, _check_user, _mask_sensitive, _require_idle, _send_text

_history_map: dict[int, int] = {}

def _build_history_map(sessions: list[dict[str, Any]]) -> dict[int, int]:
    global _history_map
    _history_map = {i + 1: s['id'] for i, s in enumerate(sessions)}
    return _history_map

def _resolve_session_num(num_str: str) -> int | None:
    try:
        n = int(num_str.lstrip('#'))
    except ValueError:
        return None
    return _history_map.get(n)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    await _send_text(update, '<b>Kernel</b> — 你的个人 AI 助手\n\n直接发消息开始聊天，或输入 /help 查看命令列表。')

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    await _send_text(update, '<b>命令列表</b>\n\n/new — 新开会话\n/history — 历史会话\n/resume #n — 继续会话\n/retitle [#n] — 重生标题\n/del_history #n — 删除会话\n/provider [name] — 查看/切换 provider\n/model [name] — 查看/切换模型\n/remember &lt;text&gt; — 存入长期记忆\n/memory — 查看所有长期记忆\n/forget #n — 删除记忆\n/cancel — 取消当前任务\n/status — 查看状态')

async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if not await _require_idle(update, state):
        return
    sid = await state.agent.new_session()
    await _send_text(update, f'新会话已创建 (#{sid})')

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    sessions = await state.store.list_sessions(limit=20)
    if not sessions:
        await _send_text(update, '暂无历史会话。')
        return
    hmap = _build_history_map(sessions)
    lines: list[str] = []
    for n, sid in sorted(hmap.items()):
        s = next((s for s in sessions if s['id'] == sid))
        title = html.escape(s['title'] or '无标题')
        date = state.format_dt(s['updated_at'])
        current = ' ← 当前' if sid == state.agent.session_id else ''
        lines.append(f'<b>#{n}</b> {date} {title}{current}')
    await _send_text(update, '\n'.join(lines))

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if not await _require_idle(update, state):
        return
    args = context.args
    if not args:
        await _send_text(update, '用法：/resume #n\n先用 /history 查看序号。', parse_mode=None)
        return
    sid = _resolve_session_num(args[0])
    if sid is None:
        await _send_text(update, f'无效序号：{args[0]}。先用 /history 查看。', parse_mode=None)
        return
    try:
        await state.agent.resume_session(sid)
        session = await state.store.get_session(sid)
        title = session['title'] if session else '无标题'
        count = await state.store.count_messages(sid)
        await _send_text(update, f'已恢复会话：{title}（{count} 条消息）', parse_mode=None)
    except ValueError as exc:
        await _send_text(update, str(exc), parse_mode=None)

async def cmd_retitle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if not await _require_idle(update, state):
        return
    args = context.args
    if args:
        sid = _resolve_session_num(args[0])
        if sid is None:
            await _send_text(update, f'无效序号：{args[0]}。', parse_mode=None)
            return
    else:
        sid = state.agent.session_id
        if sid is None:
            await _send_text(update, '当前没有活跃会话。', parse_mode=None)
            return
    try:
        title = await state.agent.regenerate_title(sid)
        if title:
            await _send_text(update, f'新标题：{title}', parse_mode=None)
        else:
            await _send_text(update, '标题生成未配置或失败。', parse_mode=None)
    except Exception as exc:
        await _send_text(update, f'标题生成失败：{_mask_sensitive(str(exc))}', parse_mode=None)

async def cmd_del_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if not await _require_idle(update, state):
        return
    args = context.args
    if not args:
        await _send_text(update, '用法：/del_history #n [#n2 ...]', parse_mode=None)
        return
    sids: list[int] = []
    for a in args:
        for part in a.split('/'):
            sid = _resolve_session_num(part.strip())
            if sid is None:
                await _send_text(update, f'无效序号：{part}。先用 /history 查看。', parse_mode=None)
                return
            sids.append(sid)
    deleted = await state.store.delete_sessions(sids)
    if state.agent.session_id in sids:
        state.agent._session_id = None
        state.agent._history = []
    await _send_text(update, f'已删除 {deleted} 个会话。', parse_mode=None)
