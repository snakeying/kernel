from __future__ import annotations
from typing import Any, AsyncIterator
import anthropic
from kernel.models.base import ContentBlock, ImageContent, LLM, LLMResponse, Message, Role, StreamChunk, TextContent, ToolDef, ToolResultContent, ToolUseContent

def _to_anthropic_content(blocks: list[ContentBlock] | str) -> list[dict[str, Any]] | str:
    if isinstance(blocks, str):
        return blocks
    out: list[dict[str, Any]] = []
    for b in blocks:
        if isinstance(b, TextContent):
            out.append({'type': 'text', 'text': b.text})
        elif isinstance(b, ImageContent):
            out.append({'type': 'image', 'source': {'type': 'base64', 'media_type': b.media_type, 'data': b.data}})
        elif isinstance(b, ToolUseContent):
            out.append({'type': 'tool_use', 'id': b.id, 'name': b.name, 'input': b.input})
        elif isinstance(b, ToolResultContent):
            out.append({'type': 'tool_result', 'tool_use_id': b.tool_use_id, 'content': b.content, 'is_error': b.is_error})
    return out

def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = 'user' if msg.role == Role.TOOL_RESULT else msg.role.value
        content = _to_anthropic_content(msg.content)
        out.append({'role': role, 'content': content})
    return out

def _to_anthropic_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [{'name': t.name, 'description': t.description, 'input_schema': t.parameters} for t in tools]

def _from_anthropic_content(blocks: list[dict[str, Any]]) -> list[ContentBlock]:
    out: list[ContentBlock] = []
    for b in blocks:
        btype = b.get('type')
        if btype == 'text':
            out.append(TextContent(text=b['text']))
        elif btype == 'tool_use':
            out.append(ToolUseContent(id=b['id'], name=b['name'], input=b['input']))
    return out

class ClaudeLLM(LLM):

    def __init__(self, api_key: str, default_model: str, max_tokens: int, *, api_base: str | None=None, headers: dict[str, str] | None=None) -> None:
        kwargs: dict[str, Any] = {'api_key': api_key}
        if api_base:
            kwargs['base_url'] = api_base
        if headers:
            kwargs['default_headers'] = headers
        self._client = anthropic.AsyncAnthropic(**kwargs)
        self._default_model = default_model
        self._max_tokens = max_tokens

    async def chat(self, messages: list[Message], *, system: str | None=None, tools: list[ToolDef] | None=None, model: str | None=None) -> LLMResponse:
        kwargs: dict[str, Any] = {'model': model or self._default_model, 'max_tokens': self._max_tokens, 'messages': _to_anthropic_messages(messages)}
        if system:
            kwargs['system'] = system
        if tools:
            kwargs['tools'] = _to_anthropic_tools(tools)
        resp = await self._client.messages.create(**kwargs)
        return LLMResponse(content=_from_anthropic_content([b.model_dump() for b in resp.content]), stop_reason=resp.stop_reason, usage={'input_tokens': resp.usage.input_tokens, 'output_tokens': resp.usage.output_tokens})

    async def chat_stream(self, messages: list[Message], *, system: str | None=None, tools: list[ToolDef] | None=None, model: str | None=None) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {'model': model or self._default_model, 'max_tokens': self._max_tokens, 'messages': _to_anthropic_messages(messages)}
        if system:
            kwargs['system'] = system
        if tools:
            kwargs['tools'] = _to_anthropic_tools(tools)
        async with self._client.messages.stream(**kwargs) as stream:
            current_tool_id: str | None = None
            current_tool_name: str | None = None
            tool_json_parts: list[str] = []
            async for event in stream:
                etype = event.type
                if etype == 'content_block_start':
                    block = event.content_block
                    if block.type == 'tool_use':
                        current_tool_id = block.id
                        current_tool_name = block.name
                        tool_json_parts = []
                    elif block.type == 'text':
                        pass
                elif etype == 'content_block_delta':
                    delta = event.delta
                    if delta.type == 'text_delta':
                        yield StreamChunk(text=delta.text)
                    elif delta.type == 'input_json_delta':
                        tool_json_parts.append(delta.partial_json)
                elif etype == 'content_block_stop':
                    if current_tool_id:
                        full_json = ''.join(tool_json_parts)
                        yield StreamChunk(tool_use_id=current_tool_id, tool_name=current_tool_name, tool_input_json=full_json)
                        current_tool_id = None
                        current_tool_name = None
                        tool_json_parts = []
                elif etype == 'message_delta':
                    yield StreamChunk(finish_reason=event.delta.stop_reason)

    async def close(self) -> None:
        await self._client.close()
