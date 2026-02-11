from __future__ import annotations
import asyncio
import base64
import logging
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from kernel.agent import Agent
from kernel.config import Config, load_config
from kernel.memory.store import Store
from kernel.render import md_to_tg_html, split_tg_message
from kernel.voice.stt import STTClient
from kernel.voice.tts import TTSClient
log = logging.getLogger(__name__)
_SENSITIVE_PATTERNS = [(re.compile('(sk-ant-[A-Za-z0-9]{4})[A-Za-z0-9-]+([A-Za-z0-9]{4})'), '\\1…\\2'), (re.compile('(sk-[A-Za-z0-9]{4})[A-Za-z0-9-]+([A-Za-z0-9]{4})'), '\\1…\\2'), (re.compile('(\\d{8,12}):\\w{30,}'), '[TG_TOKEN_REDACTED]')]

def _mask_sensitive(text: str) -> str:
    for pattern, repl in _SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text
_MAX_FILE_SIZE = 20 * 1024 * 1024
_MAX_TEXT_CHARS = 50000
_TEXT_EXTENSIONS: set[str] = {'.txt', '.md', '.markdown', '.rst', '.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.html', '.htm', '.css', '.scss', '.less', '.svg', '.sh', '.bash', '.zsh', '.bat', '.cmd', '.ps1', '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.java', '.kt', '.kts', '.scala', '.groovy', '.go', '.rs', '.rb', '.php', '.pl', '.lua', '.r', '.R', '.jl', '.swift', '.m', '.mm', '.sql', '.graphql', '.gql', '.xml', '.csv', '.tsv', '.log', '.env', '.gitignore', '.dockerignore', '.dockerfile', '.makefile', '.tf', '.hcl', '.vue', '.svelte'}
_UNSUPPORTED_EXTENSIONS: set[str] = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf', '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar', '.exe', '.dll', '.so', '.dylib', '.bin', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.ico', '.tiff', '.mp3', '.mp4', '.avi', '.mkv', '.wav', '.flac', '.ogg'}

def _is_text_file(filename: str) -> bool | None:
    ext = Path(filename).suffix.lower()
    if not ext:
        basename = Path(filename).name.lower()
        if basename in ('makefile', 'dockerfile', 'vagrantfile', 'gemfile', 'rakefile', 'procfile'):
            return True
        return None
    if ext in _TEXT_EXTENSIONS:
        return True
    if ext in _UNSUPPORTED_EXTENSIONS:
        return False
    return None

async def _extract_file_text(file_path: Path) -> str:
    text = file_path.read_text(encoding='utf-8')
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + f'\n\n[… 截断，共 {len(text)} 字符]'
    return text

class BotState:

    def __init__(self, config: Config, agent: Agent, store: Store) -> None:
        self.config = config
        self.agent = agent
        self.store = store
        self.busy = False
        self.busy_notified = False
        self._chat_task: asyncio.Task | None = None
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

