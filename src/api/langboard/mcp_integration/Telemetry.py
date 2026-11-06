"""Payload-free tool execution records through FastMCP's native middleware."""

import json
import logging
from asyncio import CancelledError
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4
from anyio import CancelScope
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.middleware import Middleware
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
from langboard_shared.core.db.ReadQueryTiming import ReadQueryTiming, read_query_timing
from langboard_shared.core.types import SafeDateTime
from langboard_shared.Env import Env
from opentelemetry import trace
from .ExecutionHistory import execution_scope, persist_execution
from .Tool import McpTool


logger = logging.getLogger(f"{Env.PROJECT_NAME}.mcp.telemetry")
_recording = ContextVar("mcp_telemetry_recording", default=False)


class ToolTelemetryMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        if _recording.get():
            return await call_next(context)
        token = _recording.set(True)
        scope_token = execution_scope.set({})
        started_at = SafeDateTime.now()
        name = context.message.name
        if name == "call_raw_tool":
            name = (context.message.arguments or {}).get("name")
        # Never log a caller-controlled unknown tool name or arbitrary arguments.
        tool = name if isinstance(name, str) and (McpTool.get_tool(name) or name == "search_raw_tools") else "unknown"
        request_id = uuid4().hex
        # Correlate with the caller's existing span without creating/exporting
        # traces or copying attributes, baggage, tracestate, or request content.
        span_context = trace.get_current_span().get_span_context()
        trace_ids = (
            {
                "trace_id": trace.format_trace_id(span_context.trace_id),
                "span_id": trace.format_span_id(span_context.span_id),
            }
            if span_context.is_valid
            else {}
        )
        outcome = "error"
        started = perf_counter()
        # Reuse native DB instrumentation across the worker context. Acquisition
        # includes checkout/pre-ping; execution includes DB waits, not pure lock wait.
        sql_timing = ReadQueryTiming() if tool == "preview_card_work_plan" else None
        sql_token = read_query_timing.set(sql_timing) if sql_timing is not None else None
        try:
            result = await call_next(context)
            receipt = (result.meta or {}).get("mutation_receipt")
            if isinstance(receipt, dict):
                identity = receipt.get("request_id")
                if isinstance(identity, str) and len(identity) == 32 and all(c in "0123456789abcdef" for c in identity):
                    request_id = identity
                declared = receipt.get("outcome")
                if isinstance(declared, str) and declared in {"applied", "not_applied", "unknown"}:
                    outcome = declared
                else:
                    outcome = "error" if result.is_error else "success"
            else:
                outcome = "error" if result.is_error else "success"
            return result
        except CancelledError:
            outcome = "unknown"
            raise
        except AuthorizationError:
            outcome = "denied"
            raise
        finally:
            duration_ms = round((perf_counter() - started) * 1000, 3)
            sql = sql_timing.snapshot(close=True) if sql_timing is not None else {}
            if sql_token is not None:
                read_query_timing.reset(sql_token)
            scope = execution_scope.get()
            execution_scope.reset(scope_token)
            _recording.reset(token)
            try:
                with CancelScope(shield=True):
                    await call_sync_fn_in_threadpool(
                        persist_execution,
                        scope,
                        request_id=request_id,
                        tool=tool,
                        outcome=outcome,
                        started_at=started_at,
                        duration_ms=duration_ms,
                    )
            except Exception:
                logger.warning("Native MCP execution history storage unavailable")
            logger.info(
                json.dumps(
                    {
                        "event": "mcp_tool_execution",
                        "request_id": request_id,
                        "tool": tool,
                        "duration_ms": duration_ms,
                        "outcome": outcome,
                        **trace_ids,
                        **sql,
                    },
                    separators=(",", ":"),
                )
            )
