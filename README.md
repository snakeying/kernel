# Kernel

Personal AI assistant on Telegram.

## Quickstart

```bash
# 1. Clone & enter
git clone <repo-url> && cd kernel

# 2. Create venv
uv venv .venv --python 3.11

# 3. Install dependencies
uv sync

# 4. Copy config and fill in your tokens
cp config.example.toml config.toml
# Edit config.toml: set telegram.token, telegram.allowed_user, provider api_keys

# 5. Run
uv run -m kernel
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome / help |
| `/help` | Show commands |
| `/new` | Start new session |
| `/history` | List past sessions |
| `/resume #n` | Resume a session |
| `/retitle [#n]` | Regenerate session title |
| `/del_history #n` | Delete session(s) |
| `/provider [name]` | View/switch provider |
| `/model [name]` | View/switch model |
| `/cancel` | Cancel current task |
| `/status` | Show current status |
