"""MCP client — connect to MCP servers, load tools, call tools.

Handles both HTTP (streamable HTTP with SSE fallback) and stdio transports.
Tools are exposed as ``{server_name}.{tool_name}`` to avoid collisions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, types as mcp_types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from kernel.config import MCPServerConfig
from kernel.models.base import ToolDef

log = logging.getLogger(__name__)

# Exponential backoff for reconnection
_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 60.0
_MAX_RECONNECT_ATTEMPTS = 5


class _ServerConnection:
    """Manages a single MCP server connection."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[mcp_types.Tool] = []
        self._connected = False

    async def connect(self) -> bool:
        """Establish connection and list tools. Returns True on success."""
        try:
            await self._do_connect()
            self._connected = True
            log.info("MCP [%s] connected — %d tools", self.name, len(self._tools))
            return True
        except Exception:
            log.warning("MCP [%s] connection failed", self.name, exc_info=True)
            self._connected = False
            return False

    async def _do_connect(self) -> None:
        self._exit_stack = AsyncExitStack()

        if self.config.type == "stdio":
            if not self.config.command:
                raise ValueError(f"MCP server '{self.name}' is stdio but has no command")
            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args or [],
            )
            streams = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
            read, write = streams[0], streams[1]
        else:
            # HTTP (streamable HTTP)
            if not self.config.url:
                raise ValueError(f"MCP server '{self.name}' is http but has no url")

            headers = dict(self.config.headers) if self.config.headers else {}
            http_client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(30, read=300),
                follow_redirects=True,
            )
            await self._exit_stack.enter_async_context(http_client)

            # streamable_http_client may return 2 or 3 values depending on SDK version
            streams = await self._exit_stack.enter_async_context(
                streamable_http_client(self.config.url, http_client=http_client)
            )
            read, write = streams[0], streams[1]

        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

        # List tools
        result = await self._session.list_tools()
        self._tools = list(result.tools)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server. Returns text result."""
        if not self._session or not self._connected:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")

        try:
            result = await self._session.call_tool(tool_name, arguments=arguments)
        except Exception:
            # Connection might be dead — mark as disconnected
            self._connected = False
            raise

        # Extract text from result content
        parts: list[str] = []
        if result.content:
            for block in result.content:
                if isinstance(block, mcp_types.TextContent):
                    parts.append(block.text)
                elif hasattr(block, "text"):
                    parts.append(str(block.text))
                else:
                    parts.append(str(block))

        text = "\n".join(parts) if parts else "(no output)"

        if result.isError:
            return f"Error: {text}"
        return text

    @property
    def tools(self) -> list[mcp_types.Tool]:
        return self._tools

    @property
    def connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        self._connected = False
        self._session = None
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                log.debug("Error closing MCP [%s]", self.name, exc_info=True)
            self._exit_stack = None


def _mcp_tool_to_tooldef(server_name: str, tool: mcp_types.Tool) -> ToolDef:
    """Convert an MCP tool to our internal ToolDef."""
    name = f"{server_name}.{tool.name}"
    description = tool.description or f"{server_name}/{tool.name}"
    # MCP tool inputSchema is already a JSON Schema object
    parameters = tool.inputSchema if isinstance(tool.inputSchema, dict) else {"type": "object", "properties": {}}
    return ToolDef(name=name, description=description, parameters=parameters)


class MCPClient:
    """Manages connections to multiple MCP servers."""

    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs = configs
        self._connections: dict[str, _ServerConnection] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}

    async def connect_all(self) -> None:
        """Connect to all configured MCP servers. Failures are logged, not raised."""
        for cfg in self._configs:
            conn = _ServerConnection(cfg)
            self._connections[cfg.name] = conn
            ok = await conn.connect()
            if not ok:
                log.warning("MCP [%s] skipped — connection failed", cfg.name)

    def get_tool_defs(self) -> dict[str, ToolDef]:
        """Return all tool definitions from connected servers."""
        defs: dict[str, ToolDef] = {}
        for conn in self._connections.values():
            if not conn.connected:
                continue
            for tool in conn.tools:
                td = _mcp_tool_to_tooldef(conn.name, tool)
                defs[td.name] = td
        return defs

    async def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by its qualified name (``server.tool_name``).

        If the server is disconnected, attempts one reconnection before failing.
        """
        dot = qualified_name.find(".")
        if dot < 0:
            return f"Error: invalid tool name '{qualified_name}' (expected server.tool)"
        server_name = qualified_name[:dot]
        tool_name = qualified_name[dot + 1:]

        conn = self._connections.get(server_name)
        if conn is None:
            return f"Error: MCP server '{server_name}' not configured"

        # Try call; on failure, reconnect once and retry
        for attempt in range(2):
            if not conn.connected and attempt == 0:
                log.info("MCP [%s] reconnecting …", server_name)
                ok = await conn.connect()
                if not ok:
                    return f"Error: MCP server '{server_name}' reconnection failed"

            try:
                return await conn.call_tool(tool_name, arguments)
            except Exception as exc:
                if attempt == 0:
                    log.warning(
                        "MCP [%s] tool call failed, will reconnect: %s",
                        server_name, exc,
                    )
                    await conn.close()
                    ok = await conn.connect()
                    if not ok:
                        return f"Error: MCP server '{server_name}' reconnection failed"
                else:
                    log.error("MCP [%s] tool call failed after reconnect", server_name, exc_info=True)
                    return f"Error calling {qualified_name}: {exc}"

        return f"Error: unexpected failure calling {qualified_name}"

    async def close(self) -> None:
        """Close all server connections."""
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()

        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
