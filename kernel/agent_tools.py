from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from kernel.cli.claude_code import ClaudeCodeAgent
from kernel.cli.codex import CodexAgent
from kernel.mcp.client import MCPClient

log = logging.getLogger(__name__)


class AgentToolsMixin:
    def _init_cli_agents(self) -> None:
        output_dir = self.config.data_path / "cli_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, cli_cfg in self.config.cli.items():
            if name == "claude_code":
                self._cli_agents[name] = ClaudeCodeAgent(
                    command=cli_cfg.command, args=cli_cfg.args, output_dir=output_dir
                )
            elif name == "codex":
                self._cli_agents[name] = CodexAgent(
                    command=cli_cfg.command, args=cli_cfg.args, output_dir=output_dir
                )
            else:
                log.warning("Unknown CLI agent type: %s", name)

    def _register_builtin_tools(self) -> None:
        @self._registry.tool(
            "delegate_to_cli",
            description="当用户需要执行文件操作、代码编辑、项目分析、Shell 命令、浏览器操作等实际任务时使用。将任务委派给 CLI Agent（Claude Code 或 Codex）执行。默认在 tasks/ 下为每次任务创建子目录运行；如需在指定目录运行请传入 cwd。",
        )
        async def delegate_to_cli(
            task: str, cwd: str | None = None, cli: str | None = None
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
            "memory_search", description="搜索长期记忆。用于回忆用户偏好、历史信息等。"
        )
        async def memory_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
            return await self.store.memory_search(query, limit)

        @self._registry.tool("memory_list", description="列出所有长期记忆。")
        async def memory_list(limit: int = 200) -> list[dict[str, Any]]:
            return await self.store.memory_list(limit)

        @self._registry.tool("memory_delete", description="删除指定 ID 的长期记忆。")
        async def memory_delete(id: int) -> dict[str, Any]:
            ok = await self.store.memory_delete(id)
            return {"ok": ok}

        self._tools.update(self._registry.tool_defs())
        self._tool_handlers.update(self._registry.handlers())

    async def init_mcp(self) -> None:
        if not self.config.mcp_servers:
            return
        self._mcp = MCPClient(self.config.mcp_servers)
        await self._mcp.connect_all()
        self._register_mcp_tools()

    def _register_mcp_tools(self) -> None:
        if not self._mcp:
            return
        mcp_defs = self._mcp.get_tool_defs()
        mcp_client = self._mcp
        for name, tool_def in mcp_defs.items():

            async def _mcp_handler(_name: str = name, **kwargs: Any) -> str:
                return await mcp_client.call_tool(_name, kwargs)

            self._tools[name] = tool_def
            self._tool_handlers[name] = _mcp_handler
        if mcp_defs:
            log.info("Registered %d MCP tools", len(mcp_defs))

    async def _handle_delegate_to_cli(
        self, task: str, cwd: str | None = None, cli: str | None = None
    ) -> dict[str, Any]:
        cli_name = cli or "claude_code"
        agent = self._cli_agents.get(cli_name)
        if agent is None:
            available = ", ".join(self._cli_agents.keys()) or "none"
            return {
                "ok": False,
                "error": f"CLI '{cli_name}' not configured. Available: {available}",
            }
        if cwd:
            work_dir = cwd
        else:
            tasks_dir = self.config.default_workspace_path
            if tasks_dir.name.lower() != "tasks":
                tasks_dir = tasks_dir / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            sid = self._session_id or 0
            safe_cli = re.sub("[^A-Za-z0-9_-]", "_", cli_name)
            uid = uuid.uuid4().hex[:6]
            run_dir = tasks_dir / f"s{sid}_{safe_cli}_{ts}_{uid}"
            run_dir.mkdir(parents=True, exist_ok=True)
            work_dir = str(run_dir)
        self._active_cli = agent
        try:
            result = await agent.run(task, work_dir)
        except asyncio.CancelledError:
            return {"ok": False, "error": "Task cancelled by user"}
        finally:
            self._active_cli = None
        return result.to_dict()

    def cancel(self) -> None:
        self._cancelled = True
        if self._active_cli and self._active_cli.is_running:
            asyncio.create_task(self._active_cli.kill())

    def _check_cancel(self) -> None:
        if self._cancelled:
            self._cancelled = False
            raise asyncio.CancelledError("User cancelled")

    @property
    def active_cli_name(self) -> str | None:
        if self._active_cli and self._active_cli.is_running:
            return self._active_cli.name
        return None

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
        for cli in self._cli_agents.values():
            if cli.is_running:
                await cli.kill()

