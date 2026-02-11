# Kernel - 个人 AI 助手

## Context

从零构建个人 AI 助手（代号 Kernel），部署在 Telegram 上。OpenClaw 虽火但 bug 多、安全性差，所以自己造。

目标：本地优先、隐私可控、模块化可扩展的个人 AI Agent。

运行目标：Windows（开发）+ Debian 12（部署），两端都要可运行。

## 核心架构

```
用户 (Telegram)
    ↓ 文字/图片/文件
┌──────────────────────────────────────────────┐
│  bot.py — TG 消息收发                        │
│  图片→base64  文件→提取内容                    │
└─────────────────┬────────────────────────────┘
                  ↓
┌──────────────────────────────────────────────┐
│  agent.py — 对话层 (LLM)                     │
│  Claude API / OpenAI 兼容 API                │
│  System prompt = SOUL.md + 记忆上下文        │
│  通过 tool use 自主决定行动                  │
│                                              │
│  Tools:                                      │
│  ├── delegate_to_cli → CC / Codex (干活)     │
│  ├── memory_*        → 记忆读写              │
│  └── [MCP tools]     → 动态加载的 MCP 工具   │
└───────┬──────────┬──────────┬────────────────┘
        ↓          ↓          ↓
┌────────────┐ ┌────────┐ ┌────────────────────┐
│ 内置工具   │ │ MCP    │ │ CLI 委派           │
│ - memory   │ │ Client │ │ - Claude Code(默认)│
│            │ │ - exa  │ │ - Codex            │
│            │ │ - c7   │ │                    │
└────────────┘ └────────┘ └────────────────────┘
```

### 设计原则

- **LLM 通过 tool use 自主决定行动**，不做硬编码的任务分类
- **"干活"全部委派给 CLI Agent**（文件操作、shell、浏览器、代码编辑等）
- **Kernel 只内置 CLI 做不了的事**：常驻进程功能（记忆）
- **MCP client 提供扩展能力**：搜索、文档查询等通过 MCP servers 动态加载
- **SOUL.md 定义人格**：AI 的性格、规则、偏好，用户可自定义
- CLI 委派优先级：用户自然语言指定 > 默认 Claude Code

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11 | uv 管理依赖 |
| TG Bot | python-telegram-bot v21+ | 成熟稳定，async |
| LLM - Claude | anthropic SDK | Messages API + tool use + streaming |
| LLM - OpenAI兼容 | openai SDK | chat/completions |
| MCP | mcp SDK (Python) | MCP client，连接任意 MCP server |
| Markdown→TG | mistune | 轻量 Markdown parser + 自定义 TG HTML renderer |
| CLI - Claude Code | claude-code SDK (子进程) | 任务委派 |
| CLI - Codex | codex CLI (子进程) | 可选的任务委派 |
| 记忆 | SQLite + FTS5 | 轻量持久化 + 全文搜索 |
| 配置 | TOML | 简洁可读 |

## 项目结构

```
H:\Project-X\
├── pyproject.toml
├── config.toml              # 用户配置（providers、TG token、MCP servers 等）
├── config.example.toml      # 配置模板
├── SOUL.md                  # AI 人格定义（system prompt）
├── kernel/
│   ├── __init__.py
│   ├── __main__.py          # 入口：uv run -m kernel
│   ├── bot.py               # TG Bot 入口：文字/图片/文件处理
│   ├── config.py            # 配置加载
│   ├── agent.py             # Agent 核心：tool use 循环 + CLI 委派
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py          # LLM 抽象基类
│   │   ├── claude.py        # Anthropic Claude 实现
│   │   └── openai_compat.py # OpenAI 兼容 API 实现（chat/completions）
│   ├── tools/
│   │   ├── __init__.py
│   │   └── registry.py      # 工具注册表
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── client.py        # MCP client：连接 MCP servers，加载 tools
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── base.py          # CLI Agent 抽象基类
│   │   ├── claude_code.py   # Claude Code 委派（默认）
│   │   └── codex.py         # Codex 委派
│   └── memory/
│       ├── __init__.py
│       └── store.py         # SQLite 记忆存储 + 全文搜索
└── data/                    # 运行时数据（SQLite DB、下载文件、CLI 输出等；默认 `data_dir="data"`）
```

## 核心流程

1. 用户在 TG 发消息（文字/文件/图片）
2. `bot.py` 预处理：
   - 图片 → base64 编码，传给 LLM Vision
   - 文件 → 提取文本内容（仅 UTF-8 文本/代码等；不支持 PDF/Office）
3. `agent.py` 加载 SOUL.md + 会话历史 + 召回的长期记忆（top-k），构建完整 messages
4. 发送给选定的 LLM（Claude/OpenAI），支持 streaming
5. LLM 通过 tool use 自主决定行动：
   - **直接回复**：普通对话；先发送占位提示，最终以 Telegram HTML（Markdown 渲染）按块分段发送（不流式编辑）
   - **delegate_to_cli**：启动 CC/Codex 子进程，TG 发送等待提示；完成后完整输出回传 LLM，由 LLM 总结后发给用户
   - **memory_***：读写长期记忆
   - **[MCP tools]**：调用 MCP server 提供的工具（搜索、文档查询等）
