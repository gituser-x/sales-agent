# Source excerpt; private application dependencies are intentionally omitted.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass(slots=True)
class ToolContext:
    """Per-request execution context for production Agent tools.

    Tools must receive this context instead of creating their own DB sessions.
    It carries actor, tenant, session, request and trace information so tool
    calls can be audited and constrained consistently.
    """

    db: Session
    user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    customer_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    permissions: set[str] = field(default_factory=set)
    agent_run_id: int | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    step_index: int = 0

    def next_step_index(self) -> int:
        self.step_index += 1
        return self.step_index

    def has_permission(self, permission: str | None) -> bool:
        if not permission:
            return True
        return "*" in self.permissions or permission in self.permissions

    def child(self, **updates: Any) -> "ToolContext":
        data = {
            "db": self.db,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "customer_id": self.customer_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "permissions": set(self.permissions),
            "agent_run_id": self.agent_run_id,
            "tool_calls": self.tool_calls,
            "step_index": self.step_index,
        }
        data.update(updates)
        return ToolContext(**data)
