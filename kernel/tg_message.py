from __future__ import annotations
import asyncio
import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Any
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from kernel.render import md_to_tg_html, split_tg_message
from kernel.tg_common import BotState, _check_user, _mask_sensitive, _sanitize_filename, _send_text, _send_typing
from kernel.tg_message_utils import _MAX_FILE_SIZE, _extract_file_text, _is_text_file, _to_tts_text

log = logging.getLogger(__name__)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: BotState = context.bot_data['state']
    if not _check_user(update, state):
        return
    try:
        state._chat_gate.get_nowait()
    except asyncio.QueueEmpty:
        if not state.busy_notified:
            state.busy_notified = True
            await _send_text(update, '正在处理上一条消息，请稍后或 /cancel', parse_mode=None)
        return
    state.busy = True
    state.busy_notified = False
    state._chat_task = asyncio.current_task()
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
            try:
                await file.download_to_drive(str(local_path))
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
            if len(img_bytes) > _MAX_FILE_SIZE:
                await _send_text(update, '图片超过 20MB，无法处理。', parse_mode=None)
                return
            b64 = base64.b64encode(img_bytes).decode('ascii')
            content_blocks.append(ImageContent(media_type='image/jpeg', data=b64))
            if msg.caption:
                content_blocks.append(TextContent(text=msg.caption))
        elif msg and msg.document:
            doc = msg.document
            filename = _sanitize_filename(doc.file_name or 'unknown')
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
            try:
                await file.download_to_drive(str(local_path))
                text = await _extract_file_text(local_path)
            except UnicodeDecodeError:
                await _send_text(update, f'文件 {filename} 不是有效的 UTF-8 文本，无法处理。', parse_mode=None)
                return
            except Exception as exc:
                log.warning('File read failed: %s', exc)
                await _send_text(update, f'读取文件 {filename} 失败：{exc}', parse_mode=None)
                return
            finally:
                local_path.unlink(missing_ok=True)
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
            if typing_task:
                typing_task.cancel()

        full_text = ''.join(text_parts)
        if not full_text.strip():
            return
        await state.agent.maybe_generate_title()

        if state._last_message_was_voice and state.tts:
            state._last_message_was_voice = False
            voice_path: Path | None = None
            try:
                speak_text = _to_tts_text(full_text)
                if not speak_text:
                    speak_text = '代码略'
                voice_dir = state.config.data_path / 'voice_replies'
                voice_dir.mkdir(parents=True, exist_ok=True)
                voice_path = voice_dir / f'{msg.message_id}.ogg'
                await state.tts.synthesize(speak_text, voice_path)
                chat_id = update.effective_chat.id
                with open(voice_path, 'rb') as vf:
                    await update.get_bot().send_voice(chat_id=chat_id, voice=vf)
            except Exception as exc:
                log.warning('TTS failed, falling back to text: %s', exc)
            finally:
                if voice_path:
                    voice_path.unlink(missing_ok=True)

        if msg and (not msg.voice):
            state._last_message_was_voice = False
        try:
            html_text = md_to_tg_html(full_text)
            await _send_text(update, html_text, parse_mode=ParseMode.HTML)
        except Exception:
            log.warning('HTML render failed, falling back to plain text', exc_info=True)
            for chunk_text in split_tg_message(full_text):
                await _send_text(update, chunk_text, parse_mode=None)
    finally:
        state._chat_task = None
        state.busy = False
        state._chat_gate.put_nowait(None)
