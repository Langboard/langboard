"""Telemetry reports execution evidence without copying any caller payload."""

import json
import logging
from types import SimpleNamespace
import pytest
from fastmcp.exceptions import AuthorizationError
from fastmcp.tools import ToolResult
from langboard.mcp_integration.Telemetry import ToolTelemetryMiddleware
from langboard.mcp_integration.Tool import McpTool
from opentelemetry import trace


@pytest.fixture(autouse=True)
def capture_native_telemetry(caplog, monkeypatch):
    # Production telemetry owns its handler and deliberately does not propagate
    # to pytest's root capture handler. Observe that logger directly in all orders.
    logger = logging.getLogger("langboard.mcp.telemetry")
    monkeypatch.setattr(logger, "propagate", False)
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("raw", [True, False])
@pytest.mark.parametrize("outcome", ["applied", "not_applied", "unknown", "success", "error", "denied"])
async def test_safe_telemetry_keeps_receipt_identity_and_does_not_modify_results(monkeypatch, caplog, raw, outcome):
    monkeypatch.setattr(
        McpTool, "get_tool", lambda name: {"handler": object()} if name == "patch_card_description" else None
    )
    caplog.set_level(logging.INFO, logger="langboard.mcp.telemetry")
    receipt = {"request_id": "a" * 32, "outcome": outcome} if outcome in {"applied", "not_applied", "unknown"} else None
    result = ToolResult(
        content="PRIVATE_RESPONSE", is_error=outcome == "error", meta={"mutation_receipt": receipt} if receipt else {}
    )
    context = SimpleNamespace(
        message=SimpleNamespace(
            name="call_raw_tool" if raw else "patch_card_description",
            arguments={
                "name": "patch_card_description",
                "arguments": {"content": "PRIVATE_BODY", "token": "PRIVATE_TOKEN"},
            },
        )
    )

    async def next_call(value):
        assert value is context
        if outcome == "denied":
            raise AuthorizationError("PRIVATE_ERROR")
        return result

    if outcome == "denied":
        with pytest.raises(AuthorizationError):
            await ToolTelemetryMiddleware().on_call_tool(context, next_call)
    else:
        assert await ToolTelemetryMiddleware().on_call_tool(context, next_call) is result
    records = [r for r in caplog.records if r.name == "langboard.mcp.telemetry"]
    assert len(records) == 1
    payload = json.loads(records[0].message)
    assert set(payload) == {"event", "request_id", "tool", "duration_ms", "outcome"}
    assert payload["tool"] == "patch_card_description" and payload["outcome"] == outcome
    assert payload["duration_ms"] >= 0
    if receipt:
        assert payload["request_id"] == receipt["request_id"]
    assert "PRIVATE_" not in records[0].message


async def test_unrecognized_name_and_exception_details_are_not_logged(monkeypatch, caplog):
    monkeypatch.setattr(McpTool, "get_tool", lambda _: None)
    caplog.set_level(logging.INFO, logger="langboard.mcp.telemetry")
    context = SimpleNamespace(message=SimpleNamespace(name="call_raw_tool", arguments={"name": "PRIVATE_TOKEN"}))

    async def fail(_):
        raise RuntimeError("PRIVATE_ERROR")

    with pytest.raises(RuntimeError):
        await ToolTelemetryMiddleware().on_call_tool(context, fail)
    record = json.loads(caplog.records[-1].message)
    assert record["tool"] == "unknown" and record["outcome"] == "error"
    assert "PRIVATE_" not in caplog.records[-1].message