6. tool 执行结果回传 LLM，循环直到最终回复（单条消息最多 25 次 tool call）
7. 最终回复发回 TG
8. 保存会话历史（写入 DB 前做历史瘦身）

## 关键设计决策

### 已确认的取舍（单人开发优先）
- **历史瘦身（默认开启）**：写入 SQLite 前，对“大体积内容”做替换；`/resume` 恢复的是“瘦身后的历史”，不保证复现全部细节。
  - tool_result：用一句话摘要 + artifact 引用（如 `data/cli_outputs/xxx.txt`）替换
  - 图片：base64 替换为 `[图片已处理]`
  - 文件：提取文本替换为 `[文件 xxx.py 已处理]`
- **长期记忆注入默认仅召回 top-k**（默认 5，可用 config.toml 的 `general.memory_recall_k` 调整），不全量注入上下文；其余通过 `memory_search` 现查。
- **provider/model 持久化**：切换 provider/model 时自动保存到 SQLite `settings` 表；重启后恢复上次选择；若上次 provider 不可用则回退到 config.toml 的 `default_provider`。`/new` 继承当前 provider/model。
- **`/cancel` 已完整实现**：取消 LLM streaming + tool loop + kill CLI 子进程。
- **CLI 两个都可用**：默认 Claude Code；必要时可通过自然语言或 `delegate_to_cli(cli=...)` 指定 Codex。
- **7 天清理策略维持不变**：下载文件与 `data_dir/cli_outputs/` 7 天自动清理；历史里引用的 artifact 可能过期（可接受）。

### SOUL.md — AI 人格定义
- 作为 system prompt 的核心部分加载
- 定义 AI 的名字、性格、规则、偏好
- 用户可自由编辑定制自己的 AI 助手人格

### LLM 对话层
```python
class LLM(ABC):
    async def chat(self, messages, tools=None, stream=False) -> AsyncIterator[Chunk] | Response
```
- Claude 和 OpenAI 兼容 API 统一接口
- OpenAI 兼容层支持 `/v1/chat/completions`
- tool use 格式在抽象层统一转换
- OpenAI-compatible endpoint 能力差异：不做自动降级；遇到 streaming/tools/vision 等能力缺失直接报错，并提示切换 provider/model/endpoint
- 支持 Vision（图片作为 image content 传给 LLM，不检查模型是否支持，由用户确保）
- **多 Provider 支持**：config.toml 配置多个 provider（Anthropic、OpenAI、DeepSeek、Ollama 等）
- **两级切换**：`/provider` 切换 provider，`/model` 切换当前 provider 下的模型
- **provider/model 持久化到 SQLite `settings` 表**：切换时自动保存，重启后恢复；provider 不可用时回退到 config.toml 默认值；`/new` 继承当前 provider/model
- `/model` 仅允许在 config.toml 中为该 provider 配置的 `models=[...]` 里选择；不在列表中直接报错
- 自然语言不切换对话模型，仅用于 CLI 委派时指定 CC/Codex

### MCP Client
- Kernel 作为 MCP client，启动时连接 config.toml 中配置的 MCP servers
- servers 支持两种形态：http（url + 可选 headers）与 stdio（command + args）
- 将 MCP tools 转换为 LLM tool use 格式，以 `mcp_{server}__{tool}` 命名避免重名（同时满足 LLM tool/function 命名约束），动态注册
- LLM 可直接调用 MCP tools（exa 搜索、context7 文档查询等）
- 用户可在 config.toml 中自由添加/移除 MCP servers
- 启动时连接失败：跳过该 server，不阻塞启动
- 运行中 server 掉线/超时：自动重连（指数退避）；重连失败：禁用该 server 的 tools + 日志告警

### CLI 委派（delegate_to_cli）
```python
class CLIAgent(ABC):
    async def run(self, task: str, cwd: str) -> str
```
- 作为 LLM 的一个 tool 注册，LLM 自主决定何时调用
- tool 描述："当用户需要执行文件操作、代码编辑、项目分析、Shell 命令、浏览器操作等实际任务时使用"
- Claude Code：通过子进程调用，统一执行 `command + args + [task]`（如 `claude -p --output-format text "task"`）
- Codex：通过子进程调用，使用 `codex exec` 非交互模式；统一执行 `command + args + [task]`，并在运行时追加 `-C <cwd>` + `--output-last-message <file>`（确保可靠取回最终回复）。Windows 上需使用 `--dangerously-bypass-approvals-and-sandbox`（sandbox 在 Windows 上不可用）。示例（默认 `data_dir="data"`）：`codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --color never -C "H:\\Project-X" --output-last-message "data/cli_outputs/xxx.txt" "task"`
- 默认 Claude Code；用户自然语言指定（"用codex帮我..."）时切换 Codex；不支持并发（串行执行）
- 运行参数：`cwd` 优先取 tool 入参；否则使用 `general.default_workspace`（相对 config.toml 所在目录）；不做路径限制
- 取消/超时：支持 TG `/cancel` 取消；CLI 子进程超时 10min 自动 kill
- 执行期间：TG 发送等待提示（如"正在执行任务..."）
- 完成后：完整输出回传给 LLM，由 LLM 总结后发给用户（不做实时进度转发）
- CLI 输出落盘到 `data_dir/cli_outputs/`；传给 LLM 时超过 50K 字符则头尾截断（保留头部 + 尾部，中间省略）
- 通过 exit code + 最终输出判断任务是否完成

