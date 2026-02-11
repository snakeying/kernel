"""Provider-agnostic LLM types and abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


# ---------------------------------------------------------------------------
# Internal message types (provider-agnostic)
# ---------------------------------------------------------------------------


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"


class ContentType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


@dataclass
class TextContent:
    text: str
    type: str = "text"


@dataclass
class ImageContent:
    """Base64-encoded image."""
    media_type: str  # e.g. "image/jpeg"
    data: str  # base64
    type: str = "image"


@dataclass
class ToolUseContent:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class ToolResultContent:
    tool_use_id: str
    content: str  # text result
    is_error: bool = False
    type: str = "tool_result"


ContentBlock = TextContent | ImageContent | ToolUseContent | ToolResultContent


@dataclass
class Message:
    role: Role
    content: list[ContentBlock] | str

    def text_content(self) -> str:
        """Extract concatenated text from content blocks."""
        if isinstance(self.content, str):
            return self.content
        parts = []
        for block in self.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return "\n".join(parts)

    def has_tool_use(self) -> bool:
        if isinstance(self.content, str):
            return False
        return any(isinstance(b, ToolUseContent) for b in self.content)

    def tool_use_blocks(self) -> list[ToolUseContent]:
        if isinstance(self.content, str):
            return []
        return [b for b in self.content if isinstance(b, ToolUseContent)]


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """JSON-Schema based tool definition."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@dataclass
class StreamChunk:
    """Incremental piece of an LLM response."""
    text: str = ""
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input_json: str = ""
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# Complete response
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Complete (non-streaming or aggregated) LLM response."""
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def text_content(self) -> str:
        parts = []
        for block in self.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return "\n".join(parts)

    def has_tool_use(self) -> bool:
        return any(isinstance(b, ToolUseContent) for b in self.content)

    def tool_use_blocks(self) -> list[ToolUseContent]:
        return [b for b in self.content if isinstance(b, ToolUseContent)]


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class LLM(ABC):
    """Abstract LLM provider interface."""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDef] | None = None,
        model: str | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDef] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    @abstractmethod
    async def close(self) -> None: ...