@pytest.mark.parametrize("profile", ["agent", "raw", "compatibility"])
async def test_native_transport_record_matches_mutation_receipt(monkeypatch, caplog, profile):
    from fastmcp import Client
    from langboard.mcp_integration.Server import McpServer
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context

    name = "mark_all_notifications_read" if profile == "raw" else "mark_notification_read"

    def handler(notification_uid: str) -> dict:
        return {"read": True}

    metadata = {"handler": handler, "description": "Read", "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {name: metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda key: metadata if key == name else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    caplog.set_level(logging.INFO, logger="langboard.mcp.telemetry")
    _, server = McpServer.get_http_app(profile)
    token = mcp_auth_context.set(
        {"user_or_bot": object(), "tool_group": SimpleNamespace(activated_at=object(), tools=[name])}
    )
    try:
        async with Client(server) as client:
            arguments = {"notification_uid": "PRIVATE_UID"}
            result = await client.call_tool(
                "call_raw_tool" if profile == "raw" else name,
                {"name": name, "arguments": arguments} if profile == "raw" else arguments,
            )
            assert result.structured_content == {"read": True}
            records = [json.loads(r.message) for r in caplog.records if r.name == "langboard.mcp.telemetry"]
            assert len(records) == 1
            record = records[0]
            assert record["tool"] == name
            assert "PRIVATE_" not in json.dumps(record)
            if profile == "compatibility":
                assert record["outcome"] == "success"
                assert "mutation_receipt" not in (result.meta or {})
            else:
                receipt = result.meta["mutation_receipt"]
                assert record["request_id"] == receipt["request_id"]
                assert record["outcome"] == receipt["outcome"] == "applied"
    finally:
        mcp_auth_context.reset(token)


def test_production_telemetry_formatter_keeps_complete_record_on_one_line():
    import io
    from langboard_shared.core.logger import Logger
    from langboard_shared.Env import Env

    config = Logger.get_config()
    policy = config["loggers"][f"{Env.PROJECT_NAME}.mcp.telemetry"]
    assert policy == {"handlers": ["mcp_telemetry"], "level": "INFO", "propagate": False}
    handler_config = config["handlers"]["mcp_telemetry"]
    assert handler_config["class"] == "logging.StreamHandler"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(config["formatters"]["mcp_telemetry"]["format"]))
    payload = {
        "event": "mcp_tool_execution",
        "request_id": "a" * 32,
        "tool": "get_card_bundle",
        "duration_ms": 1.25,
        "outcome": "success",
    }
    handler.emit(logging.LogRecord("langboard.mcp.telemetry", logging.INFO, "", 0, json.dumps(payload), (), None))
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == payload


@pytest.mark.parametrize("failure", [None, "denied", "exception", "cancellation"])
async def test_existing_span_correlates_receipt_and_worker_without_exporting_private_context(
    monkeypatch, caplog, failure
):
    from asyncio import CancelledError
    from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
    from langboard.mcp_integration import Telemetry

    monkeypatch.setattr(McpTool, "get_tool", lambda _: {"handler": object()})
    monkeypatch.setattr(Telemetry, "persist_execution", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.INFO, logger="langboard.mcp.telemetry")
    parent = trace.SpanContext(
        trace_id=0x123,
        span_id=0x456,
        is_remote=True,
        trace_state=trace.TraceState([("vendor", "PRIVATE_STATE")]),
    )
    receipt = {"request_id": "a" * 32, "outcome": "applied"}
    result = ToolResult(content="PRIVATE_RESULT", meta={"mutation_receipt": receipt})
    context = SimpleNamespace(message=SimpleNamespace(name="get_card_bundle", arguments={"token": "PRIVATE_TOKEN"}))
    original = {"denied": AuthorizationError, "exception": RuntimeError, "cancellation": CancelledError}

    def worker():
        assert trace.get_current_span().get_span_context() == parent
        if failure:
            raise original[failure]("PRIVATE_ERROR")
        return result

    async def next_call(_):
        return await call_sync_fn_in_threadpool(worker)

    with trace.use_span(trace.NonRecordingSpan(parent), record_exception=False, set_status_on_exception=False):
        if failure:
            with pytest.raises(original[failure]):
                await ToolTelemetryMiddleware().on_call_tool(context, next_call)
        else:
            assert await ToolTelemetryMiddleware().on_call_tool(context, next_call) is result
    records = [json.loads(r.message) for r in caplog.records if r.name == "langboard.mcp.telemetry"]
    assert len(records) == 1
    record = records[0]
    assert set(record) == {"event", "request_id", "tool", "duration_ms", "outcome", "trace_id", "span_id"}
    assert record["trace_id"] == "00000000000000000000000000000123"
    assert record["span_id"] == "0000000000000456"
    expected_outcome = {None: "applied", "denied": "denied", "exception": "error", "cancellation": "unknown"}[failure]
    assert record["outcome"] == expected_outcome
    if not failure:
        assert record["request_id"] == receipt["request_id"]
    assert "PRIVATE_" not in json.dumps(record)