### 内置 Tool 接口（LLM tool use）
- `delegate_to_cli(task: str, cwd: str | null = null, cli: "claude_code" | "codex" | null = null) -> {ok, cli, cwd, exit_code, output_path, output}`
  - `cli=null` 时使用默认 Claude Code
  - `output` 为传回 LLM 的内容（必要时按 50K 规则截断）；完整输出始终落盘在 `output_path`
- `memory_add(text: str) -> {id}`
- `memory_search(query: str, limit: int = 5) -> [{id, text, created_at}]`
- `memory_list(limit: int = 200) -> [{id, text, created_at}]`
- `memory_delete(id: int) -> {ok}`

### 内置工具（仅限 CLI 做不了的）
- **memory**：长期记忆读写，Kernel 自身状态
- 工具通过装饰器注册，自动生成 JSON schema

### 会话历史与长期记忆
- Phase 1：会话历史落 SQLite（会话表 + 消息表）；消息存 messages JSON（含 tool_use/tool_result 结构），但**对大体积内容启用历史瘦身**，确保 DB 可控且 `/resume` 可用
  - `/resume` 恢复“瘦身后的历史”；若需要完整输出，依赖 artifact 文件（可能因 7 天清理而过期）
- 进程重启后**总是新对话**：不会自动 `/resume` 最近会话；历史只通过 `/history` + `/resume` 手动进入
- Phase 4：长期记忆落 SQLite（独立表）+ FTS5 全文搜索
- 触发：LLM 自动（由 SOUL.md 规则约束）+ 用户手动（`/remember`）
- 召回：agent 先 FTS 召回 top-k（默认 5，可用 config.toml 的 `general.memory_recall_k` 调整）注入上下文；LLM 也可主动调用 `memory_search`
- 管理：`/memory` 查看、`/forget <id>` 删除
- FTS5 兜底：启动时检测 FTS5；不可用则 `memory_search` 退化为 LIKE，并在 `/status` 提示

### 图片理解
- TG 发图 → bot.py 下载并转 base64 → 作为 image content 传给 LLM
- 不硬编码 vision 能力检查，由用户确保当前模型支持 vision
- 图片超过 20MB 则提示用户，不传 LLM

### 文件上传处理
- TG 发文件 → bot.py 下载到 `data_dir/` → 根据类型提取文本内容
- Phase 3 先支持：txt、代码文件（py/js/ts/json/yaml/md 等，必须 UTF-8）
- 不支持：PDF、Office 文档、非 UTF-8 文本（解码失败直接提示用户）
- Office 文档后置（当前不支持）
- 文件最大 20MB，提取文本截断到 50K 字符
- 提取的文本作为 user message 的一部分传给 LLM

### 输出到 TG
- 先发送占位提示（如“正在生成回复…”）
- LLM 完成后一次性发送最终回复（Telegram HTML：对 Markdown 做本地渲染）
- TG 单条消息限制 4096 字符；最终回复超长时按段落/代码块边界智能分割，依次发送多条消息
- 若 HTML 解析失败（如 can't parse entities），回退到纯文本分段发送

### Telegram 接入
- 统一使用 long polling（开发与部署）
- 启动时 `drop_pending_updates=True`
- Phase 1 输出格式：Telegram HTML（**mistune** 解析 Markdown AST + 自定义 TelegramHTMLRenderer 映射到 TG 支持的标签；不支持的元素 graceful fallback 到纯文本；失败回退到纯文本 `parse_mode=None`）
- 白名单：仅响应 `allowed_user` 的私聊（未配置则拒绝启动；推荐用 `@userinfobot` 获取）；非白名单消息静默丢弃
- 推送目标：始终推送到 `allowed_user` 的私聊
- LLM API 调用失败：直接把错误信息（脱敏后）发送到 TG（不做友好化包装）

### 并发与取消
- 全局串行：同时只处理一条用户消息；其余普通消息不排队（直接拒绝）
- 忙碌提示：处理期间收到的新消息，**每个忙碌窗口只提示一次**（例如“正在处理上一条，请稍后或 /cancel”），其余静默丢弃
- `/cancel` 走旁路不排队：立即中断当前 LLM streaming + tool loop，回复"已取消"；若正在委派 CLI（Phase 2 起），同时 kill CLI 子进程

### 安全
- API keys 不进代码：只在 config.toml 或环境变量（仅敏感项支持 env 覆盖）
- 日志与错误信息脱敏：不得包含 TG token、provider api_key、MCP headers
  - 普通 token/key：默认掩码为“保留前 4 + 后 4”；长度 ≤ 8 则全掩码
  - HTTP headers（含 `Authorization`/`Cookie`/MCP headers）：始终整段替换为 `[REDACTED]`（不保留片段）
- CLI 委派继承 CC/Codex 自身的安全机制

