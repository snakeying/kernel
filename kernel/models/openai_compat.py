from __future__ import annotations
import json
from typing import Any, AsyncIterator
import openai
from kernel.models.base import ContentBlock, ImageContent, LLM, LLMResponse, Message, Role, StreamChunk, TextContent, ToolDef, ToolResultContent, ToolUseContent

def _to_openai_content(blocks: list[ContentBlock] | str) -> str | list[dict[str, Any]]:
    if isinstance(blocks, str):
        return blocks
    text_only = all((isinstance(b, TextContent) for b in blocks))
    if text_only:
        return '\n'.join((b.text for b in blocks if isinstance(b, TextContent)))
    out: list[dict[str, Any]] = []
    for b in blocks:
        if isinstance(b, TextContent):
            out.append({'type': 'text', 'text': b.text})
        elif isinstance(b, ImageContent):
            out.append({'type': 'image_url', 'image_url': {'url': f'data:{b.media_type};base64,{b.data}'}})
    return out

def _to_openai_messages(messages: list[Message], *, system: str | None=None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({'role': 'system', 'content': system})
    for msg in messages:
        if msg.role == Role.USER:
            out.append({'role': 'user', 'content': _to_openai_content(msg.content)})
            continue
        if msg.role == Role.ASSISTANT:
            entry: dict[str, Any] = {'role': 'assistant'}
            if isinstance(msg.content, str):
                entry['content'] = msg.content
            else:
                text_parts = [b.text for b in msg.content if isinstance(b, TextContent)]
                tool_uses = [b for b in msg.content if isinstance(b, ToolUseContent)]
                entry['content'] = '\n'.join(text_parts) if text_parts else None
                if tool_uses:
                    entry['tool_calls'] = [{'id': tu.id, 'type': 'function', 'function': {'name': tu.name, 'arguments': json.dumps(tu.input)}} for tu in tool_uses]
            out.append(entry)
            continue
        if msg.role == Role.TOOL_RESULT:
            if isinstance(msg.content, str):
                continue
            for b in msg.content:
                if isinstance(b, ToolResultContent):
                    out.append({'role': 'tool', 'tool_call_id': b.tool_use_id, 'content': b.content})
            continue
    return out

def _to_openai_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [{'type': 'function', 'function': {'name': t.name, 'description': t.description, 'parameters': t.parameters}} for t in tools]

class OpenAICompatLLM(LLM):

    def __init__(self, api_key: str, default_model: str, *, api_base: str | None=None, max_tokens: int | None=None, headers: dict[str, str] | None=None) -> None:
        kwargs: dict[str, Any] = {'api_key': api_key}
        if api_base:
            kwargs['base_url'] = api_base
        if headers:
            kwargs['default_headers'] = headers
        self._client = openai.AsyncOpenAI(**kwargs)
        self._default_model = default_model
        self._max_tokens = max_tokens

    async def chat(self, messages: list[Message], *, system: str | None=None, tools: list[ToolDef] | None=None, model: str | None=None) -> LLMResponse:
        kwargs: dict[str, Any] = {'model': model or self._default_model, 'messages': _to_openai_messages(messages, system=system)}
        if self._max_tokens is not None:
            kwargs['max_tokens'] = self._max_tokens
        if tools:
            kwargs['tools'] = _to_openai_tools(tools)
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        content: list[ContentBlock] = []
        if msg.content:
            content.append(TextContent(text=msg.content))
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except Exception:
                    args = {}
                content.append(ToolUseContent(id=tc.id, name=tc.function.name, input=args))
        return LLMResponse(content=content, stop_reason=choice.finish_reason, usage={'input_tokens': resp.usage.prompt_tokens if resp.usage else 0, 'output_tokens': resp.usage.completion_tokens if resp.usage else 0})

    async def chat_stream(self, messages: list[Message], *, system: str | None=None, tools: list[ToolDef] | None=None, model: str | None=None) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {'model': model or self._default_model, 'messages': _to_openai_messages(messages, system=system), 'stream': True}
        if self._max_tokens is not None:
            kwargs['max_tokens'] = self._max_tokens
        if tools:
            kwargs['tools'] = _to_openai_tools(tools)
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish = chunk.choices[0].finish_reason
            if delta.content:
                yield StreamChunk(text=delta.content)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {'id': tc_delta.id or '', 'name': '', 'arguments': []}
                    acc = tool_calls_acc[idx]
                    if tc_delta.id:
                        acc['id'] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            acc['name'] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            acc['arguments'].append(tc_delta.function.arguments)
            if finish:
                for _idx in sorted(tool_calls_acc):
                    acc = tool_calls_acc[_idx]
                    yield StreamChunk(tool_use_id=acc['id'], tool_name=acc['name'], tool_input_json=''.join(acc['arguments']))
                tool_calls_acc.clear()
                yield StreamChunk(finish_reason=finish)

    async def close(self) -> None:
        await self._client.close()
