<h1 align="center">
Kernel
</h1>

<p align="center">
  <strong>Telegram 上的个人 AI 助手</strong>
</p>

<p align="center">
  <strong>多模型 LLM · 长期记忆 · 语音交互 · CLI 委派</strong><br/>
  <em>不只是聊天机器人 —— 它记住一切，执行任何任务。</em>
</p>

<p align="center">
  简体中文 | <a href="README.md">English</a>
</p>

---

## 🤔 这是什么？

Kernel 是一个**个人 Telegram 机器人**，把强大的 AI 助手装进你的口袋：

```
你 (Telegram) ←→ Kernel ←→ LLM (Claude / OpenAI / DeepSeek / ...)
                    ↕              ↕
                长期记忆        工具 (CLI 代理, MCP 服务器, ...)
```

发条消息，得到 AI 回复。发条语音，得到语音回复。让它改代码，它会委派给 Claude Code 或 Codex。它会记住你告诉它的一切 —— 跨会话，永久保存。

---

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 💬 多模型切换 | 运行时在 Claude、OpenAI、DeepSeek 及任何 OpenAI 兼容 API 之间自由切换 |
| 🧠 长期记忆 | AI 自主存储和回忆重要信息（偏好、事实、计划） |
| 🔧 CLI 委派 | 将编码/文件任务委派给 Claude Code 或 Codex CLI |
| 🌐 MCP 工具 | 连接外部 MCP 服务器（网页搜索、文档查询等） |
| 🎙️ 语音交互 | 语音转文字 (Whisper) + 文字转语音 (Edge TTS) —— 发语音，回语音 |
| 📎 文件支持 | 直接在聊天中发送文本/代码文件进行分析 |
| 🖼️ 图片理解 | 发送照片并附带说明，支持视觉模型 |
| 📝 会话管理 | 多会话、自动生成标题、随时恢复 |
| 🎭 自定义人格 | 通过 `SOUL.md` 定义机器人的性格 |
| 🔒 单用户 | 锁定你的 Telegram ID —— 其他人无法使用 |

---

## 🚀 快速开始

### 📋 前置要求

| 工具 | 是否必需 |
|------|----------|
| Python | ≥ 3.11 |
| uv | 是（包管理器） |
| Telegram Bot Token | 是（从 [@BotFather](https://t.me/BotFather) 获取） |
| LLM API Key | 是（至少一个提供商） |
| Claude Code CLI | 可选（用于 CLI 委派） |

### ⚙️ 安装

```bash
# 1. 克隆仓库
git clone <repo-url> && cd kernel

# 2. 创建虚拟环境并安装依赖
uv venv .venv --python 3.11
uv sync

# 3. 复制配置模板并填入你的 token
cp config.example.toml config.toml
# 编辑 config.toml，填入 Telegram token、用户 ID 和 API key

# 4. 运行
uv run python -m kernel
```

### 🔑 最小配置 config.toml

```toml
[telegram]
token = "YOUR_BOT_TOKEN"          # 从 @BotFather 获取
allowed_user = 123456789          # 你的 Telegram 用户 ID

[general]
default_provider = "anthropic"

[providers.anthropic]
type = "claude"
api_key = "sk-ant-..."
max_tokens = 16384
default_model = "claude-sonnet-4-5-20250929"
models = ["claude-sonnet-4-5-20250929"]
```

完整配置选项（包括 OpenAI 兼容提供商、STT/TTS、CLI 代理、MCP 服务器）请参考 `config.example.toml`。

---

## 💬 机器人命令

| 命令 | 功能 |
|------|------|
| `/new` | 开始新会话 |
| `/history` | 查看历史会话 |
| `/resume #n` | 继续某个会话 |
| `/retitle [#n]` | 重新生成会话标题 |
| `/del_history #n` | 删除某个会话 |
| `/provider [name]` | 查看或切换 LLM 提供商 |
| `/model [name]` | 查看或切换模型 |
| `/remember <text>` | 存入长期记忆 |
| `/memory` | 查看所有记忆 |
| `/forget #n` | 删除某条记忆 |
| `/cancel` | 取消当前任务 |
| `/status` | 查看机器人状态 |

---

## 🧠 记忆机制

Kernel 有两层记忆：

- **会话历史** —— 保留在上下文中的近期消息（可配置轮数）
- **长期记忆** —— 持久化 SQLite 存储，支持全文搜索（中文 + 英文）

AI 会自主决定何时搜索或存储记忆。你也可以通过 `/remember`、`/memory` 和 `/forget` 手动管理。

---

## 🔧 CLI 委派

当你让 Kernel 执行文件操作、代码编辑或 Shell 命令时，它会委派给 CLI 代理：

- **Claude Code** —— 通用编码任务
- **Codex** —— 备选 CLI 代理

AI 会自动调用 `delegate_to_cli` 工具，结果会回传到 Telegram。

---

## 🌐 MCP 集成

Kernel 可以连接 [MCP](https://modelcontextprotocol.io/) 服务器来扩展能力：

```toml
[[mcp.servers]]
name = "exa"
type = "http"
url = "https://mcp.exa.ai/mcp"
```

MCP 服务器的工具会和内置工具一起注册 —— AI 可以无缝使用。

---

## 🎙️ 语音

发送语音消息 → Kernel 转录 (Whisper API) → AI 回复 → 合成语音回复 (Edge TTS，免费)。

需要在配置中添加 `[stt]` 和 `[tts]` 部分。文字回复始终可用。

---

## 🎭 人格定制

在 `config.toml` 旁边放一个 `SOUL.md` 文件，即可自定义机器人的性格、语气和规则。Kernel 会自动加载它作为系统提示词的基础。

---

## ❓ 常见问题

<details>
<summary>Q: 如何获取我的 Telegram 用户 ID？</summary>

在 Telegram 上给 [@userinfobot](https://t.me/userinfobot) 发消息，它会回复你的用户 ID。

</details>

<details>
<summary>Q: 可以使用多个 LLM 提供商吗？</summary>

可以。在 config.toml 中定义多个提供商，运行时用 `/provider` 切换。

</details>

<details>
<summary>Q: 支持哪些文件类型？</summary>

文本和代码文件（UTF-8）：`.py`、`.js`、`.ts`、`.json`、`.yaml`、`.md`、`.sql`、`.html`、`.css` 等。不支持二进制文件（PDF、压缩包等）作为文件附件 —— 但可以直接发送图片。

</details>

<details>
<summary>Q: 数据存储在哪里？</summary>

所有运行时数据（SQLite 数据库、日志、临时文件）存储在配置文件所在目录的 `data/` 下。可通过 `general.data_dir` 配置。

</details>

<details>
<summary>Q: 可以部署到服务器吗？</summary>

可以。在任何 Python 3.11+ 的 Linux 服务器上运行即可。建议使用专用系统用户（非 root）。机器人使用长轮询，不需要开放入站端口。

</details>

---

## 📚 更多信息

- [ARCHITECTURE_CN.md](ARCHITECTURE_CN.md) — 技术细节、模块结构、内部机制
- `config.example.toml` — 完整配置参考（含注释）

---

## 📄 许可证

MIT
