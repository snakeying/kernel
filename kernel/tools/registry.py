from __future__ import annotations
import inspect
import logging
from typing import Any, Awaitable, Callable, Literal, get_args, get_origin, get_type_hints
from kernel.models.base import ToolDef
log = logging.getLogger(__name__)
_TYPE_MAP: dict[type, str] = {str: 'string', int: 'integer', float: 'number', bool: 'boolean'}

def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is None:
        return {'type': 'string'}
    if annotation in _TYPE_MAP:
        return {'type': _TYPE_MAP[annotation]}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        values = list(args)
        schema_type = _TYPE_MAP.get(type(values[0]), 'string')
        return {'type': schema_type, 'enum': values}
    if origin is list:
        items = _python_type_to_schema(args[0]) if args else {'type': 'string'}
        return {'type': 'array', 'items': items}
    if origin is dict:
        return {'type': 'object'}
    if origin is type(str | None):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_schema(non_none[0])
        return {'type': 'string'}
    try:
        import typing
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return _python_type_to_schema(non_none[0])
    except Exception:
        pass
    return {'type': 'string'}

def _build_parameters_schema(func: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = func.__annotations__ if hasattr(func, '__annotations__') else {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ('self', 'cls'):
            continue
        annotation = hints.get(name, param.annotation)
        prop = _python_type_to_schema(annotation)
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {'type': 'object', 'properties': properties}
    if required:
        schema['required'] = required
    return schema

class ToolRegistry:

    def __init__(self) -> None:
        self._defs: dict[str, ToolDef] = {}
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}

    def tool(self, name: str, *, description: str) -> Callable:

        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            schema = _build_parameters_schema(func)
            self._defs[name] = ToolDef(name=name, description=description, parameters=schema)
            self._handlers[name] = func
            log.debug('Registered tool: %s', name)
            return func
        return decorator

    def tool_defs(self) -> dict[str, ToolDef]:
        return dict(self._defs)

    def handlers(self) -> dict[str, Callable[..., Awaitable[Any]]]:
        return dict(self._handlers)
