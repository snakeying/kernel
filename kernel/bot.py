from __future__ import annotations
import asyncio
import logging
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from kernel.agent import Agent
from kernel.bot_cleanup import cleanup_old_files, periodic_cleanup
from kernel.bot_logging import setup_logging
from kernel.config import load_config
from kernel.memory.store import Store
from kernel.tg_commands_memory import cmd_forget, cmd_memory, cmd_remember
from kernel.tg_commands_sessions import cmd_del_history, cmd_help, cmd_history, cmd_new, cmd_resume, cmd_retitle, cmd_start
from kernel.tg_commands_settings import cmd_cancel, cmd_model, cmd_provider, cmd_status
from kernel.tg_common import BotState
from kernel.tg_message import handle_message
from kernel.voice.stt import STTClient
from kernel.voice.tts import TTSClient

log = logging.getLogger(__name__)

async def _post_init(app: Application) -> None:
    commands = [
        BotCommand('start', '欢迎 / 帮助'),
        BotCommand('help', '命令列表'),
        BotCommand('new', '新开会话'),
        BotCommand('history', '历史会话'),
        BotCommand('resume', '继续会话'),
        BotCommand('retitle', '重生标题'),
        BotCommand('del_history', '删除会话'),
        BotCommand('provider', '查看/切换 provider'),
        BotCommand('model', '查看/切换模型'),
        BotCommand('remember', '存入长期记忆'),
        BotCommand('memory', '查看长期记忆'),
        BotCommand('forget', '删除指定记忆'),
        BotCommand('cancel', '取消当前任务'),
        BotCommand('status', '查看状态'),
    ]
    await app.bot.set_my_commands(commands)

async def run_bot() -> None:
    config = load_config()
    setup_logging(config)
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
    cleanup_old_files(config.data_path / 'downloads', max_age_days=max_age_days)
    cleanup_old_files(config.data_path / 'cli_outputs', max_age_days=max_age_days)
    cleanup_old_files(config.data_path / 'voice_replies', max_age_days=max_age_days)

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
        cleanup_task = asyncio.create_task(periodic_cleanup(config.data_path, max_age_days=max_age_days), name='kernel_cleanup')
        log.info('Bot ready — polling …')

        stop_event = asyncio.Event()
        try:
            import signal

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop_event.set)
                except NotImplementedError:
                    pass
        except Exception:
            pass

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
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