### 上下文管理
- 会话历史 + 记忆 + 文件内容可能超 LLM 上下文窗口
- 截断策略：保留 system prompt（SOUL.md）+ 召回的长期记忆（top-k）+ 最近 N 轮对话，超限丢最旧消息
- N 可在 config.toml 配置，默认 50 轮
- 不做自动总结（复杂且费 token），先简单截断
- **历史瘦身**（存入 DB 时即替换，`/resume` 天然安全）：
  - tool_result：LLM 当轮看到完整内容并回复后，存入历史时替换为**规则化生成的一句话摘要**（不额外调用 LLM）。模板示例：`"重构任务已完成(成功)，详见 data/cli_outputs/xxx.txt"`。适用于 delegate_to_cli、MCP tools 等所有大体积 tool_result
  - 图片：LLM 当轮处理后，存入历史时将 base64 image content 替换为 `[图片已处理]`
  - 文件内容：LLM 当轮处理后，存入历史时将提取的文本替换为 `[文件 xxx.py 已处理]`

### 时区
- config.toml 配置固定时区（如 `timezone = "Asia/Shanghai"`）
- Windows 环境需依赖 `tzdata` 以支持 IANA 时区

### config.toml Schema
```toml
[telegram]
token = "BOT_TOKEN"               # 必填；可用环境变量覆盖：KERNEL_TELEGRAM_TOKEN
allowed_user = 123456789          # 必填：唯一授权的 TG user ID（未配置则拒绝启动；推荐用 @userinfobot 获取）

[general]
timezone = "Asia/Shanghai"
default_provider = "anthropic"    # 启动时默认使用的 provider
default_workspace = "."           # CLI 默认 cwd，默认为 config.toml 所在目录
context_rounds = 50               # 上下文保留轮数
memory_recall_k = 5               # 长期记忆默认注入条数（top-k）
data_dir = "data"                 # 运行时数据目录，相对 config.toml 所在目录

[providers.anthropic]
type = "claude"
api_base = "https://api.anthropic.com"  # 可选：默认官方 API endpoint
api_key = "sk-ant-..."            # 可用环境变量覆盖：KERNEL_PROVIDER_ANTHROPIC_API_KEY
max_tokens = 16384               # 必填（Claude Messages API 要求）；无代码级默认值；按模型能力设置
default_model = "claude-sonnet-4-5-20250929"
models = ["claude-sonnet-4-5-20250929", "claude-opus-4-6"]
headers = { User-Agent = "Mozilla/5.0 ..." }  # 可选：自定义 HTTP headers

[providers.openai]
type = "openai_compat"
api_base = "https://api.openai.com/v1"
api_key = "sk-..."                # 可用环境变量覆盖：KERNEL_PROVIDER_OPENAI_API_KEY
# max_tokens =                   # 可选：OpenAI 兼容类型不强制
default_model = "gpt-4o"
models = ["gpt-4o"]
headers = { User-Agent = "Mozilla/5.0 ..." }  # 可选：自定义 HTTP headers

[providers.deepseek]
type = "openai_compat"
api_base = "https://api.deepseek.com/v1"
api_key = "sk-..."                # 可用环境变量覆盖：KERNEL_PROVIDER_DEEPSEEK_API_KEY
default_model = "deepseek-chat"
models = ["deepseek-chat"]
headers = { User-Agent = "Mozilla/5.0 ..." }  # 可选：自定义 HTTP headers

[titles]
type = "openai_compat"               # 独立 provider 配置（不引用已有 provider）
api_base = "https://..."
api_key = "sk-..."                   # 可用环境变量覆盖：KERNEL_TITLES_API_KEY
model = "claude-haiku-4-5-20251001"  # 推荐用便宜快速的模型
max_tokens = 100                     # 标题不需要长输出
headers = { User-Agent = "Mozilla/5.0 ..." }  # 可选：自定义 HTTP headers

[cli.claude_code]
command = "claude"                # 可执行文件路径
args = ["-p", "--output-format", "text"]  # 额外参数，task 追加在末尾：command + args + [task]

[cli.codex]
command = "codex"
args = ["exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--color", "never"]

[[mcp.servers]]
name = "exa"
type = "http"                       # http: url + 可选 headers；stdio: command + args
url = "https://mcp.exa.ai/mcp"

[[mcp.servers]]
name = "context7"
type = "http"
url = "https://mcp.context7.com/mcp"
headers = { CONTEXT7_API_KEY = "${CONTEXT7_API_KEY}" }  # 示例：从环境变量注入（仅敏感项支持 env）
```

### 配置定位与覆盖
- config.toml 定位：优先环境变量 `KERNEL_CONFIG`，否则默认使用当前工作目录下的 `config.toml`
- 路径基准：SOUL.md / `data_dir` 均相对 config.toml 所在目录
- 环境变量覆盖（仅敏感项，优先级 env > config.toml）：
  - `telegram.token` ← `KERNEL_TELEGRAM_TOKEN`
  - `providers.<name>.api_key` ← `KERNEL_PROVIDER_<NAME>_API_KEY`（NAME=section 名大写，非字母数字转 `_`）
  - `titles.api_key` ← `KERNEL_TITLES_API_KEY`
  - `mcp.servers[*].headers` 支持 `${ENV_VAR}` 注入

