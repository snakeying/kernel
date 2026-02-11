"""Telegram Bot — message handling, commands, and output formatting.

Handles text/image input, session commands, provider/model switching,
placeholder + final HTML output, busy guard, whitelist, and /cancel.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import traceback
from datetime import datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from kernel.agent import Agent
from kernel.config import Config, load_config
from kernel.memory.store import Store
from kernel.render import md_to_tg_html, split_tg_message

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sensitive-info masking
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS = [
    (re.compile(r"(sk-ant-[A-Za-z0-9]{4})[A-Za-z0-9-]+([A-Za-z0-9]{4})"), r"\1…\2"),
    (re.compile(r"(sk-[A-Za-z0-9]{4})[A-Za-z0-9-]+([A-Za-z0-9]{4})"), r"\1…\2"),
    (re.compile(r"(\d{8,12}):\w{30,}"), "[TG_TOKEN_REDACTED]"),
]


def _mask_sensitive(text: str) -> str:
    for pattern, repl in _SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Bot state
# ---------------------------------------------------------------------------


class BotState:
    """Shared mutable state for the bot."""

    def __init__(self, config: Config, agent: Agent, store: Store) -> None:
        self.config = config
        self.agent = agent
        self.store = store
        self.busy = False
        self.busy_notified = False
        self._chat_task: asyncio.Task | None = None  # running chat task for /cancel
        self._tz = ZoneInfo(config.general.timezone)

    def local_now(self) -> datetime:
        return datetime.now(self._tz)

    def format_dt(self, iso: str) -> str:
        """Format ISO timestamp to local date string."""
        try:
            dt = datetime.fromisoformat(iso).astimezone(self._tz)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return iso[:10]


# ---------------------------------------------------------------------------
# Whitelist guard
# ---------------------------------------------------------------------------


def _check_user(update: Update, state: BotState) -> bool:
    user = update.effective_user
    if user is None or user.id != state.config.telegram.allowed_user:
        return False
    return True


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------


async def _send_text(
    update: Update,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
) -> None:
    """Send text, splitting if needed.  Falls back to plain text on parse error."""
    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    chunks = split_tg_message(text) if parse_mode == ParseMode.HTML else [text]
    for chunk in chunks:
        try:
            await update.get_bot().send_message(
                chat_id=chat_id, text=chunk, parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            if parse_mode == ParseMode.HTML and "can't parse entities" in str(exc).lower():
                # Fallback to plain text
                await update.get_bot().send_message(
                    chat_id=chat_id, text=chunk, parse_mode=None,
                )
            else:
                raise


async def _send_typing(update: Update) -> None:
    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    await update.get_bot().send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)


# ---------------------------------------------------------------------------
# History # mapping
# ---------------------------------------------------------------------------

# /history shows #1..#N.  We map # indices to real session IDs per invocation.
_history_map: dict[int, int] = {}  # #n → session_id


def _build_history_map(sessions: list[dict[str, Any]]) -> dict[int, int]:
    global _history_map
    _history_map = {i + 1: s["id"] for i, s in enumerate(sessions)}
    return _history_map


def _resolve_session_num(num_str: str) -> int | None:
    """Resolve ``#<n>`` to a session ID via the current history map."""
    try:
        n = int(num_str.lstrip("#"))
    except ValueError:
        return None
    return _history_map.get(n)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    await _send_text(update, (
        "<b>Kernel</b> — 你的个人 AI 助手\n\n"
        "直接发消息开始聊天，或输入 /help 查看命令列表。"
    ))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    await _send_text(update, (
        "<b>命令列表</b>\n\n"
        "/new — 新开会话\n"
        "/history — 历史会话\n"
        "/resume #n — 继续会话\n"
        "/retitle [#n] — 重生标题\n"
        "/del_history #n — 删除会话\n"
        "/provider [name] — 查看/切换 provider\n"
        "/model [name] — 查看/切换模型\n"
        "/cancel — 取消当前任务\n"
        "/status — 查看状态"
    ))


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    sid = await state.agent.new_session()
    await _send_text(update, f"新会话已创建 (#{sid})")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    sessions = await state.store.list_sessions(limit=20)
    if not sessions:
        await _send_text(update, "暂无历史会话。")
        return
    hmap = _build_history_map(sessions)
    lines: list[str] = []
    for n, sid in sorted(hmap.items()):
        s = next(s for s in sessions if s["id"] == sid)
        title = s["title"] or "无标题"
        date = state.format_dt(s["updated_at"])
        current = " ← 当前" if sid == state.agent.session_id else ""
        lines.append(f"<b>#{n}</b> {date} {title}{current}")
    await _send_text(update, "\n".join(lines))


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    args = context.args
    if not args:
        await _send_text(update, "用法：/resume #n\n先用 /history 查看序号。", parse_mode=None)
        return
    sid = _resolve_session_num(args[0])
    if sid is None:
        await _send_text(update, f"无效序号：{args[0]}。先用 /history 查看。", parse_mode=None)
        return
    try:
        await state.agent.resume_session(sid)
        session = await state.store.get_session(sid)
        title = session["title"] if session else "无标题"
        count = await state.store.count_messages(sid)
        await _send_text(update, f"已恢复会话：{title}（{count} 条消息）", parse_mode=None)
    except ValueError as exc:
        await _send_text(update, str(exc), parse_mode=None)


