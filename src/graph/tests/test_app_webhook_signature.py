"""Exact app-event bytes reuse the native signer and independent SDK verifier."""

import json
import pytest
from langboard_sdk.webhooks import InvalidWebhook, verify_webhook
from langboard_shared.tasks.webhooks.WebhookTask import sign_webhook_bytes, signed_request
from langboard_shared.tasks.webhooks.utils import WebhookModel


def test_native_app_answer_bytes_verified_by_standalone_sdk():
    payload = {
        "schema_version": 1,
        "event_uid": "ABCDEFGHIJK",
        "event_type": "io.langboard.app.interaction.answered.v1",
        "values": {"decision": {"choice_id": "approve"}},
        "prompt": "한글·日本語·中文",
        "execution_resumed": False,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signed_body, headers = sign_webhook_bytes(body, payload["event_uid"], "test-secret", timestamp=1700000000)
    assert signed_body is body
    verify_webhook(signed_body, headers, "test-secret", now=1700000000)
    assert json.loads(signed_body) == payload
    for changed in (body.replace(b"approve", b"reject"), body + b" "):
        with pytest.raises(InvalidWebhook):
            verify_webhook(changed, headers, "test-secret", now=1700000000)
    with pytest.raises(InvalidWebhook):
        verify_webhook(body, headers, "test-secret", now=1700000301)
    with pytest.raises(InvalidWebhook):
        verify_webhook(body, headers, "wrong-secret", now=1700000000)


def test_existing_webhook_projection_and_unsigned_mode_preserved():
    model = WebhookModel(event="card_created", data={"card_uid": "ABCDEFGHIJK", "secret": "never project"})
    body, headers = signed_request(model, "test-secret", timestamp=1700000000)
    verify_webhook(body, headers, "test-secret", now=1700000000)
    assert json.loads(body)["data"] == {"card_uid": "ABCDEFGHIJK"}
    unsigned_body, unsigned_headers = signed_request(model, None, timestamp=1700000000)
    assert unsigned_body == body
    assert "X-Langboard-Webhook-Signature" not in unsigned_headers


@pytest.mark.parametrize(
    "kwargs",
    [
        {"body": "text"},
        {"body": b"x" * (1024 * 1024 + 1)},
        {"event_id": "bad\r\nheader"},
        {"timestamp": True},
        {"timestamp": -1},
        {"version": "2"},
        {"secret": ""},
    ],
)
def test_signer_rejects_invalid_wire_fields(kwargs):
    fields = dict(body=b"{}", event_id="ABCDEFGHIJK", secret="test-secret", timestamp=1700000000)
    fields.update(kwargs)
    with pytest.raises(ValueError):
        sign_webhook_bytes(**fields)