### 启动校验与缺失行为
- `telegram.token` 或 `telegram.allowed_user` 缺失 → 拒绝启动并提示获取方式
- 默认 provider 的 `api_key` 缺失 → 拒绝启动；非默认 provider 缺失 `api_key` → 允许启动但该 provider 不可用
- MCP：headers 中引用的 `${ENV_VAR}` 缺失 → 跳过该 server 并告警；启动时连接失败也跳过，不阻塞启动

### 数据目录与保留
- `data_dir` 用于存放：SQLite DB、下载文件、CLI 输出、日志（均位于 `general.data_dir` 下）
- 保留策略：会话历史/长期记忆永久保留；下载文件与 `data_dir/cli_outputs/` 7 天自动清理

### 日志
- 标准库 `logging`：默认输出到 stdout + `data_dir/logs/kernel.log`（RotatingFileHandler：10MB × 5）
- 敏感信息脱敏规则见“安全”一节

### SQLite 迁移
- `PRAGMA user_version` + 简单迁移函数，不引入 Alembic

## 分阶段实施计划

### 执行约定（重要）
- 每个 Phase 在一个新窗口/新会话中完成：先实现 → 按该 Phase 的“验证”验收 → 再进入下一 Phase
- 每个 Phase 结束时同步更新 `plan.md`：记录新增决策/变更点、未完成事项、下一 Phase 的注意点

### Phase 1：骨架 + 基础对话（先跑起来）
1. 初始化项目：pyproject.toml、uv（含 `uv.lock`）、目录结构、`__main__.py`、.gitignore（忽略 config.toml、data/、.venv/、*.pyc、日志等）、README.md（Quickstart）
2. `config.py` + `config.toml` + `config.example.toml` — 配置加载（含 providers、timezone、上下文轮数等）
3. `SOUL.md` — AI 人格定义模板
4. `models/base.py` — LLM 抽象接口
5. `models/claude.py` — Claude Messages API（streaming + tool use + vision）
6. `models/openai_compat.py` — OpenAI 兼容实现（chat/completions）
7. `memory/store.py` — 最小版 SQLite（会话表 + 消息表，支持 /new /history /resume /del_history）
8. `agent.py` — Agent 核心：会话管理 + tool use 循环 + 上下文截断 + 支持 `/cancel` 取消当前 streaming/tool loop
9. `bot.py` — TG Bot：文字/图片消息收发 + 占位提示 + 最终 HTML 输出（Markdown 渲染 + 智能分段）+ 会话命令（含 `/cancel` `/status`）
10. 验证：TG 发消息能收到格式化回复（Markdown 渲染），发图能理解，`/start` `/help` 可用，`/provider` `/model` 能切换，`/new` `/history` `/resume` 能管理会话，`/cancel` 能中止当前回复，`/status` 能显示关键状态

### Phase 2：CLI 委派 + MCP（核心能力）
11. `tools/registry.py` — 工具注册 + JSON schema 自动生成
12. `cli/base.py` — CLI Agent 抽象接口
13. `cli/claude_code.py` — Claude Code 集成
14. `cli/codex.py` — Codex 集成
15. `mcp/client.py` — MCP client：连接 servers，加载 tools
16. `agent.py` 集成 delegate_to_cli + MCP tools
17. 验证：能委派任务给 CC 并回传结果；`/cancel` 能中止委派；10min 超时自动 kill；能调用 MCP tools

### Phase 3：文件处理
18. `bot.py` 集成文件上传处理
19. 验证：发送 UTF-8 txt/代码文件能读取内容；不支持类型（PDF/Office/非 UTF-8）有明确提示

### Phase 4：长期记忆
20. `memory/store.py` 扩展：长期记忆表 + FTS5 全文搜索
21. `agent.py` 集成 memory 工具：LLM 自动记忆（SOUL.md 规则约束）+ `/remember` 手动记忆
22. 验证：AI 能记住用户偏好，`/memory` `/forget` 正常工作

### Phase 5：部署
23. 部署脚本（systemd service for Debian 12）
24. 验证：Debian 12 上稳定运行

## TG 命令汇总

| 命令 | 功能 |
|------|------|
| `/start` | 显示欢迎/Quickstart（等同 `/help`） |
| `/help` | 显示命令列表与关键配置提示 |
| `/new` | 新开会话（当前会话归档，开始全新对话） |
| `/history` | 查看历史会话列表（最新在前；显示 `#<n>` 序号 + 日期 + 标题） |
| `/resume #<n>` | 继续指定的历史会话（先 `/history`，再用 `#` 序号） |
| `/retitle [#<n>]` | 重生会话标题（默认：当前会话；如指定 `#<n>` 需先 `/history`） |
| `/del_history #<n>[/#n2/...]` | 删除一个或多个会话（先 `/history`） |
| `/provider [name]` | 查看/切换当前 provider |
| `/model [name]` | 查看/切换当前 provider 下的模型 |
| `/remember <text>` | 手动存入长期记忆 |
| `/memory` | 查看所有长期记忆（显示 `#<n>` 序号 + 日期 + 内容） |
| `/forget #<n>[/#n2/...]` | 删除一个或多个记忆（先 `/memory`） |
| `/cancel` | 取消当前任务（中断 streaming + tool loop + CLI） |
| `/status` | 查看当前状态（session、provider/model、运行中的 CLI、最后错误） |