async def _send_text(update: Update, text: str, *, parse_mode: str | None=ParseMode.HTML) -> None:
    chat_id = update.effective_chat.id
    chunks = split_tg_message(text) if parse_mode == ParseMode.HTML else [text]
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
        title = s['title'] or '无标题'
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
        lines.append(f"<b>#{n}</b> {date} {m['text']}")
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

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    agent = state.agent
    cli_status = agent.active_cli_name
    fts5 = 'yes' if state.store.fts5_available else 'no (LIKE fallback)'
    lines = ['<b>Kernel Status</b>', '', f'Provider: {agent.current_provider_name}', f'Model: {agent.current_model}', f"Session: {agent.session_id or 'none'}", f"Busy: {('yes' if state.busy else 'no')}", f"CLI: {cli_status or 'idle'}", f'FTS5: {fts5}', f"Time: {state.local_now().strftime('%Y-%m-%d %H:%M:%S')}"]
    await _send_text(update, '\n'.join(lines))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    if state.busy:
        if not state.busy_notified:
            state.busy_notified = True
            await _send_text(update, '正在处理上一条消息，请稍后或 /cancel', parse_mode=None)
        return
    state.busy = True
    state.busy_notified = False
    try:
        from kernel.models.base import ImageContent, TextContent
        content_blocks: list[Any] = []
        msg = update.message
        if msg and msg.voice:
            if not state.stt:
                await _send_text(update, '语音消息功能未配置。请在 config.toml 中添加 [stt] 配置。', parse_mode=None)
                return
            voice = msg.voice
            if voice.file_size and voice.file_size > _MAX_FILE_SIZE:
                await _send_text(update, '语音文件超过 20MB，无法处理。', parse_mode=None)
                return
            downloads_dir = state.config.data_path / 'downloads'
            downloads_dir.mkdir(parents=True, exist_ok=True)
            file = await voice.get_file()
            local_path = downloads_dir / f'{file.file_unique_id}.ogg'
            await file.download_to_drive(str(local_path))
            try:
                text = await state.stt.transcribe(local_path)
                log.info('STT transcribed: %s', text[:100])
            except Exception as exc:
                log.exception('STT failed')
                await _send_text(update, f'语音识别失败：{exc}', parse_mode=None)
                return
            finally:
                local_path.unlink(missing_ok=True)
            if not text.strip():
                await _send_text(update, '未识别到语音内容。', parse_mode=None)
                return
            state._last_message_was_voice = True
            content_blocks.append(TextContent(text=f'[语音: {text}]'))
        elif msg and msg.photo:
            photo = msg.photo[-1]
            file = await photo.get_file()
            buf = BytesIO()
            await file.download_to_memory(buf)
            img_bytes = buf.getvalue()
            if len(img_bytes) > 20 * 1024 * 1024:
                await _send_text(update, '图片超过 20MB，无法处理。', parse_mode=None)
                return
            b64 = base64.b64encode(img_bytes).decode('ascii')
            content_blocks.append(ImageContent(media_type='image/jpeg', data=b64))
            if msg.caption:
                content_blocks.append(TextContent(text=msg.caption))
        elif msg and msg.document:
            doc = msg.document
            filename = doc.file_name or 'unknown'
            supported = _is_text_file(filename)
            if supported is False:
                ext = Path(filename).suffix.lower()
                await _send_text(update, f'不支持的文件类型：{ext}\n目前仅支持文本和代码文件（UTF-8）。', parse_mode=None)
                return
            if supported is None:
                await _send_text(update, f'无法识别的文件类型：{filename}\n目前仅支持文本和代码文件（UTF-8）。', parse_mode=None)
                return
            if doc.file_size and doc.file_size > _MAX_FILE_SIZE:
                await _send_text(update, '文件超过 20MB，无法处理。', parse_mode=None)
                return
            downloads_dir = state.config.data_path / 'downloads'
            downloads_dir.mkdir(parents=True, exist_ok=True)
            file = await doc.get_file()
            local_path = downloads_dir / f'{file.file_unique_id}_{filename}'
            await file.download_to_drive(str(local_path))
            try:
                text = await _extract_file_text(local_path)
            except UnicodeDecodeError:
                await _send_text(update, f'文件 {filename} 不是有效的 UTF-8 文本，无法处理。', parse_mode=None)
                return
            except Exception as exc:
                log.warning('File read failed: %s', exc)
                await _send_text(update, f'读取文件 {filename} 失败：{exc}', parse_mode=None)
                return
            content_blocks.append(TextContent(text=f'[文件: {filename}]\n```\n{text}\n```'))
            if msg.caption:
                content_blocks.append(TextContent(text=msg.caption))
        elif msg and msg.text:
            content_blocks.append(TextContent(text=msg.text))
        if not content_blocks:
            return
        user_content = content_blocks
        await _send_typing(update)
        text_parts: list[str] = []
        typing_task: asyncio.Task | None = None

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
                elif chunk.tool_name and (not chunk.text):
                    tool = chunk.tool_name
                    if tool == 'delegate_to_cli':
                        await _send_text(update, '⏳ 正在执行任务…', parse_mode=None)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            error_msg = _mask_sensitive(str(exc))
            log.exception('LLM error')
            await _send_text(update, f'Error: {error_msg}', parse_mode=None)
            return
        finally:
            state._chat_task = None
            if typing_task:
                typing_task.cancel()
        full_text = ''.join(text_parts)
        if not full_text.strip():
            return
        await state.agent.maybe_generate_title()
        if state._last_message_was_voice and state.tts:
            state._last_message_was_voice = False
            try:
                voice_dir = state.config.data_path / 'voice_replies'
                voice_dir.mkdir(parents=True, exist_ok=True)
                voice_path = voice_dir / f'{msg.message_id}.ogg'
                await state.tts.synthesize(full_text, voice_path)
                chat_id = update.effective_chat.id
                with open(voice_path, 'rb') as vf:
                    await update.get_bot().send_voice(chat_id=chat_id, voice=vf)
                voice_path.unlink(missing_ok=True)
                return
            except Exception as exc:
                log.warning('TTS failed, falling back to text: %s', exc)
        if msg and (not msg.voice):
            state._last_message_was_voice = False
        try:
            html = md_to_tg_html(full_text)
            await _send_text(update, html, parse_mode=ParseMode.HTML)
        except Exception:
            log.warning('HTML render failed, falling back to plain text', exc_info=True)
            for chunk_text in split_tg_message(full_text):
                await _send_text(update, chunk_text, parse_mode=None)
    finally:
        state.busy = False

