"""Exercise the official SDK envelope boundary with no network transport."""

import ast
import importlib
import json
from pathlib import Path
import pytest
import sentry_sdk
from sentry_sdk import Client
from sentry_sdk.transport import Transport
from langboard_shared.core.logger.Logger import diagnostic_event
from langboard_shared.Env import Env


def test_official_sdk_envelope_excludes_application_content_and_retains_diagnostics():
    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    client = Client(
        dsn="https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@errors.example.test/3",
        transport=MemoryTransport(),
        default_integrations=False,
        before_send=diagnostic_event,
        release="test-revision",
        environment="canary",
    )
    event = {
        "level": "error",
        "message": "PRIVATE_LOG",
        "logentry": {"message": "%s", "formatted": "PRIVATE_FORMATTED", "params": ["PRIVATE_PARAM"]},
        "request": {"data": {"title": "PRIVATE_TITLE"}, "headers": {"authorization": "PRIVATE_TOKEN"}},
        "user": {"email": "PRIVATE_EMAIL"},
        "breadcrumbs": [{"category": "query", "message": "PRIVATE_SQL"}],
        "fingerprint": ["PRIVATE_FINGERPRINT"],
        "tags": {"custom": "PRIVATE_TAG"},
        "extra": {
            "request_id": "a" * 32,
            "tool": "create_card_checkitem",
            "cause_type": "ReadOnlySqlTransaction",
            "body": "PRIVATE_EXTRA",
        },
        "contexts": {
            "trace": {"trace_id": "b" * 32, "span_id": "c" * 16, "description": "PRIVATE_TRACE"},
            "custom": {"value": "PRIVATE_CONTEXT"},
        },
        "exception": {
            "values": [
                {
                    "type": "ReadOnlySqlTransaction",
                    "value": "PRIVATE_EXCEPTION",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "langboard.routes.mcp.McpApi",
                                "function": "call_tool",
                                "lineno": 136,
                                "vars": {"body": "PRIVATE_LOCAL"},
                                "filename": "PRIVATE_FILENAME",
                                "pre_context": ["PRIVATE_SOURCE"],
                            }
                        ]
                    },
                }
            ]
        },
    }
    assert client.capture_event(event) is not None
    payload = envelopes[0].items[0].payload.json
    serialized = json.dumps(payload)
    assert "PRIVATE_" not in serialized
    assert payload["exception"]["values"][0]["type"] == "ReadOnlySqlTransaction"
    assert payload["exception"]["values"][0]["stacktrace"]["frames"][0]["lineno"] == 136
    assert payload["tags"] == {
        "request_id": "a" * 32,
        "tool": "create_card_checkitem",
        "cause_type": "ReadOnlySqlTransaction",
    }
    assert payload["contexts"] == {"trace": {"trace_id": "b" * 32, "span_id": "c" * 16}}
    assert payload["release"] == "test-revision" and payload["environment"] == "canary"
    client.close()


def test_unrecognized_diagnostic_fields_and_exception_type_are_not_forwarded():
    payload = diagnostic_event(
        {
            "extra": {"request_id": "PRIVATE_ID", "tool": "PRIVATE_TOOL", "cause_type": "PRIVATE TYPE"},
            "exception": {"values": [{"type": "PRIVATE TYPE", "value": "PRIVATE_MESSAGE"}]},
        },
        {},
    )
    assert "PRIVATE" not in json.dumps(payload)
    assert "tags" not in payload
    assert payload["exception"]["values"][0]["type"] == "Error"


def test_log_only_tool_failures_keep_bounded_grouping_without_original_message():
    payload = diagnostic_event(
        {
            "logentry": {"message": "PRIVATE_LOG"},
            "extra": {"tool": "create_card_checkitem", "cause_type": "ReadOnlySqlTransaction"},
        },
        {},
    )
    assert payload["fingerprint"] == ["mcp-tool", "create_card_checkitem", "ReadOnlySqlTransaction"]
    assert "PRIVATE" not in json.dumps(payload)