## Phase 1

### 决策记录
- 新增：Claude `max_tokens` 改为可配置：`providers.<name>.max_tokens`（默认 4096）；因为 Claude Messages API 必填该参数。
- 新增：Claude `max_tokens` 为必填（Claude Messages API 要求），无代码级默认值；OpenAI 兼容类型 `max_tokens` 可选。
- 新增：所有 provider（含 `[titles]`）支持 `headers` 字段，透传到 SDK 的 `default_headers`，用于自定义 User-Agent 等。
- 增强：`providers.<name>.api_base` 对 Claude 也生效（映射到 Anthropic SDK `base_url`），便于使用自建/代理端点。
- 新增：`[titles]` 为独立的完整 provider 配置（type/api_base/api_key/model/max_tokens/headers），不引用已有 provider。
- 新增：会话标题（可选）：支持在 `config.toml` 配置 `[titles]` + 专用 provider/model；新会话首轮对话后自动生成一次标题；支持 `/retitle` 手动重生（默认当前会话）。
- 新增：标题生成遇到瞬时错误（例如 429/5xx/网络超时）自动重试（0/3/15/60s）；最终失败仅告警日志，不影响聊天。
- 新增：`/history` 显示 `#1..#20` 序号（映射到真实 session id），输出简化为 `#<n> YYYY-MM-DD <title>`（本地时间，时区来自 `general.timezone`）；`/resume` `/del_history` `/retitle` 均使用 `#<n>`。
- 变更：SOUL.md 暂时移除了 delegate_to_cli / memory_add 的工具规则和记忆规则（Phase 1 无 tool）；**Phase 2 开始时必须加回**。

### 实现要点
- TG Bot 使用 `concurrent_updates=True`，否则 `/cancel` 和忙碌拒绝无法生效（update 会排队）。
- `/cancel` 由 `cmd_cancel` 发送"已取消"；`handle_message` 的 `CancelledError` 静默 return，避免重复发送。
- 标题生成的 `_clean_title()` 会剥离 `<think>` 思维链标签（含未闭合的），兼容思考模型。
- 标题生成触发从 async generator post-yield 移到了 `bot.py` 的 `maybe_generate_title()` 调用（async generator 尾部代码不可靠）。
- 标题生成遇到 429 直接放弃不重试。
- Markdown→TG HTML 使用 mistune 3.x，`render_token` 需覆盖以匹配 HTMLRenderer 的 children/raw/attrs 模式。
- 消息分割 `split_tg_message` 的标签修复：必须按文档顺序处理 open/close 标签，且重开标签时保留原始属性（`_find_unclosed_tags` 返回 `(tag_name, full_open_tag)` 元组）。
- 消息分割重开标签时需逆序 prepend（因为 prepend 会反转顺序）。

### Phase 2 注意事项
- **必须**恢复 SOUL.md 中的 delegate_to_cli / memory_add 工具规则和记忆规则。
- tool use 循环骨架已在 `agent.py` 中就绪（`self._tools` / `self._tool_handlers` 字典），Phase 2 注册即可。
- `tools/registry.py` 和 `cli/` 目录已创建为空占位，Phase 2 直接填充。
- 历史瘦身框架已就绪（`Store.slim_content`），Phase 2 需扩展 tool_result 瘦身规则。

## Phase 2

### 决策记录
- 新增依赖：`mcp>=1.0,<2`（MCP SDK）+ `httpx>=0.27,<1`（MCP HTTP transport 需要）。
- `tools/registry.py`：装饰器 `@registry.tool(name, description=...)` 从函数签名自动生成 JSON Schema；支持 `str|None`（Optional）、`Literal[...]`（enum）等类型映射；另有 `registry.register(...)` 方法供 MCP 工具动态注册。
- `cli/base.py`：`CLIAgent` 抽象基类，`run()` 方法通过 `asyncio.create_subprocess_exec` 执行子进程，统一处理超时（10min）、取消、输出落盘（`data_dir/cli_outputs/`）、50K 字符截断（头尾保留）。`CLIResult` 数据类包含 `ok/cli_name/cwd/exit_code/output_path/output`。
- `cli/claude_code.py`：`build_command` = `[command, *args, task]`；输出取自 stdout。
- `cli/codex.py`：`build_command` 运行时追加 `-C <cwd>` + `--output-last-message <output_path>` + task；输出优先从 `--output-last-message` 文件读取，fallback 到 stdout。
- `mcp/client.py`：使用 `contextlib.AsyncExitStack` 保持 `streamable_http_client` / `stdio_client` + `ClientSession` 上下文存活。工具命名 `mcp_{server}__{tool}`（安全字符）。连接失败跳过不阻塞启动。工具调用失败自动重连一次再重试。
- `agent.py` 集成：`__init__` 中注册 `delegate_to_cli` 内置工具；`init_mcp()` 异步方法连接 MCP 并注册工具；MCP 工具通过闭包绑定 `qualified_name` 并路由到 `MCPClient.call_tool()`。
- `agent.cancel()` 现在同时 kill 正在运行的 CLI 子进程（`asyncio.create_task(self._active_cli.kill())`）。
- `bot.py`：当 `delegate_to_cli` 工具执行时发送 "⏳ 正在执行任务…" 等待提示；`/cancel` 显示被终止的 CLI 名称；`/status` 新增 CLI 运行状态行。
- SOUL.md 已恢复工具使用规则：delegate_to_cli、memory_*（Phase 4 启用）、MCP 工具命名与使用说明。
- `Store.slim_content` 扩展：delegate_to_cli 结果（含 output_path）一律瘦身；其他 tool_result 超 200 字符时瘦身。规则化生成一句话摘要（尝试解析 JSON 提取 ok/cli/exit_code/output_path，fallback 到前 80 字符预览 + 字符数）。
- provider/model 持久化：新增 `settings` 表（schema v2），切换时 `set_setting` 保存，启动时 `restore_provider_model` 恢复。
- 标题自动生成触发条件：从 `msg_count == 2` 改为检查 session 无标题（兼容 tool use 产生多条消息的情况）。
- Windows Ctrl+C shutdown：`except Exception` → `except BaseException`（CancelledError 继承自 BaseException）；shutdown 顺序改为 store → agent → app（趁 event loop 存活关 DB）；`Store.close()` async 失败时 fallback 到同步关闭底层连接。
- Windows CLI 子进程：`shutil.which` 解析 `.cmd` 文件；解析失败时 fallback 到 `create_subprocess_shell`。
- MCP `streamable_http_client` 返回值用 `streams[0], streams[1]` 解包，兼容 2/3 元素。
- Codex args 改为 `--dangerously-bypass-approvals-and-sandbox`（Windows 上 sandbox 不可用）。

