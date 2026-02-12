from __future__ import annotations
from typing import Any, Awaitable, Callable
from kernel.agent_chat import AgentChatMixin
from kernel.agent_history import AgentHistoryMixin
from kernel.agent_sessions import AgentSessionsMixin
from kernel.agent_titles import AgentTitlesMixin
from kernel.agent_tools import AgentToolsMixin
from kernel.config import Config
from kernel.memory.store import Store
from kernel.mcp.client import MCPClient
from kernel.models.base import LLM, Message, ToolDef
from kernel.tools.registry import ToolRegistry
from kernel.cli.base import CLIAgent

class Agent(
    AgentToolsMixin, AgentSessionsMixin, AgentHistoryMixin, AgentChatMixin, AgentTitlesMixin
):
    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store
        self._current_provider_name: str = config.general.default_provider
        self._current_model: str | None = None
        self._session_id: int | None = None
        self._history: list[Message] = []
        self._cancelled = False
        self._llms: dict[str, LLM] = {}
        self._titles_llm: LLM | None = None
        self._registry = ToolRegistry()
        self._tools: dict[str, ToolDef] = {}
        self._tool_handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._cli_agents: dict[str, CLIAgent] = {}
        self._active_cli: CLIAgent | None = None
        self._init_cli_agents()
        self._mcp: MCPClient | None = None
        self._soul: str = ""
        soul_path = config.config_dir / "SOUL.md"
        if soul_path.exists():
            self._soul = soul_path.read_text(encoding="utf-8")
        self._register_builtin_tools()
