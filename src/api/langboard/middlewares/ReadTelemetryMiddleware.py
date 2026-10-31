"""Bounded, payload-free measurements for selected workspace reads."""

import json
import logging
from time import perf_counter
from uuid import uuid4
from langboard_shared.core.db.ReadQueryTiming import ReadQueryTiming, read_query_timing
from langboard_shared.core.routing import BaseMiddleware
from langboard_shared.Env import Env
from sentry_sdk import get_current_span
from starlette.types import Message, Receive, Scope, Send


logger = logging.getLogger(f"{Env.PROJECT_NAME}.http.telemetry")


class ReadTelemetryMiddleware(BaseMiddleware):
    __auto_load__ = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") != "GET"
            or not (
                path
                in {
                    "/dashboard/projects",
                    "/dashboard/work/my",
                    "/dashboard/work/assigned",
                    "/dashboard/user/projects/starred",
                    "/notifications",
                    "/settings/notification-schedule",
                }
                or path.startswith("/board/")
                or path.startswith("/metadata/project/")
            )
        ):
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        started = perf_counter()
        status = 500
        response_bytes = 0
        headers_ms = None
        completed = False
        trace_id = None
        sql_timing = ReadQueryTiming()
        sql_token = read_query_timing.set(sql_timing)

        async def measured_send(message: Message) -> None:
            nonlocal status, response_bytes, headers_ms, completed, trace_id
            if message["type"] == "http.response.start":
                span = get_current_span()
                trace_id = span.trace_id if span else None
                status = message["status"]
                headers_ms = round((perf_counter() - started) * 1000, 3)
                sql = sql_timing.snapshot()
                # Copy the envelope; preserve body chunks and existing timing metrics.
                message = {**message, "headers": [*message.get("headers", [])]}
                message["headers"].extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"server-timing", f"app;dur={headers_ms}".encode("ascii")),
                        (b"server-timing", f"sql_execute;dur={sql['sql_execute_ms']}".encode("ascii")),
                    ]
                )
            await send(message)
            if message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    completed = True

        try:
            await self.app(scope, receive, measured_send)
        finally:
            sql = sql_timing.snapshot(close=True)
            read_query_timing.reset(sql_token)
            # Route templates, never user-controlled URLs, queries, headers or payloads.
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(
                json.dumps(
                    {
                        "event": "http_read",
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "route": route,
                        "status": status,
                        "headers_ms": headers_ms,
                        "duration_ms": round((perf_counter() - started) * 1000, 3),
                        "response_bytes": response_bytes,
                        "completed": completed,
                        **sql,
                    },
                    separators=(",", ":"),
                )
            )