### 实现要点
- MCP client 使用 `streamable_http_client`（v2 API，返回 2 元素元组 `(read, write)`），HTTP headers 通过 `httpx.AsyncClient(headers=...)` 传递。
- CLI 子进程通过 `asyncio.create_subprocess_exec` 启动（非 shell），env 继承当前环境。
- `ToolRegistry` 同时支持装饰器注册（内置工具）和编程式注册（MCP 工具），两类工具统一存入 `self._tools` / `self._tool_handlers`。
- `agent.chat()` 在工具执行前 yield 一个 `StreamChunk(tool_use_id=..., tool_name=...)` 通知 bot.py 显示等待提示。
- `_handle_delegate_to_cli` 中 `self._active_cli` 追踪当前运行的 CLI，`cancel()` 和 `active_cli_name` 依赖此字段。

### Phase 3 注意事项
- Phase 3 范围：bot.py 集成文件上传处理（TG 文件下载 → 提取文本 → 传 LLM）。
- 支持 txt、代码文件（py/js/ts/json/yaml/md 等，必须 UTF-8）；不支持 PDF/Office/非 UTF-8。
- 文件最大 20MB，提取文本截断到 50K 字符。
- 历史瘦身：文件内容替换为 `[文件 xxx.py 已处理]`。

## Phase 3

### 决策记录
- 文件类型判断基于扩展名白名单（`_TEXT_EXTENSIONS`）+ 黑名单（`_UNSUPPORTED_EXTENSIONS`）；无扩展名的已知文件（Makefile/Dockerfile 等）也支持。
- 不在白名单也不在黑名单的未知扩展名：拒绝并提示，避免误读二进制文件。
- 文件内容格式化为 `[文件: {filename}]\n```\n{text}\n```` 传给 LLM，便于 LLM 识别文件边界。
- 历史瘦身匹配 `[文件: ` 前缀 + `` \n```\n `` 标记，提取文件名后替换为 `[文件 xxx.py 已处理]`。
- 下载路径：`data_dir/downloads/{file_unique_id}_{filename}`，使用 TG 的 `file_unique_id` 避免重名。
- `_extract_file_text` 使用同步 `read_text`（文件已在本地磁盘，I/O 极快，无需 async）。

### 实现要点
- `filters.Document.ALL` 加入消息处理器过滤器（与 `filters.TEXT | filters.PHOTO` 并列）。
- 文件处理分支在 `handle_message` 中位于图片处理之后、纯文本之前（`elif msg.document`）。
- 三种拒绝路径：黑名单扩展名（明确不支持）、未知扩展名（无法识别）、UTF-8 解码失败（非文本）。
- `downloads/` 目录在 `run_bot()` 启动时预创建，文件处理时也有 `mkdir` 兜底。

### Phase 4 注意事项
- Phase 4 范围：长期记忆（memory 表 + FTS5 + memory_* 工具 + /remember /memory /forget 命令）。
- SOUL.md 中 memory 工具规则已写好（标注"Phase 4 启用"），实现后去掉该标注即可。
- `agent.py` 需注册 memory_add / memory_search / memory_list / memory_delete 四个工具。
- tool use 循环骨架已就绪（Phase 2），注册新工具即可被 LLM 调用。
- FTS5 兜底：启动时检测 FTS5 可用性；不可用则 `memory_search` 退化为 LIKE。
- 记忆召回 top-k 注入 system prompt（`agent._build_system_prompt`），默认 5 条（`config.general.memory_recall_k`）。
- `/remember` 是 TG 命令（bot.py），直接调用 store 写入，不经过 LLM。
- `/memory` 列出所有记忆，`/forget <id>` 删除指定记忆。

### 验证结果
- 全部 12 项测试通过 ✓

## Phase 4

### 决策记录
- `memories` 表：`id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT`。
- FTS5：`memories_fts` contentless 虚拟表（`content='', content_rowid=id`），应用层通过 jieba 分词后手动同步（不用触发器，因为需要存分词后的文本而非原文）。启动时 `_try_fts5()` 检测；失败则 `fts5_available=False`，`memory_search` 退化为 `LIKE %query%`。
- 新增依赖：`jieba>=0.42,<1`（中文分词，~15MB）。`_tokenize()` 使用 `jieba.cut_for_search`（搜索引擎模式，粒度更细）。
- FTS5 搜索返回空时 fallback 到 LIKE；LIKE 也搜不到时，system prompt 注入最近 top-k 条记忆兜底。
- 已迁移 DB 不重新创建 FTS5：`_check_fts5_exists()` 检查 `sqlite_master`。
- Schema 版本 2 → 4（跳过 3，因为 v3 的触发器方案已被替换）。
- `agent._build_system_prompt` 改为 `async`，接收 `user_query` 参数；先搜索召回，搜不到则注入最近 top-k（`config.general.memory_recall_k`，默认 5），注入格式 `## 长期记忆（自动召回）\n- [id] text`。
- memory 工具注册：`memory_add`/`memory_search`/`memory_list`/`memory_delete` 四个工具通过 `ToolRegistry` 装饰器注册，LLM 可自主调用。
- `/remember <text>` 直接调用 `store.memory_add`，不经过 LLM。
- `/memory` 显示 `#<n>` 序号 + 日期 + 内容（同 `/history` 的序号映射模式）。`/forget #<n>[/#n2/...]` 支持批量删除（先 `/memory` 查看序号）。
- `/status` 新增 FTS5 状态行。
- SOUL.md 已去掉 memory 工具规则中的"（Phase 4 启用）"标注。

