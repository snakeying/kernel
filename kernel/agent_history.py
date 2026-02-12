from __future__ import annotations
from kernel.agent_content import _content_to_json, _json_to_content
from kernel.memory.store import Store
from kernel.models.base import (
    ContentBlock,
    Message,
    Role,
    ToolResultContent,
    ToolUseContent,
)

class AgentHistoryMixin:
    def _tool_safe_history(self, messages: list[Message]) -> list[Message]:
        if not messages:
            return messages
        tool_result_ids: set[str] = set()
        for msg in messages:
            if msg.role != Role.TOOL_RESULT or not isinstance(msg.content, list):
                continue
            for block in msg.content:
                if isinstance(block, ToolResultContent) and block.tool_use_id:
                    tool_result_ids.add(block.tool_use_id)
        cleaned: list[Message] = []
        tool_use_ids: set[str] = set()
        for msg in messages:
            if msg.role == Role.ASSISTANT and isinstance(msg.content, list):
                new_blocks: list[ContentBlock] = []
                for block in msg.content:
                    if isinstance(block, ToolUseContent):
                        if block.id and block.id in tool_result_ids:
                            new_blocks.append(block)
                            tool_use_ids.add(block.id)
                        continue
                    new_blocks.append(block)
                if new_blocks:
                    cleaned.append(Message(role=msg.role, content=new_blocks))
                continue
            cleaned.append(msg)

        final: list[Message] = []
        for msg in cleaned:
            if msg.role != Role.TOOL_RESULT or not isinstance(msg.content, list):
                final.append(msg)
                continue
            new_blocks = [
                b
                for b in msg.content
                if isinstance(b, ToolResultContent) and b.tool_use_id in tool_use_ids
            ]
            if new_blocks:
                final.append(Message(role=msg.role, content=new_blocks))
        return final

    def _truncate_history(self, messages: list[Message]) -> list[Message]:
        rounds = self.config.general.context_rounds
        if rounds <= 0:
            start = 0
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].role == Role.USER:
                    start = i
                    break
            return self._tool_safe_history(messages[start:])
        max_msgs = rounds * 2
        truncated = messages if len(messages) <= max_msgs else messages[-max_msgs:]
        return self._tool_safe_history(truncated)

    def _slim_history_inplace(self) -> None:
        slimmed: list[Message] = []
        for msg in self._history:
            raw = _content_to_json(msg.content)
            slimmed_json = Store.slim_content(msg.role.value, raw)
            slimmed_content = _json_to_content(slimmed_json)
            slimmed.append(Message(role=msg.role, content=slimmed_content))
        self._history = slimmed