@pytest.mark.parametrize("failure", ["storage", "cancellation", "exception", "nested"])
async def test_history_observability_keeps_original_result_and_exception(monkeypatch, caplog, failure):
    from asyncio import CancelledError
    from langboard.mcp_integration import Telemetry
    from langboard.mcp_integration.ExecutionHistory import execution_scope

    monkeypatch.setattr(McpTool, "get_tool", lambda _: {"handler": object()})
    caplog.set_level(logging.INFO, logger="langboard.mcp.telemetry")
    calls = []

    def persist(scope, **metadata):
        calls.append((scope, metadata))
        if failure == "storage":
            raise RuntimeError("PRIVATE_STORAGE_ERROR")

    monkeypatch.setattr(Telemetry, "persist_execution", persist)
    context = SimpleNamespace(message=SimpleNamespace(name="get_card_bundle", arguments={}))
    result = ToolResult(content="PRIVATE_RESULT")
    original = CancelledError("PRIVATE_CANCEL") if failure == "cancellation" else RuntimeError("PRIVATE_ERROR")

    async def final_call(_):
        execution_scope.get().update(project_id=10, user_id=1)
        if failure in {"cancellation", "exception"}:
            raise original
        return result

    async def next_call(ctx):
        return (
            await ToolTelemetryMiddleware().on_call_tool(ctx, final_call)
            if failure == "nested"
            else await final_call(ctx)
        )

    if failure in {"cancellation", "exception"}:
        with pytest.raises(type(original)) as raised:
            await ToolTelemetryMiddleware().on_call_tool(context, next_call)
        assert raised.value is original
    else:
        assert await ToolTelemetryMiddleware().on_call_tool(context, next_call) is result
    assert len(calls) == 1 and calls[0][0] == {"project_id": 10, "user_id": 1}
    assert calls[0][1]["outcome"] == (
        "unknown" if failure == "cancellation" else "error" if failure == "exception" else "success"
    )
    assert "PRIVATE" not in " ".join(record.message for record in caplog.records)


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_preview_sql_timing_follows_worker_and_excludes_history(monkeypatch, caplog, raw, failed):
    from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
    from langboard.mcp_integration import Telemetry
    from langboard_shared.core.db.ReadQueryTiming import (
        ReadTimingQueuePool,
        instrument_read_queries,
        read_query_timing,
    )
    from sqlalchemy import create_engine, text

    name = "preview_card_work_plan"
    monkeypatch.setattr(McpTool, "get_tool", lambda value: {"handler": object()} if value == name else None)
    caplog.set_level(logging.INFO, logger="langboard.mcp.telemetry")
    engine = instrument_read_queries(
        create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=ReadTimingQueuePool)
    )
    result = ToolResult(content="PRIVATE_RESULT")
    error = RuntimeError("PRIVATE_FAILURE")
    collectors = []

    def query():
        collectors.append(read_query_timing.get())
        with engine.connect() as db:
            assert db.execute(text("SELECT :value"), {"value": "PRIVATE_PARAMETER"}).scalar_one() == "PRIVATE_PARAMETER"
        if failed:
            raise error
        return result

    def persist(*args, **kwargs):
        assert read_query_timing.get() is None
        with engine.connect() as db:
            db.execute(text("SELECT 2"))

    monkeypatch.setattr(Telemetry, "persist_execution", persist)
    context = SimpleNamespace(
        message=SimpleNamespace(
            name="call_raw_tool" if raw else name,
            arguments={"name": name, "arguments": {"title": "PRIVATE_TITLE"}},
        )
    )

    async def next_call(_):
        return await call_sync_fn_in_threadpool(query)

    try:
        if failed:
            with pytest.raises(RuntimeError) as raised:
                await ToolTelemetryMiddleware().on_call_tool(context, next_call)
            assert raised.value is error
        else:
            assert await ToolTelemetryMiddleware().on_call_tool(context, next_call) is result
        records = [json.loads(r.message) for r in caplog.records if r.name == "langboard.mcp.telemetry"]
        assert len(records) == 1
        record = records[0]
        assert record["sql_count"] == 1  # History's own query is outside the measurement.
        assert record["sql_errors"] == 0
        assert record["sql_execute_ms"] >= 0
        assert record["sql_acquire_ms"] > 0
        assert record["outcome"] == ("error" if failed else "success")
        assert collectors[0] is not None and collectors[0].active is False
        assert read_query_timing.get() is None
        assert "PRIVATE" not in json.dumps(records)
    finally:
        engine.dispose()
