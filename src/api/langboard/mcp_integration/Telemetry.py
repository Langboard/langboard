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
from langboard_shared.core.types import SafeDateTime
from langboard_shared.Env import Env
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
        outcome = "error"
        started = perf_counter()
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
                    },
                    separators=(",", ":"),
                )
            )
