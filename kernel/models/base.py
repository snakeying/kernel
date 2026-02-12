from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

class Role(str, Enum):
    USER = 'user'
    ASSISTANT = 'assistant'
    TOOL_RESULT = 'tool_result'

@dataclass
class TextContent:
    text: str
    type: str = 'text'

@dataclass
class ImageContent:
    media_type: str
    data: str
    type: str = 'image'

@dataclass
class ToolUseContent:
    id: str
    name: str
    input: dict[str, Any]
    type: str = 'tool_use'

@dataclass
class ToolResultContent:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = 'tool_result'
ContentBlock = TextContent | ImageContent | ToolUseContent | ToolResultContent

@dataclass
class Message:
    role: Role
    content: list[ContentBlock] | str

    def text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        parts = []
        for block in self.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return '\n'.join(parts)

@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]

@dataclass
class StreamChunk:
    text: str = ''
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input_json: str = ''
    finish_reason: str | None = None

@dataclass
class LLMResponse:
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def text_content(self) -> str:
        parts = []
        for block in self.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return '\n'.join(parts)

class LLM(ABC):

    @abstractmethod
    async def chat(self, messages: list[Message], *, system: str | None=None, tools: list[ToolDef] | None=None, model: str | None=None) -> LLMResponse:
        ...

    @abstractmethod
    async def chat_stream(self, messages: list[Message], *, system: str | None=None, tools: list[ToolDef] | None=None, model: str | None=None) -> AsyncIterator[StreamChunk]:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...