### 实现要点
- jieba `cut_for_search` 模式：在精确模式基础上对长词再切分，提高召回率（如"昭和时期"→"昭和 时期"）。
- FTS5 使用 contentless 表（`content=''`）：只存分词后的文本用于搜索，原文从 `memories` 表读取。
- `memory_add` 同时写 `memories` 表（原文）和 `memories_fts`（分词文本）。
- `memory_delete` 先查原文 → tokenize → 从 FTS5 删除 → 再删 memories 行。
- `_try_fts5` 迁移时：先清理旧触发器和旧 FTS 表（兼容 v3），再重建并索引已有数据。
- `memory_search` FTS5 模式使用 `ORDER BY rank`（BM25 相关性排序）；LIKE 模式使用 `ORDER BY id DESC`。
- `_build_system_prompt` 中 memory recall 失败静默降级（仅 debug 日志），不影响正常对话。

### Phase 5 注意事项
- Phase 5 范围：部署。

## Phase 5

### 决策记录
- `_build_system_prompt` 注入当前本地时间，便于 LLM 感知时间。
- 部署：`deploy/kernel.service`（systemd unit）+ `deploy/setup.sh`（Debian 12 部署脚本）。

### 实现要点
- 当前时间格式：`2026-02-11T21:42:00+0800（Asia/Shanghai）`，每次对话动态生成。
- systemd service 使用 `uv run -m kernel` 启动，`Restart=on-failure`。

## Phase 6

### 决策记录
- STT：OpenAI Whisper 兼容 API（`openai.AsyncOpenAI`），支持 `api_base`/`api_key`/`model`/`headers` 配置。
- TTS：`edge-tts`（免费，中文质量好）生成 mp3，`static-ffmpeg`（pip 包自带 ffmpeg 二进制）转换为 ogg opus。
- 依赖变更：移除 `pydub`，改用 `edge-tts>=7,<8` + `static-ffmpeg>=3,<4`。
- `STTConfig` 去掉 `type` 字段（无需区分类型，统一用 OpenAI 兼容 API），新增 `headers` 字段。
- `static_ffmpeg.add_paths()` 在 `run_bot()` 启动时调用（首次下载 ffmpeg 二进制，后续秒过）。
- 语音消息经 STT 转写后以 `[语音: {text}]` 格式传给 LLM。
- 历史瘦身：`[语音: ...]` → `[语音已处理]`。
- TTS 失败时 fallback 到文字回复（try/except + log warning）。
- STT 未配置时发语音消息直接拒绝并提示。TTS 未配置时语音输入仍以文字回复。

### 实现要点
- `STTClient` 使用 `openai.AsyncOpenAI` 的 `audio.transcriptions.create`，支持自定义 headers（`default_headers`）。
- `TTSClient.synthesize` 流程：`edge_tts.Communicate` → mp3 → `static_ffmpeg` subprocess → ogg opus（`-c:a libopus -b:a 48k`）→ 清理临时 mp3。
- `bot.py` 语音分支在图片分支之前（`msg.voice` 优先检查）。
- `is_voice` 标记通过 `state._last_message_was_voice` 追踪，TTS 回复后重置。
