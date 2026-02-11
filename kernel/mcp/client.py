from __future__ import annotations
import hashlib
import logging
import re
from contextlib import AsyncExitStack
from typing import Any
import httpx
from mcp import ClientSession, types as mcp_types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from kernel.config import MCPServerConfig
from kernel.models.base import ToolDef
log = logging.getLogger(__name__)
_INVALID_TOOL_CHAR_RE = re.compile('[^A-Za-z0-9_-]')
_MAX_TOOL_NAME_LEN = 64

def _safe_tool_name(server_name: str, tool_name: str) -> str:
    raw = f'mcp_{server_name}__{tool_name}'
    safe = _INVALID_TOOL_CHAR_RE.sub('_', raw)
    if len(safe) <= _MAX_TOOL_NAME_LEN:
        return safe
    digest = hashlib.sha1(safe.encode('utf-8')).hexdigest()[:8]
    keep = _MAX_TOOL_NAME_LEN - (1 + len(digest))
    return f'{safe[:keep]}_{digest}'

def _dedupe_tool_name(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    digest = hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]
    keep = _MAX_TOOL_NAME_LEN - (1 + len(digest))
    candidate = f'{name[:keep]}_{digest}'
    i = 2
    while candidate in used:
        digest = hashlib.sha1(f'{name}_{i}'.encode('utf-8')).hexdigest()[:8]
        candidate = f'{name[:keep]}_{digest}'
        i += 1
    return candidate

class _ServerConnection:

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[mcp_types.Tool] = []
        self._connected = False

    async def connect(self) -> bool:
        try:
            await self.close()
            await self._do_connect()
            self._connected = True
            log.info('MCP [%s] connected — %d tools', self.name, len(self._tools))
            return True
        except Exception:
            log.warning('MCP [%s] connection failed', self.name, exc_info=True)
            await self.close()
            return False

    async def _do_connect(self) -> None:
        self._exit_stack = AsyncExitStack()
        if self.config.type == 'stdio':
            if not self.config.command:
                raise ValueError(f"MCP server '{self.name}' is stdio but has no command")
            params = StdioServerParameters(command=self.config.command, args=self.config.args or [])
            streams = await self._exit_stack.enter_async_context(stdio_client(params))
            read, write = (streams[0], streams[1])
        else:
            if not self.config.url:
                raise ValueError(f"MCP server '{self.name}' is http but has no url")
            headers = dict(self.config.headers) if self.config.headers else {}
            http_client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=300), follow_redirects=True)
            await self._exit_stack.enter_async_context(http_client)
            streams = await self._exit_stack.enter_async_context(streamable_http_client(self.config.url, http_client=http_client))
            read, write = (streams[0], streams[1])
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        result = await self._session.list_tools()
        self._tools = list(result.tools)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not self._session or not self._connected:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        try:
            result = await self._session.call_tool(tool_name, arguments=arguments)
        except Exception:
            self._connected = False
            raise
        parts: list[str] = []
        if result.content:
            for block in result.content:
                if isinstance(block, mcp_types.TextContent):
                    parts.append(block.text)
                elif hasattr(block, 'text'):
                    parts.append(str(block.text))
                else:
                    parts.append(str(block))
        text = '\n'.join(parts) if parts else '(no output)'
        if result.isError:
            return f'Error: {text}'
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
                log.debug('Error closing MCP [%s]', self.name, exc_info=True)
            self._exit_stack = None

class MCPClient:

    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs = configs
        self._connections: dict[str, _ServerConnection] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}

    async def connect_all(self) -> None:
        for cfg in self._configs:
            conn = _ServerConnection(cfg)
            self._connections[cfg.name] = conn
            ok = await conn.connect()
            if not ok:
                log.warning('MCP [%s] skipped — connection failed', cfg.name)

    def get_tool_defs(self) -> dict[str, ToolDef]:
        self._tool_map.clear()
        defs: dict[str, ToolDef] = {}
        used: set[str] = set()
        for conn in self._connections.values():
            if not conn.connected:
                continue
            for tool in conn.tools:
                safe_name = _safe_tool_name(conn.name, tool.name)
                safe_name = _dedupe_tool_name(safe_name, used)
                used.add(safe_name)
                description = tool.description or f'{conn.name}/{tool.name}'
                description = f'[{conn.name}.{tool.name}] {description}'
                parameters = tool.inputSchema if isinstance(tool.inputSchema, dict) else {'type': 'object', 'properties': {}}
                self._tool_map[safe_name] = (conn.name, tool.name)
                defs[safe_name] = ToolDef(name=safe_name, description=description, parameters=parameters)
        return defs

    async def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        server_name: str
        tool_name: str
        if '.' in qualified_name:
            dot = qualified_name.find('.')
            server_name = qualified_name[:dot]
            tool_name = qualified_name[dot + 1:]
        else:
            mapped = self._tool_map.get(qualified_name)
            if not mapped:
                return f"Error: unknown MCP tool '{qualified_name}'"
            server_name, tool_name = mapped
        conn = self._connections.get(server_name)
        if conn is None:
            return f"Error: MCP server '{server_name}' not configured"
        for attempt in range(2):
            if not conn.connected and attempt == 0:
                log.info('MCP [%s] reconnecting …', server_name)
                ok = await conn.connect()
                if not ok:
                    return f"Error: MCP server '{server_name}' reconnection failed"
            try:
                return await conn.call_tool(tool_name, arguments)
            except Exception as exc:
                if attempt == 0:
                    log.warning('MCP [%s] tool call failed, will reconnect: %s', server_name, exc)
                    await conn.close()
                    ok = await conn.connect()
                    if not ok:
                        return f"Error: MCP server '{server_name}' reconnection failed"
                else:
                    log.error('MCP [%s] tool call failed after reconnect', server_name, exc_info=True)
                    return f'Error calling {qualified_name}: {exc}'
        return f'Error: unexpected failure calling {qualified_name}'

    async def close(self) -> None:
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
