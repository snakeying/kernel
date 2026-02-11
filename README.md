<h1 align="center">
Kernel
</h1>

<p align="center">
  <strong>Personal AI Assistant on Telegram</strong>
</p>

<p align="center">
  <strong>Multi-provider LLM · Long-term Memory · Voice I/O · CLI Delegation</strong><br/>
  <em>More than a chatbot — it remembers everything and gets things done.</em>
</p>

<p align="center">
  <a href="README_CN.md">简体中文</a> | English
</p>

---

## 🤔 What is this?

Kernel is a **personal Telegram bot** that puts a powerful AI assistant in your pocket:

```
You (Telegram) ←→ Kernel ←→ LLM (Claude / OpenAI / DeepSeek / ...)
                    ↕              ↕
              Long-term Memory   Tools (CLI agents, MCP servers, ...)
```

Send a message, get an AI response. Send a voice note, get a voice reply. Ask it to edit code, it delegates to Claude Code or Codex. It remembers what you tell it — across sessions, forever.

---

## ✨ Core Features

| Feature | Description |
|---------|-------------|
| 💬 Multi-provider | Switch between Claude, OpenAI, DeepSeek and any OpenAI-compatible API at runtime |
| 🧠 Long-term memory | AI autonomously stores and recalls important info (preferences, facts, plans) |
| 🔧 CLI delegation | Delegates coding/file tasks to Claude Code or Codex CLI |
| 🌐 MCP tools | Connects to external MCP servers (web search, docs, etc.) |
| 🎙️ Voice I/O | Speech-to-text (Whisper) + text-to-speech (Edge TTS) — send voice, get voice back |
| 📎 File support | Send text/code files directly in chat for analysis |
| 🖼️ Image understanding | Send photos with captions for vision-capable models |
| 📝 Session management | Multiple conversations with auto-generated titles, resume anytime |
| 🎭 Customizable personality | Define your bot's character via `SOUL.md` |
| 🔒 Single-user | Locked to your Telegram ID — no one else can use it |

---

## 🚀 Quick Start

### 📋 Prerequisites

| Tool | Required |
|------|----------|
| Python | ≥ 3.11 |
| uv | Yes (package manager) |
| Telegram Bot Token | Yes (from [@BotFather](https://t.me/BotFather)) |
| LLM API Key | Yes (at least one provider) |
| Claude Code CLI | Optional (for CLI delegation) |

### ⚙️ Setup

```bash
# 1. Clone the repo
git clone <repo-url> && cd kernel

# 2. Create venv and install dependencies
uv venv .venv --python 3.11
uv sync

# 3. Copy config template and fill in your tokens
cp config.example.toml config.toml
# Edit config.toml with your Telegram token, user ID, and API keys

# 4. Run
uv run python -m kernel
```

### 🔑 Minimal config.toml

```toml
[telegram]
token = "YOUR_BOT_TOKEN"          # From @BotFather
allowed_user = 123456789          # Your Telegram user ID

[general]
default_provider = "anthropic"

[providers.anthropic]
type = "claude"
api_key = "sk-ant-..."
max_tokens = 16384
default_model = "claude-sonnet-4-5-20250929"
models = ["claude-sonnet-4-5-20250929"]
```

See `config.example.toml` for all options including OpenAI-compatible providers, STT/TTS, CLI agents, and MCP servers.

---

## 💬 Bot Commands

| Command | What it does |
|---------|-------------|
| `/new` | Start a new conversation |
| `/history` | List recent conversations |
| `/resume #n` | Continue a previous conversation |
| `/retitle [#n]` | Regenerate conversation title |
| `/del_history #n` | Delete a conversation |
| `/provider [name]` | View or switch LLM provider |
| `/model [name]` | View or switch model |
| `/remember <text>` | Save something to long-term memory |
| `/memory` | View all memories |
| `/forget #n` | Delete a memory |
| `/cancel` | Cancel the current task |
| `/status` | View bot status |

---

## 🧠 How Memory Works

Kernel has two layers of memory:

- **Session history** — recent messages kept in context (configurable rounds)
- **Long-term memory** — persistent SQLite store with full-text search (Chinese + English)

The AI decides on its own when to search or store memories. You can also manage them manually via `/remember`, `/memory`, and `/forget`.

---

## 🔧 CLI Delegation

When you ask Kernel to do something that requires file operations, code editing, or shell commands, it delegates to a CLI agent:

- **Claude Code** — for general coding tasks
- **Codex** — alternative CLI agent

The AI calls the `delegate_to_cli` tool automatically. Results are streamed back to you in Telegram.

---

## 🌐 MCP Integration

Kernel can connect to [MCP](https://modelcontextprotocol.io/) servers to extend its capabilities:

```toml
[[mcp.servers]]
name = "exa"
type = "http"
url = "https://mcp.exa.ai/mcp"
```

Tools from MCP servers are registered alongside built-in tools — the AI can use them seamlessly.

---

## 🎙️ Voice

Send a voice message → Kernel transcribes it (Whisper API) → AI responds → reply is synthesized as voice (Edge TTS, free).

Requires `[stt]` and `[tts]` sections in config. Text fallback is always available.

---

## 🎭 Personality

Drop a `SOUL.md` file next to your `config.toml` to customize the bot's character, tone, and rules. Kernel loads it automatically as the system prompt foundation.

---

## ❓ FAQ

<details>
<summary>Q: How do I get my Telegram user ID?</summary>

Message [@userinfobot](https://t.me/userinfobot) on Telegram. It will reply with your user ID.

</details>

<details>
<summary>Q: Can I use multiple LLM providers?</summary>

Yes. Define multiple providers in config.toml and switch between them at runtime with `/provider`.

</details>

<details>
<summary>Q: What file types can I send?</summary>

Text and code files (UTF-8): `.py`, `.js`, `.ts`, `.json`, `.yaml`, `.md`, `.sql`, `.html`, `.css`, and many more. Binary files (PDF, images, archives) are not supported as file attachments — but you can send images as photos.

</details>

<details>
<summary>Q: Where is data stored?</summary>

All runtime data (SQLite database, logs, temp files) is stored in the `data/` directory relative to your config file. Configurable via `general.data_dir`.

</details>

<details>
<summary>Q: Can I deploy this on a server?</summary>

Yes. Run it on any Linux server with Python 3.11+. Use a dedicated system user (not root) for security. The bot uses long-polling, so no inbound ports are needed.

</details>

---

## 📚 More Information

- [ARCHITECTURE.md](ARCHITECTURE.md) — Technical details, module structure, internal mechanisms
- `config.example.toml` — Full configuration reference with comments

---

## 📄 License

MIT
