from __future__ import annotations

from typing import Any

from kernel.models.base import (
    ContentBlock,
    ImageContent,
    Message,
    Role,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)


def _content_to_json(content: list[ContentBlock] | str) -> Any:
    if isinstance(content, str):
        return content
    out: list[dict[str, Any]] = []
    for b in content:
        if isinstance(b, TextContent):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ImageContent):
            out.append({"type": "image", "media_type": b.media_type, "data": b.data})
        elif isinstance(b, ToolUseContent):
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, ToolResultContent):
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                    "is_error": b.is_error,
                }
            )
    return out


def _json_to_content(data: Any) -> list[ContentBlock] | str:
    if isinstance(data, str):
        return data
    blocks: list[ContentBlock] = []
    for d in data:
        t = d.get("type")
        if t == "text":
            blocks.append(TextContent(text=d["text"]))
        elif t == "image":
            blocks.append(ImageContent(media_type=d["media_type"], data=d["data"]))
        elif t == "tool_use":
            name = d["name"]
            if isinstance(name, str) and "." in name and (not name.startswith("mcp_")):
                server, tool = name.split(".", 1)
                try:
                    from kernel.mcp.client import _safe_tool_name

                    name = _safe_tool_name(server, tool)
                except Exception:
                    name = f"mcp_{server}__{tool}".replace(".", "_")
            blocks.append(ToolUseContent(id=d["id"], name=name, input=d["input"]))
        elif t == "tool_result":
            blocks.append(
                ToolResultContent(
                    tool_use_id=d["tool_use_id"],
                    content=d["content"],
                    is_error=d.get("is_error", False),
                )
            )
    return blocks


def _json_to_message(row: dict[str, Any]) -> Message:
    role = Role(row["role"])
    content = _json_to_content(row["content"])
    return Message(role=role, content=content)

