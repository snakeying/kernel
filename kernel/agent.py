"""Kernel Agent — session management, tool-use loop, context truncation, title generation.

Phase 2: integrates CLI delegation, MCP tools, and tool registry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Awaitable, Literal

from kernel.config import Config, ProviderConfig, TitlesConfig
from kernel.memory.store import Store
from kernel.models.base import (
    ContentBlock,
    ImageContent,
    LLM,
    LLMResponse,
    Message,
    Role,
    StreamChunk,
    TextContent,
    ToolDef,
    ToolResultContent,
    ToolUseContent,
)
from kernel.models.claude import ClaudeLLM
from kernel.models.openai_compat import OpenAICompatLLM
from kernel.tools.registry import ToolRegistry
from kernel.cli.base import CLIAgent, CLIResult
from kernel.cli.claude_code import ClaudeCodeAgent
from kernel.cli.codex import CodexAgent
from kernel.mcp.client import MCPClient

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 25
TITLE_RETRY_DELAYS = [0, 3, 15, 60]  # seconds
TITLE_PROMPT = (
    "Generate a short title (max 10 Chinese characters) for this conversation. "
    "Output ONLY the title itself, nothing else. No quotes, no explanation, no punctuation.\n\n"
)
TITLE_MAX_LEN = 30  # safety truncation

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*", re.DOTALL)


def _clean_title(raw: str) -> str:
    """Strip thinking tags, quotes, and truncate to a clean title."""
    # Strip closed <think>...</think> blocks
    text = _THINK_RE.sub("", raw)
    # Strip unclosed <think>... (truncated by max_tokens)
    text = _THINK_OPEN_RE.sub("", text)
    text = text.strip().strip('"\'')
    # Take first non-empty line
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line[:TITLE_MAX_LEN]
    return ""


def _make_llm(provider: ProviderConfig) -> LLM:
    """Instantiate an LLM from provider config."""
    if provider.type == "claude":
        return ClaudeLLM(
            api_key=provider.api_key,
            default_model=provider.default_model,
            max_tokens=provider.max_tokens or 4096,
            api_base=provider.api_base,
            headers=provider.headers,
        )
    return OpenAICompatLLM(
        api_key=provider.api_key,
        default_model=provider.default_model,
        api_base=provider.api_base,
        max_tokens=provider.max_tokens,
        headers=provider.headers,
    )


def _make_titles_llm(cfg: TitlesConfig) -> LLM:
    """Instantiate an LLM for title generation."""
    if cfg.type == "claude":
        return ClaudeLLM(
            api_key=cfg.api_key,
            default_model=cfg.model,
            max_tokens=cfg.max_tokens,
            api_base=cfg.api_base,
            headers=cfg.headers,
        )
    return OpenAICompatLLM(
        api_key=cfg.api_key,
        default_model=cfg.model,
        api_base=cfg.api_base,
        max_tokens=cfg.max_tokens,
        headers=cfg.headers,
    )


# ---------------------------------------------------------------------------
# Content serialisation helpers (internal types ↔ JSON-safe dicts for DB)
# ---------------------------------------------------------------------------


def _content_to_json(content: list[ContentBlock] | str) -> Any:
    """Serialise internal content blocks to JSON-safe structure."""
    if isinstance(content, str):
        return content
    out: list[dict[str, Any]] = []
    for b in content:
        if isinstance(b, TextContent):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ImageContent):
            out.append({"type": "image", "media_type": b.media_type, "data": b.data})
        elif isinstance(b, ToolUseContent):
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, ToolResultContent):
            out.append({
                "type": "tool_result",
                "tool_use_id": b.tool_use_id,
                "content": b.content,
                "is_error": b.is_error,
            })
    return out


def _json_to_content(data: Any) -> list[ContentBlock] | str:
    """Deserialise JSON-safe structure back to internal content blocks."""
    if isinstance(data, str):
        return data
    blocks: list[ContentBlock] = []
    for d in data:
        t = d.get("type")
        if t == "text":
            blocks.append(TextContent(text=d["text"]))
        elif t == "image":
            blocks.append(ImageContent(media_type=d["media_type"], data=d["data"]))
        elif t == "tool_use":
            blocks.append(ToolUseContent(id=d["id"], name=d["name"], input=d["input"]))
        elif t == "tool_result":
            blocks.append(ToolResultContent(
                tool_use_id=d["tool_use_id"],
                content=d["content"],
                is_error=d.get("is_error", False),
            ))
    return blocks


def _json_to_message(row: dict[str, Any]) -> Message:
    """Rebuild a Message from a DB row."""
    role = Role(row["role"])
    content = _json_to_content(row["content"])
    return Message(role=role, content=content)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent:
    """Core agent: manages sessions, LLM calls, tool-use loop, context."""

    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store

        # Runtime state (not persisted)
        self._current_provider_name: str = config.general.default_provider
        self._current_model: str | None = None  # None = use provider default
        self._session_id: int | None = None
        self._history: list[Message] = []
        self._cancelled = False

        # LLM instances (lazy, per provider)
        self._llms: dict[str, LLM] = {}
        self._titles_llm: LLM | None = None

        # Tool registry
        self._registry = ToolRegistry()
        self._tools: dict[str, ToolDef] = {}
        self._tool_handlers: dict[str, Callable[..., Awaitable[Any]]] = {}

        # CLI agents
        self._cli_agents: dict[str, CLIAgent] = {}
        self._active_cli: CLIAgent | None = None  # currently running CLI
        self._init_cli_agents()

        # MCP client (initialised asynchronously)
        self._mcp: MCPClient | None = None

        # SOUL.md
        self._soul: str = ""
        soul_path = config.config_dir / "SOUL.md"
        if soul_path.exists():
            self._soul = soul_path.read_text(encoding="utf-8")

        # Register built-in tools
        self._register_builtin_tools()

    def _init_cli_agents(self) -> None:
        """Instantiate CLI agents from config."""
        output_dir = self.config.data_path / "cli_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)

        for name, cli_cfg in self.config.cli.items():
            if name == "claude_code":
                self._cli_agents[name] = ClaudeCodeAgent(
                    command=cli_cfg.command,
                    args=cli_cfg.args,
                    output_dir=output_dir,
                )
            elif name == "codex":
                self._cli_agents[name] = CodexAgent(
                    command=cli_cfg.command,
                    args=cli_cfg.args,
                    output_dir=output_dir,
                )
            else:
                log.warning("Unknown CLI agent type: %s", name)

    def _register_builtin_tools(self) -> None:
        """Register built-in tools: delegate_to_cli + memory_*."""

        @self._registry.tool(
            "delegate_to_cli",
            description=(
                "当用户需要执行文件操作、代码编辑、项目分析、Shell 命令、浏览器操作等实际任务时使用。"
                "将任务委派给 CLI Agent（Claude Code 或 Codex）执行。"
            ),
        )
        async def delegate_to_cli(
            task: str,
            cwd: str | None = None,
            cli: str | None = None,
        ) -> dict[str, Any]:
            return await self._handle_delegate_to_cli(task, cwd, cli)

        @self._registry.tool(
            "memory_add",
            description="将一条信息存入长期记忆。只记有长期价值的信息（偏好、约定、重要事实）。",
        )
        async def memory_add(text: str) -> dict[str, Any]:
            mid = await self.store.memory_add(text)
            return {"id": mid}

        @self._registry.tool(
            "memory_search",
            description="搜索长期记忆。用于回忆用户偏好、历史信息等。",
        )
        async def memory_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
            return await self.store.memory_search(query, limit)

        @self._registry.tool(
            "memory_list",
            description="列出所有长期记忆。",
        )
        async def memory_list(limit: int = 200) -> list[dict[str, Any]]:
            return await self.store.memory_list(limit)

        @self._registry.tool(
            "memory_delete",
            description="删除指定 ID 的长期记忆。",
        )
        async def memory_delete(id: int) -> dict[str, Any]:
            ok = await self.store.memory_delete(id)
            return {"ok": ok}

        # Merge into active tool sets
        self._tools.update(self._registry.tool_defs())
        self._tool_handlers.update(self._registry.handlers())

    async def init_mcp(self) -> None:
        """Connect to MCP servers and register their tools."""
        if not self.config.mcp_servers:
            return
        self._mcp = MCPClient(self.config.mcp_servers)
        await self._mcp.connect_all()
        self._register_mcp_tools()

    def _register_mcp_tools(self) -> None:
        """Register MCP tools into the agent's tool registry."""
        if not self._mcp:
            return
        mcp_defs = self._mcp.get_tool_defs()
        mcp_client = self._mcp

        for name, tool_def in mcp_defs.items():
            # Create a closure-based handler for each MCP tool
            async def _mcp_handler(_name: str = name, **kwargs: Any) -> str:
                return await mcp_client.call_tool(_name, kwargs)

            self._tools[name] = tool_def
            self._tool_handlers[name] = _mcp_handler

        if mcp_defs:
            log.info("Registered %d MCP tools", len(mcp_defs))

    # -- delegate_to_cli handler -------------------------------------------

    async def _handle_delegate_to_cli(
        self,
        task: str,
        cwd: str | None = None,
        cli: str | None = None,
    ) -> dict[str, Any]:
        """Execute a CLI delegation."""
        # Determine which CLI
        cli_name = cli or "claude_code"
        agent = self._cli_agents.get(cli_name)
        if agent is None:
            available = ", ".join(self._cli_agents.keys()) or "none"
            return {
                "ok": False,
                "error": f"CLI '{cli_name}' not configured. Available: {available}",
            }

        # Determine cwd
        work_dir = cwd or str(self.config.default_workspace_path)

        self._active_cli = agent
        try:
            result = await agent.run(task, work_dir)
        except asyncio.CancelledError:
            return {"ok": False, "error": "Task cancelled by user"}
        finally:
            self._active_cli = None

        return result.to_dict()

    # -- Provider / Model ------------------------------------------------

    @property
    def current_provider_name(self) -> str:
        return self._current_provider_name

    @property
    def current_model(self) -> str:
        prov = self.config.providers[self._current_provider_name]
        return self._current_model or prov.default_model

    @property
    def available_providers(self) -> list[str]:
        return [
            name for name, p in self.config.providers.items()
            if p.api_key and not p.api_key.endswith("...")
        ]

    def switch_provider(self, name: str) -> str:
        """Switch provider.  Returns the new provider name or raises ValueError."""
        if name not in self.config.providers:
            raise ValueError(f"Unknown provider: {name}")
        prov = self.config.providers[name]
        if not prov.api_key or prov.api_key.endswith("..."):
            raise ValueError(f"Provider '{name}' has no API key configured.")
        self._current_provider_name = name
        self._current_model = None  # reset to provider default
        asyncio.create_task(self._persist_provider_model())
        return name

    def switch_model(self, model: str) -> str:
        """Switch model within current provider.  Returns the new model or raises ValueError."""
        prov = self.config.providers[self._current_provider_name]
        if model not in prov.models:
            allowed = ", ".join(prov.models)
            raise ValueError(
                f"Model '{model}' not in allowed models for {self._current_provider_name}: [{allowed}]"
            )
        self._current_model = model
        asyncio.create_task(self._persist_provider_model())
        return model

    async def _persist_provider_model(self) -> None:
        """Save current provider/model to DB for next restart."""
        try:
            await self.store.set_setting("last_provider", self._current_provider_name)
            model = self._current_model or ""
            await self.store.set_setting("last_model", model)
        except Exception:
            log.debug("Failed to persist provider/model", exc_info=True)

    async def restore_provider_model(self) -> None:
        """Restore last provider/model from DB. Call after store.init()."""
        try:
            provider = await self.store.get_setting("last_provider")
            if provider and provider in self.config.providers:
                prov = self.config.providers[provider]
                if prov.api_key and not prov.api_key.endswith("..."):
                    self._current_provider_name = provider
                    model = await self.store.get_setting("last_model")
                    if model and model in prov.models:
                        self._current_model = model
                    else:
                        self._current_model = None
                    log.info("Restored provider: %s, model: %s",
                             self._current_provider_name, self.current_model)
        except Exception:
            log.debug("Failed to restore provider/model", exc_info=True)

    def _get_llm(self) -> LLM:
        name = self._current_provider_name
        if name not in self._llms:
            prov = self.config.providers[name]
            self._llms[name] = _make_llm(prov)
        return self._llms[name]

    # -- Session management ----------------------------------------------

    @property
    def session_id(self) -> int | None:
        return self._session_id

    async def new_session(self) -> int:
        """Archive current session (if any) and start a new one."""
        if self._session_id is not None:
            await self.store.archive_session(self._session_id)
        self._session_id = await self.store.create_session()
        self._history = []
        return self._session_id

    async def resume_session(self, session_id: int) -> int:
        """Resume a session from the DB."""
        session = await self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found.")
        # Archive current if different
        if self._session_id is not None and self._session_id != session_id:
            await self.store.archive_session(self._session_id)
        self._session_id = session_id
        # Load history
        rows = await self.store.get_messages(session_id)
        self._history = [_json_to_message(r) for r in rows]
        return session_id

    async def ensure_session(self) -> int:
        """Ensure there is an active session, creating one if needed."""
        if self._session_id is None:
            self._session_id = await self.store.create_session()
            self._history = []
        return self._session_id

    # -- Cancel ----------------------------------------------------------

    def cancel(self) -> None:
        self._cancelled = True
        # Also kill any running CLI subprocess
        if self._active_cli and self._active_cli.is_running:
            asyncio.create_task(self._active_cli.kill())

    def _check_cancel(self) -> None:
        if self._cancelled:
            self._cancelled = False
            raise asyncio.CancelledError("User cancelled")

    @property
    def active_cli_name(self) -> str | None:
        """Return name of currently running CLI agent, if any."""
        if self._active_cli and self._active_cli.is_running:
            return self._active_cli.name
        return None

    # -- Context building ------------------------------------------------

    async def _build_system_prompt(self, user_query: str = "") -> str:
        """Build the full system prompt from SOUL.md + recalled memories."""
        parts = []
        if self._soul:
            parts.append(self._soul)
        # Recall top-k memories: search first, fallback to most recent
        k = self.config.general.memory_recall_k
        memories: list[dict] = []
        try:
            if user_query:
                memories = await self.store.memory_search(user_query, limit=k)
            if not memories:
                memories = await self.store.memory_list(limit=k)
        except Exception:
            log.debug("Memory recall failed", exc_info=True)
        if memories:
            lines = ["## 长期记忆（自动召回）"]
            for m in memories:
                lines.append(f"- [{m['id']}] {m['text']}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts) if parts else ""

    def _truncate_history(self, messages: list[Message]) -> list[Message]:
        """Keep at most ``context_rounds`` recent messages."""
        max_msgs = self.config.general.context_rounds * 2  # user + assistant = 1 round
        if len(messages) <= max_msgs:
            return messages
        return messages[-max_msgs:]

    # -- Core chat (streaming) -------------------------------------------

    async def chat(
        self,
        user_content: list[ContentBlock] | str,
    ) -> AsyncIterator[StreamChunk]:
        """Process a user message.  Yields streaming chunks.

        Handles the full tool-use loop internally.  The caller only sees
        text chunks and a final finish_reason.
        """
        self._cancelled = False
        await self.ensure_session()
        assert self._session_id is not None

        # Build user message
        user_msg = Message(role=Role.USER, content=user_content)
        self._history.append(user_msg)
        await self.store.add_message_slimmed(
            self._session_id, Role.USER.value, _content_to_json(user_content)
        )

        llm = self._get_llm()
        # Extract user text for memory recall
        if isinstance(user_content, str):
            _user_query = user_content
        elif isinstance(user_content, list):
            _user_query = " ".join(
                b.text for b in user_content if isinstance(b, TextContent)
            )
        else:
            _user_query = ""
        system = await self._build_system_prompt(_user_query)
        tools_list = list(self._tools.values()) if self._tools else None

        for _round in range(MAX_TOOL_ROUNDS):
            self._check_cancel()

            truncated = self._truncate_history(self._history)

            # Collect the full response via streaming
            text_parts: list[str] = []
            tool_chunks: list[StreamChunk] = []
            finish_reason: str | None = None

            async for chunk in llm.chat_stream(
                truncated,
                system=system,
                tools=tools_list,
                model=self._current_model,
            ):
                self._check_cancel()

                if chunk.text:
                    text_parts.append(chunk.text)
                    yield chunk

                if chunk.tool_use_id:
                    tool_chunks.append(chunk)

                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

            # Build the assistant message from accumulated content
            assistant_blocks: list[ContentBlock] = []
            full_text = "".join(text_parts)
            if full_text:
                assistant_blocks.append(TextContent(text=full_text))

            for tc in tool_chunks:
                try:
                    tool_input = json.loads(tc.tool_input_json) if tc.tool_input_json else {}
                except json.JSONDecodeError:
                    tool_input = {}
                assistant_blocks.append(ToolUseContent(
                    id=tc.tool_use_id or "",
                    name=tc.tool_name or "",
                    input=tool_input,
                ))

            assistant_msg = Message(role=Role.ASSISTANT, content=assistant_blocks)
            self._history.append(assistant_msg)
            await self.store.add_message_slimmed(
                self._session_id, Role.ASSISTANT.value,
                _content_to_json(assistant_blocks),
            )

            # If no tool use, we're done
            if not tool_chunks or finish_reason not in ("tool_use", "tool_calls"):
                yield StreamChunk(finish_reason=finish_reason or "end_turn")
                break

            # Execute tools and feed results back
            result_blocks: list[ContentBlock] = []
            for tc in tool_chunks:
                tool_name = tc.tool_name or ""
                tool_id = tc.tool_use_id or ""
                handler = self._tool_handlers.get(tool_name)
                if handler is None:
                    result_blocks.append(ToolResultContent(
                        tool_use_id=tool_id,
                        content=f"Error: unknown tool '{tool_name}'",
                        is_error=True,
                    ))
                    continue
                try:
                    tool_input = json.loads(tc.tool_input_json) if tc.tool_input_json else {}
                except json.JSONDecodeError:
                    tool_input = {}

                # Notify caller that a tool is being executed
                yield StreamChunk(text="", tool_use_id=tool_id, tool_name=tool_name)

                try:
                    result = await handler(**tool_input)
                    result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                except asyncio.CancelledError:
                    result_blocks.append(ToolResultContent(
                        tool_use_id=tool_id,
                        content="Task cancelled by user",
                        is_error=True,
                    ))
                    raise
                except Exception as exc:
                    log.exception("Tool %s failed", tool_name)
                    result_str = f"Error: {exc}"
                    result_blocks.append(ToolResultContent(
                        tool_use_id=tool_id, content=result_str, is_error=True,
                    ))
                    continue
                result_blocks.append(ToolResultContent(
                    tool_use_id=tool_id, content=result_str,
                ))

            tool_result_msg = Message(role=Role.TOOL_RESULT, content=result_blocks)
            self._history.append(tool_result_msg)
            await self.store.add_message_slimmed(
                self._session_id, Role.TOOL_RESULT.value,
                _content_to_json(result_blocks),
            )
            # Loop continues — next round sends tool results to LLM

    async def maybe_generate_title(self) -> None:
        """Trigger title generation if the session has no title yet."""
        if self._session_id is None:
            return
        session = await self.store.get_session(self._session_id)
        if session and not session.get("title"):
            asyncio.create_task(self._generate_title(self._session_id))

    # -- Title generation ------------------------------------------------

    async def _generate_title(self, session_id: int) -> None:
        """Generate a short title for the session (fire-and-forget)."""
        if not self.config.titles:
            return
        if self._titles_llm is None:
            self._titles_llm = _make_titles_llm(self.config.titles)

        rows = await self.store.get_messages(session_id, limit=4)
        if not rows:
            return
        conversation = ""
        for r in rows:
            content = r["content"]
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                continue
            conversation += f"{r['role']}: {text}\n"

        prompt = (
            "根据以下对话生成一个简短的标题（10字以内，不要引号）：\n\n"
            f"{conversation}"
        )

        for attempt, delay in enumerate(TITLE_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._titles_llm.chat(
                    [Message(role=Role.USER, content=prompt)],
                )
                title = _clean_title(resp.text_content())
                if title:
                    await self.store.update_session_title(session_id, title)
                    log.info("Session %d titled: %s", session_id, title)
                return
            except Exception as exc:
                # 429 rate limit — give up immediately, don't make it worse
                exc_str = str(exc).lower()
                if "429" in exc_str or "rate" in exc_str:
                    log.warning("Title generation hit rate limit, giving up")
                    return
                log.warning(
                    "Title generation attempt %d/%d failed",
                    attempt + 1, len(TITLE_RETRY_DELAYS),
                    exc_info=True,
                )
        log.warning("Title generation for session %d failed after all retries", session_id)

    async def regenerate_title(self, session_id: int) -> str | None:
        """Regenerate title (for /retitle).  Returns the new title or None."""
        if not self.config.titles:
            return None
        if self._titles_llm is None:
            self._titles_llm = _make_titles_llm(self.config.titles)

        rows = await self.store.get_messages(session_id, limit=6)
        if not rows:
            return None
        conversation = ""
        for r in rows:
            content = r["content"]
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                continue
            conversation += f"{r['role']}: {text}\n"

        prompt = (
            "根据以下对话生成一个简短的标题（10字以内，不要引号）：\n\n"
            f"{conversation}"
        )
        resp = await self._titles_llm.chat(
            [Message(role=Role.USER, content=prompt)],
        )
        title = _clean_title(resp.text_content())
        if title:
            await self.store.update_session_title(session_id, title)
        return title or None

    # -- Cleanup ---------------------------------------------------------

    async def close(self) -> None:
        for llm in self._llms.values():
            await llm.close()
        self._llms.clear()
        if self._titles_llm:
            await self._titles_llm.close()
            self._titles_llm = None
        if self._mcp:
            await self._mcp.close()
            self._mcp = None
        # Kill any running CLI
        for cli in self._cli_agents.values():
            if cli.is_running:
                await cli.kill()
