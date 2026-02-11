# Voice (STT + TTS) Support Summary

Kernel supports Telegram voice messages via:

- **STT**: Whisper-compatible API (OpenAI-compatible) → text
- **TTS**: `edge-tts` → mp3 → `static-ffmpeg` → ogg/opus (Telegram voice)

## Dependencies (pyproject.toml)

- `openai` (already used by OpenAI-compat providers)
- `edge-tts` (free Microsoft voices)
- `static-ffmpeg` (auto-fetches ffmpeg binaries)

## Files

- `kernel/voice/stt.py` — `STTClient` wraps `AsyncOpenAI().audio.transcriptions.create()`
- `kernel/voice/tts.py` — `TTSClient` uses `edge_tts` then converts with `static_ffmpeg`
- `kernel/bot.py` — voice download → STT → chat → optional TTS reply
- `kernel/memory/store.py` — history slimming: `[语音: ...]` → `[语音已处理]`

## Config

```toml
[stt]
api_base = "https://api.openai.com/v1"
api_key = "sk-..."                        # env: KERNEL_STT_API_KEY
model = "whisper-1"

[tts]
voice = "zh-CN-XiaoxiaoNeural"           # edge-tts voice name
```

## Behavior

- User sends voice → bot downloads to `data/downloads/*.ogg` → STT to text → agent runs.
- If `[tts]` is enabled, **voice input gets a voice reply** (fallback to text on TTS failure).

## Notes

- `static-ffmpeg` fetches ffmpeg binaries on first use (no system ffmpeg required in the common case).