async def cmd_retitle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    args = context.args
    if args:
        sid = _resolve_session_num(args[0])
        if sid is None:
            await _send_text(update, f"无效序号：{args[0]}。", parse_mode=None)
            return
    else:
        sid = state.agent.session_id
        if sid is None:
            await _send_text(update, "当前没有活跃会话。", parse_mode=None)
            return
    try:
        title = await state.agent.regenerate_title(sid)
        if title:
            await _send_text(update, f"新标题：{title}", parse_mode=None)
        else:
            await _send_text(update, "标题生成未配置或失败。", parse_mode=None)
    except Exception as exc:
        await _send_text(update, f"标题生成失败：{_mask_sensitive(str(exc))}", parse_mode=None)


async def cmd_del_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    args = context.args
    if not args:
        await _send_text(update, "用法：/del_history #n [#n2 ...]", parse_mode=None)
        return
    sids: list[int] = []
    for a in args:
        for part in a.split("/"):
            sid = _resolve_session_num(part.strip())
            if sid is None:
                await _send_text(update, f"无效序号：{part}。先用 /history 查看。", parse_mode=None)
                return
            sids.append(sid)
    deleted = await state.store.delete_sessions(sids)
    # If current session was deleted, clear it
    if state.agent.session_id in sids:
        state.agent._session_id = None
        state.agent._history = []
    await _send_text(update, f"已删除 {deleted} 个会话。", parse_mode=None)