async def _post_init(app: Application) -> None:
    commands = [BotCommand('start', '欢迎 / 帮助'), BotCommand('help', '命令列表'), BotCommand('new', '新开会话'), BotCommand('history', '历史会话'), BotCommand('resume', '继续会话'), BotCommand('retitle', '重生标题'), BotCommand('del_history', '删除会话'), BotCommand('provider', '查看/切换 provider'), BotCommand('model', '查看/切换模型'), BotCommand('remember', '存入长期记忆'), BotCommand('memory', '查看长期记忆'), BotCommand('forget', '删除指定记忆'), BotCommand('cancel', '取消当前任务'), BotCommand('status', '查看状态')]
    await app.bot.set_my_commands(commands)

def _setup_logging(config: Config) -> None:
    log_dir = config.data_path / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(log_dir / 'kernel.log', maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logging.getLogger().addHandler(fh)

def _cleanup_old_files(dir_path: Path, *, max_age_days: int) -> int:
    if not dir_path.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    for p in dir_path.iterdir():
        try:
            if not p.is_file():
                continue
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            log.debug('Cleanup failed for %s', p, exc_info=True)
    return deleted

async def _periodic_cleanup(data_path: Path, *, max_age_days: int, interval_hours: int=24) -> None:
    dirs = ('downloads', 'cli_outputs', 'voice_replies')
    while True:
        try:
            total = 0
            for d in dirs:
                total += _cleanup_old_files(data_path / d, max_age_days=max_age_days)
            if total:
                log.info('Cleanup: removed %d old files (>%dd)', total, max_age_days)
        except Exception:
            log.debug('Cleanup loop failed', exc_info=True)
        await asyncio.sleep(interval_hours * 3600)

async def run_bot() -> None:
    config = load_config()
    _setup_logging(config)
    log.info('Starting Kernel …')
    import static_ffmpeg
    static_ffmpeg.add_paths()
    store = Store(config.data_path / 'kernel.db')
    await store.init()
    agent = Agent(config, store)
    await agent.restore_provider_model()
    log.info('Provider: %s | Model: %s', agent.current_provider_name, agent.current_model)
    await agent.init_mcp()
    (config.data_path / 'cli_outputs').mkdir(parents=True, exist_ok=True)
    (config.data_path / 'downloads').mkdir(parents=True, exist_ok=True)
    (config.data_path / 'voice_replies').mkdir(parents=True, exist_ok=True)
    max_age_days = 7
    _cleanup_old_files(config.data_path / 'downloads', max_age_days=max_age_days)
    _cleanup_old_files(config.data_path / 'cli_outputs', max_age_days=max_age_days)
    _cleanup_old_files(config.data_path / 'voice_replies', max_age_days=max_age_days)
    cleanup_task: asyncio.Task | None = None
    stt_client = None
    if config.stt:
        stt_client = STTClient(api_base=config.stt.api_base, api_key=config.stt.api_key, model=config.stt.model, headers=config.stt.headers)
        log.info('STT enabled: %s', config.stt.model)
    tts_client = None
    if config.tts:
        tts_client = TTSClient(voice=config.tts.voice)
        log.info('TTS enabled: %s', config.tts.voice)
    state = BotState(config, agent, store)
    state.stt = stt_client
    state.tts = tts_client
    app = Application.builder().token(config.telegram.token).post_init(_post_init).concurrent_updates(True).build()
    app.bot_data['state'] = state
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('help', cmd_help))
    app.add_handler(CommandHandler('new', cmd_new))
    app.add_handler(CommandHandler('history', cmd_history))
    app.add_handler(CommandHandler('resume', cmd_resume))
    app.add_handler(CommandHandler('retitle', cmd_retitle))
    app.add_handler(CommandHandler('del_history', cmd_del_history))
    app.add_handler(CommandHandler('provider', cmd_provider))
    app.add_handler(CommandHandler('model', cmd_model))
    app.add_handler(CommandHandler('cancel', cmd_cancel))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(CommandHandler('remember', cmd_remember))
    app.add_handler(CommandHandler('memory', cmd_memory))
    app.add_handler(CommandHandler('forget', cmd_forget))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VOICE) & ~filters.COMMAND, handle_message))
    try:
        cleanup_task = asyncio.create_task(_periodic_cleanup(config.data_path, max_age_days=max_age_days), name='kernel_cleanup')
        log.info('Bot ready — polling …')
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
    finally:
        log.info('Shutting down …')
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        try:
            await store.close()
        except BaseException:
            pass
        try:
            await agent.close()
        except BaseException:
            pass
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except BaseException:
            pass