def _logger_options(monkeypatch, rate=None):
    module = importlib.import_module("langboard_shared.core.logger.Logger")
    monkeypatch.setitem(Env._Env__envs, "SENTRY_DSN", "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@errors.example.test/3")
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.delitem(Env._Env__envs, "SENTRY_TRACES_SAMPLE_RATE", raising=False)
    if rate is not None:
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", rate)
    options = {}
    monkeypatch.setattr(module, "sentry_init", lambda **kwargs: options.update(kwargs))
    monkeypatch.setattr(module, "basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(module.Logger, "get_handlers", lambda: [])
    # The singleton decorator replaces __init__ after first import. Execute the
    # actual source initializer without reconstructing or mutating that singleton.
    tree = ast.parse(Path(module.__file__).read_text())
    logger_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Logger")
    initializer = next(
        node for node in logger_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[initializer], type_ignores=[]), module.__file__, "exec"), namespace)
    namespace["__init__"](module.Logger)
    return options


@pytest.mark.parametrize(
    "rate,expected",
    [
        (None, 0.0),
        ("0", 0.0),
        ("0.05", 0.05),
        ("1", 1.0),
        ("-1", 0.0),
        ("1.1", 0.0),
        ("nan", 0.0),
        ("inf", 0.0),
        ("bad", 0.0),
    ],
)
def test_native_sampler_uses_valid_local_rate_even_when_parent_is_sampled(monkeypatch, rate, expected):
    options = _logger_options(monkeypatch, rate)
    assert options["traces_sampler"]({"parent_sampled": True}) == expected


@pytest.mark.parametrize("setting,expected", [(None, True), ("true", True), ("false", False)])
def test_recovery_can_disable_optional_auto_integrations_without_disabling_default_capture(
    monkeypatch, setting, expected
):
    monkeypatch.delenv("SENTRY_AUTO_ENABLING_INTEGRATIONS", raising=False)
    monkeypatch.delitem(Env._Env__envs, "SENTRY_AUTO_ENABLING_INTEGRATIONS", raising=False)
    if setting is not None:
        monkeypatch.setenv("SENTRY_AUTO_ENABLING_INTEGRATIONS", setting)
    options = _logger_options(monkeypatch)
    assert options["auto_enabling_integrations"] is expected
    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    # Keep production default integrations; suppress only automatic optional
    # framework discovery. No network or provider credentials are involved.
    client = Client(**{**options, "auto_enabling_integrations": False}, transport=MemoryTransport())
    try:
        assert "excepthook" in client.integrations and "logging" in client.integrations
        client.capture_event({"level": "error", "message": "PRIVATE_RECOVERY_ERROR"})
        assert envelopes
        assert "PRIVATE_RECOVERY_ERROR" not in json.dumps(
            [item.payload.json for envelope in envelopes for item in envelope.items if item.type == "event"]
        )
    finally:
        client.close()


@pytest.mark.parametrize("rate,parent_sampled,expected_count", [(None, True, 0), ("0", True, 0), ("1", False, 1)])
def test_official_sdk_transaction_envelope_is_opt_in_and_excludes_content(
    monkeypatch, rate, parent_sampled, expected_count
):
    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    options = _logger_options(monkeypatch, rate)
    client = Client(**options, transport=MemoryTransport(), default_integrations=False)
    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        scope.set_user({"email": "PRIVATE_EMAIL"})
        scope.set_extra("body", "PRIVATE_BODY")
        with sentry_sdk.start_transaction(name="PRIVATE_PATH", op="http.server", parent_sampled=parent_sampled):
            with sentry_sdk.start_span(op="db.sql.query", description="SELECT 'PRIVATE_SQL'") as span:
                span.set_data("db.params", {"token": "PRIVATE_TOKEN"})
    transactions = [
        item.payload.json for envelope in envelopes for item in envelope.items if item.type == "transaction"
    ]
    assert len(transactions) == expected_count
    for envelope in envelopes:
        assert b"PRIVATE_" not in envelope.serialize()
    if transactions:
        payload = transactions[0]
        assert payload["transaction"] == "Application request"
        assert payload["start_timestamp"] <= payload["timestamp"]
        assert payload["spans"][0]["op"] == "db"
        assert payload["spans"][0]["start_timestamp"] <= payload["spans"][0]["timestamp"]
        assert len(payload["contexts"]["trace"]["trace_id"]) == 32
    client.close()