async def cmd_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    args = context.args
    if not args:
        current = state.agent.current_provider_name
        available = state.agent.available_providers
        lines = [f"当前 provider：<b>{current}</b>", ""]
        for p in available:
            marker = " ← 当前" if p == current else ""
            lines.append(f"  • {p}{marker}")
        await _send_text(update, "\n".join(lines))
        return
    try:
        name = state.agent.switch_provider(args[0])
        model = state.agent.current_model
        await _send_text(update, f"已切换到 {name}，模型：{model}", parse_mode=None)
    except ValueError as exc:
        await _send_text(update, str(exc), parse_mode=None)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    args = context.args
    if not args:
        prov = state.config.providers[state.agent.current_provider_name]
        current = state.agent.current_model
        lines = [f"当前模型：<b>{current}</b>", f"Provider：{state.agent.current_provider_name}", ""]
        for m in prov.models:
            marker = " ← 当前" if m == current else ""
            lines.append(f"  • {m}{marker}")
        await _send_text(update, "\n".join(lines))
        return
    try:
        model = state.agent.switch_model(args[0])
        await _send_text(update, f"已切换到模型：{model}", parse_mode=None)
    except ValueError as exc:
        await _send_text(update, str(exc), parse_mode=None)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    if state.busy:
        cli_name = state.agent.active_cli_name
        state.agent.cancel()  # also kills CLI subprocess
        if state._chat_task and not state._chat_task.done():
            state._chat_task.cancel()
        msg = "已取消。"
        if cli_name:
            msg = f"已取消（已终止 {cli_name}）。"
        await _send_text(update, msg, parse_mode=None)
    else:
        await _send_text(update, "当前没有进行中的任务。", parse_mode=None)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return
    agent = state.agent
    cli_status = agent.active_cli_name
    lines = [
        "<b>Kernel Status</b>",
        "",
        f"Provider: {agent.current_provider_name}",
        f"Model: {agent.current_model}",
        f"Session: {agent.session_id or 'none'}",
        f"Busy: {'yes' if state.busy else 'no'}",
        f"CLI: {cli_status or 'idle'}",
        f"Time: {state.local_now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    await _send_text(update, "\n".join(lines))


# ---------------------------------------------------------------------------
# Message handler — text & images
# ---------------------------------------------------------------------------


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data["state"]
    if not _check_user(update, state):
        return

    if state.busy:
        if not state.busy_notified:
            state.busy_notified = True
            await _send_text(
                update,
                "正在处理上一条消息，请稍后或 /cancel",
                parse_mode=None,
            )
        return

    state.busy = True
    state.busy_notified = False

    try:
        from kernel.models.base import ImageContent, TextContent

        content_blocks: list[Any] = []
        msg = update.message

        # Image handling
        if msg and msg.photo:
            photo = msg.photo[-1]  # highest resolution
            file = await photo.get_file()
            buf = BytesIO()
            await file.download_to_memory(buf)
            img_bytes = buf.getvalue()

            if len(img_bytes) > 20 * 1024 * 1024:
                await _send_text(update, "图片超过 20MB，无法处理。", parse_mode=None)
                return

            b64 = base64.b64encode(img_bytes).decode("ascii")
            content_blocks.append(ImageContent(media_type="image/jpeg", data=b64))
            if msg.caption:
                content_blocks.append(TextContent(text=msg.caption))

        elif msg and msg.text:
            content_blocks.append(TextContent(text=msg.text))

        if not content_blocks:
            return

        user_content = content_blocks if len(content_blocks) > 1 else (
            content_blocks[0].text if isinstance(content_blocks[0], TextContent) else content_blocks
        )

        # Send typing indicator
        await _send_typing(update)

        # Collect streaming response
        text_parts: list[str] = []
        typing_task: asyncio.Task | None = None

        # Periodic typing indicator
        async def _keep_typing() -> None:
            try:
                while True:
                    await asyncio.sleep(4)
                    await _send_typing(update)
            except asyncio.CancelledError:
                pass

        typing_task = asyncio.create_task(_keep_typing())
        state._chat_task = asyncio.current_task()

        try:
            async for chunk in state.agent.chat(user_content):
                if chunk.text:
                    text_parts.append(chunk.text)
                # Tool execution notification — send waiting hint for CLI
                elif chunk.tool_name and not chunk.text:
                    tool = chunk.tool_name
                    if tool == "delegate_to_cli":
                        await _send_text(
                            update, "⏳ 正在执行任务…", parse_mode=None,
                        )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            error_msg = _mask_sensitive(str(exc))
            log.exception("LLM error")
            await _send_text(update, f"Error: {error_msg}", parse_mode=None)
            return
        finally:
            state._chat_task = None
            if typing_task:
                typing_task.cancel()

        full_text = "".join(text_parts)
        if not full_text.strip():
            return

        # Title generation (fire-and-forget, after first exchange)
        await state.agent.maybe_generate_title()

        # Render Markdown → TG HTML and send
        try:
            html = md_to_tg_html(full_text)
            await _send_text(update, html, parse_mode=ParseMode.HTML)
        except Exception:
            # Fallback: plain text
            log.warning("HTML render failed, falling back to plain text", exc_info=True)
            for chunk_text in split_tg_message(full_text):
                await _send_text(update, chunk_text, parse_mode=None)

    finally:
        state.busy = False


# ---------------------------------------------------------------------------
# Application setup & run
# ---------------------------------------------------------------------------


async def _post_init(app: Application) -> None:
    """Set bot commands for the menu."""
    commands = [
        BotCommand("start", "欢迎 / 帮助"),
        BotCommand("help", "命令列表"),
        BotCommand("new", "新开会话"),
        BotCommand("history", "历史会话"),
        BotCommand("resume", "继续会话"),
        BotCommand("retitle", "重生标题"),
        BotCommand("del_history", "删除会话"),
        BotCommand("provider", "查看/切换 provider"),
        BotCommand("model", "查看/切换模型"),
        BotCommand("cancel", "取消当前任务"),
        BotCommand("status", "查看状态"),
    ]
    await app.bot.set_my_commands(commands)


def _setup_logging(config: Config) -> None:
    """Add file handler for persistent logging."""
    log_dir = config.data_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(
        log_dir / "kernel.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)


async def run_bot() -> None:
    """Load config, initialise store/agent, and start polling."""
    config = load_config()
    _setup_logging(config)

    log.info("Starting Kernel …")

    # Init store
    store = Store(config.data_path / "kernel.db")
    await store.init()

    # Init agent
    agent = Agent(config, store)

    # Restore last provider/model from DB
    await agent.restore_provider_model()

    log.info("Provider: %s | Model: %s", agent.current_provider_name, agent.current_model)

    # Init MCP connections (non-blocking — failures logged)
    await agent.init_mcp()

    # Ensure data dirs
    (config.data_path / "cli_outputs").mkdir(parents=True, exist_ok=True)

    state = BotState(config, agent, store)

    # Build application (concurrent so /cancel and busy guard work during streaming)
    app = (
        Application.builder()
        .token(config.telegram.token)
        .post_init(_post_init)
        .concurrent_updates(True)
        .build()
    )
    app.bot_data["state"] = state

    # Register command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("retitle", cmd_retitle))
    app.add_handler(CommandHandler("del_history", cmd_del_history))
    app.add_handler(CommandHandler("provider", cmd_provider))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))

    # Message handler — text and photos (private chat only)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
        handle_message,
    ))

    log.info("Bot ready — polling …")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]

    # Run until stopped
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
    finally:
        log.info("Shutting down …")
        # Close store first while event loop is still alive
        try:
            await store.close()
        except BaseException:
            pass
        try:
            await agent.close()
        except BaseException:
            pass
        try:
            await app.updater.stop()  # type: ignore[union-attr]
            await app.stop()
            await app.shutdown()
        except BaseException:
            pass
