from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from kernel.agent_content import _content_to_json
from kernel.models.base import (
    ContentBlock,
    Message,
    Role,
    StreamChunk,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 25


class AgentChatMixin:
    async def _build_system_prompt(self, user_query: str = "") -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        parts = []
        if self._soul:
            parts.append(self._soul)
        tz = ZoneInfo(self.config.general.timezone)
        now = datetime.now(tz)
        parts.append(
            f"## 当前时间\n{now.strftime('%Y-%m-%dT%H:%M:%S%z')}（{self.config.general.timezone}）"
        )
        k = self.config.general.memory_recall_k
        if k > 0:
            parts.append(
                "## 长期记忆（工具访问）\n"
                "你可以通过 memory_search / memory_list 访问长期记忆。\n"
                "在回答前先判断是否需要记忆：当问题可能依赖用户偏好、身份信息、历史约定或长期计划时，优先调用 memory_search。\n"
                "如果问题明显不依赖长期信息，请不要调用。\n"
                "query 请用你自己改写后的关键词（名词为主，避免照抄原句）。\n"
                f"如果 memory_search 结果为空但仍可能相关，再调用 memory_list(limit={k}) 快速浏览。"
            )
        return "\n\n".join(parts) if parts else ""

    async def chat(self, user_content: list[ContentBlock] | str) -> AsyncIterator[StreamChunk]:
        self._cancelled = False
        await self.ensure_session()
        assert self._session_id is not None
        user_msg = Message(role=Role.USER, content=user_content)
        self._history.append(user_msg)
        await self.store.add_message_slimmed(
            self._session_id, Role.USER.value, _content_to_json(user_content)
        )
        llm = self._get_llm()
        if isinstance(user_content, str):
            _user_query = user_content
        elif isinstance(user_content, list):
            _user_query = " ".join(
                (b.text for b in user_content if isinstance(b, TextContent))
            )
        else:
            _user_query = ""
        if len(_user_query) > 2000:
            _user_query = _user_query[:2000]
        system = await self._build_system_prompt(_user_query)
        tools_list = list(self._tools.values()) if self._tools else None
        for _round in range(MAX_TOOL_ROUNDS):
            self._check_cancel()
            truncated = self._truncate_history(self._history)
            text_parts: list[str] = []
            tool_chunks: list[StreamChunk] = []
            finish_reason: str | None = None
            async for chunk in llm.chat_stream(
                truncated, system=system, tools=tools_list, model=self._current_model
            ):
                self._check_cancel()
                if chunk.text:
                    text_parts.append(chunk.text)
                    yield chunk
                if chunk.tool_use_id:
                    tool_chunks.append(chunk)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
            assistant_blocks: list[ContentBlock] = []
            full_text = "".join(text_parts)
            if full_text:
                assistant_blocks.append(TextContent(text=full_text))
            for tc in tool_chunks:
                try:
                    tool_input = json.loads(tc.tool_input_json) if tc.tool_input_json else {}
                except json.JSONDecodeError:
                    tool_input = {}
                assistant_blocks.append(
                    ToolUseContent(
                        id=tc.tool_use_id or "",
                        name=tc.tool_name or "",
                        input=tool_input,
                    )
                )
            assistant_msg = Message(role=Role.ASSISTANT, content=assistant_blocks)
            self._history.append(assistant_msg)
            await self.store.add_message_slimmed(
                self._session_id, Role.ASSISTANT.value, _content_to_json(assistant_blocks)
            )
            if not tool_chunks or finish_reason not in ("tool_use", "tool_calls"):
                yield StreamChunk(finish_reason=finish_reason or "end_turn")
                break
            result_blocks: list[ContentBlock] = []
            for tc in tool_chunks:
                tool_name = tc.tool_name or ""
                tool_id = tc.tool_use_id or ""
                handler = self._tool_handlers.get(tool_name)
                if handler is None:
                    result_blocks.append(
                        ToolResultContent(
                            tool_use_id=tool_id,
                            content=f"Error: unknown tool '{tool_name}'",
                            is_error=True,
                        )
                    )
                    continue
                try:
                    tool_input = json.loads(tc.tool_input_json) if tc.tool_input_json else {}
                except json.JSONDecodeError:
                    tool_input = {}
                yield StreamChunk(text="", tool_use_id=tool_id, tool_name=tool_name)
                try:
                    result = await handler(**tool_input)
                    result_str = (
                        json.dumps(result, ensure_ascii=False)
                        if not isinstance(result, str)
                        else result
                    )
                except asyncio.CancelledError:
                    result_blocks.append(
                        ToolResultContent(
                            tool_use_id=tool_id,
                            content="Task cancelled by user",
                            is_error=True,
                        )
                    )
                    raise
                except Exception as exc:
                    log.exception("Tool %s failed", tool_name)
                    result_str = f"Error: {exc}"
                    result_blocks.append(
                        ToolResultContent(
                            tool_use_id=tool_id, content=result_str, is_error=True
                        )
                    )
                    continue
                result_blocks.append(
                    ToolResultContent(tool_use_id=tool_id, content=result_str)
                )
            tool_result_msg = Message(role=Role.TOOL_RESULT, content=result_blocks)
            self._history.append(tool_result_msg)
            await self.store.add_message_slimmed(
                self._session_id, Role.TOOL_RESULT.value, _content_to_json(result_blocks)
            )
        try:
            self._slim_history_inplace()
        except Exception:
            log.debug("History slimming failed", exc_info=True)
