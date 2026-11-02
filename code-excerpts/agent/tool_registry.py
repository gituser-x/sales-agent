# Source excerpt; private application dependencies are intentionally omitted.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Type

from pydantic import BaseModel

from app.agent.tool_context import ToolContext

ToolHandler = Callable[[ToolContext, BaseModel], BaseModel]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    version: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    handler: ToolHandler
    description: str = ""
    permissions: set[str] = field(default_factory=set)
    read_only: bool = True
    timeout_ms: int = 3000
    retry: int = 0
    redact_input: bool = False
    redact_output: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._items:
            raise ValueError(f"tool already registered: {spec.name}")
        self._items[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"tool not registered: {name}") from exc

    def list(self) -> list[ToolSpec]:
        return list(self._items.values())


registry = ToolRegistry()


def register_tool(
    *,
    name: str,
    version: str,
    input_schema: Type[BaseModel],
    output_schema: Type[BaseModel],
    handler: ToolHandler,
    description: str = "",
    permissions: set[str] | None = None,
    read_only: bool = True,
    timeout_ms: int = 3000,
    retry: int = 0,
    redact_input: bool = False,
    redact_output: bool = False,
) -> None:
    registry.register(ToolSpec(
        name=name,
        version=version,
        input_schema=input_schema,
        output_schema=output_schema,
        handler=handler,
        description=description,
        permissions=permissions or set(),
        read_only=read_only,
        timeout_ms=timeout_ms,
        retry=retry,
        redact_input=redact_input,
        redact_output=redact_output,
    ))


def ensure_default_tools_registered() -> None:
    """Import tool modules so their register() side effects run exactly once."""
    # Tool modules call register_tool in register() functions.  Importing here is
    # cheap and keeps registry bootstrap explicit for tests and runtime.
    from app.agent.tools import manual_tools, kb_tools, policy_tools, course_tools, sales_tools, customer_tools, task_tools, kb_gap_tools

    for module in (manual_tools, kb_tools, policy_tools, course_tools, sales_tools, customer_tools, task_tools, kb_gap_tools):
        if hasattr(module, "register"):
            module.register()
