# Source excerpt; private application dependencies are intentionally omitted.

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.agent.tool_context import ToolContext
from app.agent.tool_registry import registry, ensure_default_tools_registered
from app.agent.trace import AgentTraceRecorder
from app.db.models import AgentToolCall

logger = logging.getLogger(__name__)


class ToolExecutionError(RuntimeError):
    pass


_MAX_TEXT_LEN = 500
_MAX_LIST_ITEMS = 10


def _truncate_text(value: str, limit: int = _MAX_TEXT_LEN) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


def sanitize_for_audit(value: Any, *, redact: bool = False, depth: int = 0) -> Any:
    """Make tool input/output safe and bounded for DB audit storage."""
    if redact:
        return {"redacted": True}
    if depth > 5:
        return "..."
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key in {"content", "suggested_script", "script", "response_text"} and isinstance(v, str):
                out[key] = _truncate_text(v, 220)
            elif key in {"chunks", "retrieved_chunks"} and isinstance(v, list):
                out[key] = [sanitize_for_audit(x, depth=depth + 1) for x in v[:_MAX_LIST_ITEMS]]
                if len(v) > _MAX_LIST_ITEMS:
                    out[key].append({"truncated_items": len(v) - _MAX_LIST_ITEMS})
            else:
                out[key] = sanitize_for_audit(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        out = [sanitize_for_audit(x, depth=depth + 1) for x in value[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            out.append({"truncated_items": len(value) - _MAX_LIST_ITEMS})
        return out
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_text(str(value))


class ToolExecutor:
    def __init__(self, db: Session) -> None:
        self.db = db
        ensure_default_tools_registered()

    def run(self, ctx: ToolContext, tool_name: str, input_data: BaseModel | dict[str, Any], *, required: bool = True):
        spec = registry.get(tool_name)
        tracer = AgentTraceRecorder(self.db)
        missing = [p for p in spec.permissions if not ctx.has_permission(p)]
        if missing:
            tracer.record(
                ctx=ctx,
                step_type="guardrail",
                content={
                    "event": "tool_permission_denied",
                    "tool_name": tool_name,
                    "required_permissions": sorted(spec.permissions),
                    "missing_permissions": missing,
                    "read_only": spec.read_only,
                },
            )
            raise HTTPException(403, f"工具权限不足：{tool_name} requires {','.join(missing)}")

        if isinstance(input_data, spec.input_schema):
            validated_input = input_data
        else:
            validated_input = spec.input_schema.model_validate(input_data)

        status = "success"
        error_message = None
        output_obj = None
        input_audit = sanitize_for_audit(validated_input, redact=spec.redact_input)
        tracer.record(
            ctx=ctx,
            step_type="tool_call",
            content={
                "tool_name": spec.name,
                "tool_version": spec.version,
                "required": required,
                "read_only": spec.read_only,
                "timeout_ms": spec.timeout_ms,
                "retry": spec.retry,
                "input": input_audit,
            },
        )
        t0 = time.perf_counter()
        attempts = max(1, spec.retry + 1)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                output_obj = spec.handler(ctx, validated_input)
                output_obj = spec.output_schema.model_validate(output_obj)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception("tool %s failed attempt %s/%s", tool_name, attempt, attempts)
                if attempt >= attempts:
                    status = "error"
                    error_message = str(exc)[:1000]
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if status == "success" and latency_ms > spec.timeout_ms:
            status = "partial"
            error_message = f"tool exceeded soft timeout: {latency_ms}ms > {spec.timeout_ms}ms"

        output_audit = sanitize_for_audit(output_obj, redact=spec.redact_output) if output_obj is not None else None
        row = AgentToolCall(
            run_id=ctx.agent_run_id,
            tool_name=spec.name,
            tool_version=spec.version,
            input_json=input_audit,
            output_json=output_audit,
            status=status,
            error_message=error_message,
            latency_ms=latency_ms,
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            customer_id=ctx.customer_id,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        call_summary = {
            "id": row.id,
            "tool_name": spec.name,
            "tool_version": spec.version,
            "input": input_audit,
            "output": output_audit,
            "status": status,
            "error_message": error_message,
            "latency_ms": latency_ms,
        }
        ctx.tool_calls.append(call_summary)
        tracer.record(
            ctx=ctx,
            step_type="observation",
            content={
                "tool_call_id": row.id,
                "tool_name": spec.name,
                "tool_version": spec.version,
                "status": status,
                "latency_ms": latency_ms,
                "error_message": error_message,
                "output": output_audit,
            },
        )

        if status == "error":
            if required:
                raise ToolExecutionError(f"{tool_name} failed: {error_message}") from last_exc
            return None
        return output_obj
