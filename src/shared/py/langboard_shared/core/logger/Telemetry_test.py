"""Exercise the official SDK envelope boundary with no network transport."""

import json
from sentry_sdk import Client
from sentry_sdk.transport import Transport
from langboard_shared.core.logger.Logger import diagnostic_event


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
