"""Tool registry — decorator-based registration with JSON schema auto-generation.

Usage::

    registry = ToolRegistry()

    @registry.tool("delegate_to_cli", description="Delegate a task to CLI agent")
    async def delegate_to_cli(
        task: str,
        cwd: str | None = None,
        cli: Literal["claude_code", "codex"] | None = None,
    ) -> dict: ...

    tools = registry.tool_defs()       # list[ToolDef]
    handlers = registry.handlers()     # dict[name, handler]
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Literal, get_args, get_origin

from kernel.models.base import ToolDef

log = logging.getLogger(__name__)

# Python type → JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    # Handle None / missing
    if annotation is inspect.Parameter.empty or annotation is None:
        return {"type": "string"}

    # Simple types
    if annotation in _TYPE_MAP:
        return {"type": _TYPE_MAP[annotation]}

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Literal["a", "b"] → enum
    if origin is Literal:
        values = list(args)
        # Determine type from first value
        schema_type = _TYPE_MAP.get(type(values[0]), "string")
        return {"type": schema_type, "enum": values}

    # list[X] → array
    if origin is list:
        items = _python_type_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": items}

    # dict[str, X] → object
    if origin is dict:
        return {"type": "object"}

    # Optional[X] = Union[X, None] — unwrap
    if origin is type(str | None):  # types.UnionType (3.10+)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_schema(non_none[0])
        # Multi-type union — fall back to string
        return {"type": "string"}

    # typing.Union
    try:
        import typing
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return _python_type_to_schema(non_none[0])
    except Exception:
        pass

    return {"type": "string"}


def _build_parameters_schema(
    func: Callable[..., Any],
) -> dict[str, Any]:
    """Build a JSON Schema ``object`` from a function's type hints."""
    sig = inspect.signature(func)
    hints = func.__annotations__ if hasattr(func, "__annotations__") else {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        annotation = hints.get(name, param.annotation)
        prop = _python_type_to_schema(annotation)

        # Extract description from docstring param lines (simple heuristic)
        # Not implemented — tools use the top-level description

        properties[name] = prop

        # Required if no default
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


class ToolRegistry:
    """Collects tool definitions and their async handler functions."""

    def __init__(self) -> None:
        self._defs: dict[str, ToolDef] = {}
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}

    def tool(
        self,
        name: str,
        *,
        description: str,
    ) -> Callable:
        """Decorator to register an async function as a tool."""

        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            schema = _build_parameters_schema(func)
            self._defs[name] = ToolDef(
                name=name,
                description=description,
                parameters=schema,
            )
            self._handlers[name] = func
            log.debug("Registered tool: %s", name)
            return func

        return decorator

    def register(
        self,
        name: str,
        *,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[Any]],
    ) -> None:
        """Programmatically register a tool (e.g. for MCP tools)."""
        self._defs[name] = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
        )
        self._handlers[name] = handler

    def tool_defs(self) -> dict[str, ToolDef]:
        return dict(self._defs)

    def handlers(self) -> dict[str, Callable[..., Awaitable[Any]]]:
        return dict(self._handlers)

    def unregister(self, name: str) -> None:
        self._defs.pop(name, None)
        self._handlers.pop(name, None)
