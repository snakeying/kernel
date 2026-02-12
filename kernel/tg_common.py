from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from kernel.agent import Agent
from kernel.config import Config
from kernel.memory.store import Store
from kernel.render import split_plain_text, split_tg_message

if TYPE_CHECKING:
    from kernel.voice.stt import STTClient
    from kernel.voice.tts import TTSClient

_SENSITIVE_PATTERNS = [
    (re.compile(r'(sk-ant-[A-Za-z0-9]{4})[A-Za-z0-9-]+([A-Za-z0-9]{4})'), r'\1…\2'),
    (re.compile(r'(sk-[A-Za-z0-9]{4})[A-Za-z0-9-]+([A-Za-z0-9]{4})'), r'\1…\2'),
    (re.compile(r'(\d{8,12}):[A-Za-z0-9_-]{30,}'), '[TG_TOKEN_REDACTED]'),
]

def _mask_sensitive(text: str) -> str:
    for pattern, repl in _SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text

class MaskingFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        return _mask_sensitive(super().format(record))

_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_MAX_FILENAME_LEN = 120

def _sanitize_filename(filename: str) -> str:
    name = Path(filename).name
    name = _INVALID_FILENAME_CHARS_RE.sub('_', name)
    name = name.strip().strip('.')
    if not name:
        return 'file'
    if len(name) > _MAX_FILENAME_LEN:
        p = Path(name)
        suffix = p.suffix
        keep = _MAX_FILENAME_LEN - len(suffix)
        if keep < 1:
            return name[:_MAX_FILENAME_LEN]
        name = p.stem[:keep] + suffix
    return name

class BotState:

    def __init__(self, config: Config, agent: Agent, store: Store) -> None:
        self.config = config
        self.agent = agent
        self.store = store
        self.busy = False
        self.busy_notified = False
        self._chat_task: asyncio.Task | None = None
        self._chat_gate: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._chat_gate.put_nowait(None)
        self._tz = ZoneInfo(config.general.timezone)
        self.stt: STTClient | None = None
        self.tts: TTSClient | None = None
        self._last_message_was_voice = False

    def local_now(self) -> datetime:
        return datetime.now(self._tz)

    def format_dt(self, iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso).astimezone(self._tz)
            return dt.strftime('%Y-%m-%d')
        except Exception:
            return iso[:10]

def _check_user(update: Update, state: BotState) -> bool:
    user = update.effective_user
    if user is None or user.id != state.config.telegram.allowed_user:
        return False
    return True

async def _require_idle(update: Update, state: BotState) -> bool:
    if state.busy:
        await _send_text(update, '正在处理上一条消息，请稍后或 /cancel', parse_mode=None)
        return False
    return True

async def _send_text(update: Update, text: str, *, parse_mode: str | None = ParseMode.HTML) -> None:
    chat_id = update.effective_chat.id
    if parse_mode == ParseMode.HTML:
        chunks = split_tg_message(text)
    else:
        chunks = split_plain_text(text)
    for chunk in chunks:
        try:
            await update.get_bot().send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, disable_web_page_preview=True)
        except Exception as exc:
            if parse_mode == ParseMode.HTML and "can't parse entities" in str(exc).lower():
                await update.get_bot().send_message(chat_id=chat_id, text=chunk, parse_mode=None)
            else:
                raise

async def _send_typing(update: Update) -> None:
    chat_id = update.effective_chat.id
    await update.get_bot().send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
