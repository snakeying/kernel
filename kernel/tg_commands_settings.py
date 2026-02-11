from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from kernel.tg_common import BotState, _check_user, _require_idle, _send_text


async def cmd_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if context.args and (not await _require_idle(update, state)):
        return
    args = context.args
    if not args:
        current = state.agent.current_provider_name
        available = state.agent.available_providers
        lines = [f'当前 provider：<b>{current}</b>', '']
        for p in available:
            marker = ' ← 当前' if p == current else ''
            lines.append(f'  • {p}{marker}')
        await _send_text(update, '\n'.join(lines))
        return
    try:
        name = state.agent.switch_provider(args[0])
        model = state.agent.current_model
        await _send_text(update, f'已切换到 {name}，模型：{model}', parse_mode=None)
    except ValueError as exc:
        await _send_text(update, str(exc), parse_mode=None)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if context.args and (not await _require_idle(update, state)):
        return
    args = context.args
    if not args:
        prov = state.config.providers[state.agent.current_provider_name]
        current = state.agent.current_model
        lines = [f'当前模型：<b>{current}</b>', f'Provider：{state.agent.current_provider_name}', '']
        for m in prov.models:
            marker = ' ← 当前' if m == current else ''
            lines.append(f'  • {m}{marker}')
        await _send_text(update, '\n'.join(lines))
        return
    try:
        model = state.agent.switch_model(args[0])
        await _send_text(update, f'已切换到模型：{model}', parse_mode=None)
    except ValueError as exc:
        await _send_text(update, str(exc), parse_mode=None)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if state.busy:
        cli_name = state.agent.active_cli_name
        state.agent.cancel()
        if state._chat_task and (not state._chat_task.done()):
            state._chat_task.cancel()
        msg = '已取消。'
        if cli_name:
            msg = f'已取消（已终止 {cli_name}）。'
        await _send_text(update, msg, parse_mode=None)
    else:
        await _send_text(update, '当前没有进行中的任务。', parse_mode=None)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    agent = state.agent
    cli_status = agent.active_cli_name
    fts5 = 'yes' if state.store.fts5_available else 'no (LIKE fallback)'
    lines = [
        '<b>Kernel Status</b>',
        '',
        f'Provider: {agent.current_provider_name}',
        f'Model: {agent.current_model}',
        f"Session: {agent.session_id or 'none'}",
        f"Busy: {('yes' if state.busy else 'no')}",
        f"CLI: {cli_status or 'idle'}",
        f'FTS5: {fts5}',
        f"Time: {state.local_now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    await _send_text(update, '\n'.join(lines))

