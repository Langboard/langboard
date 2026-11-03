"""Actual MCP attachment handler uses native duplicate decisions and typed results."""

import base64
from functools import wraps
from inspect import signature
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastmcp import Client, FastMCP
from langboard.mcp_integration.Providers import create_native_tool
from langboard.mcp_integration.ResourceOutputs import RESOURCE_OUTPUTS
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardMcp
from langboard_shared.core.db import DbSession
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage
from langboard_shared.domain.models import Card, CardAppOwnership, CardAttachment, ProjectActivity, ProjectRole, User
from langboard_shared.Env import Env
from sqlalchemy import update
from test_attachment_duplicate_upload import rows
from test_attachment_duplicate_upload import uploads as uploads
from test_card_mirrors import mirror_board as mirror_board


def call(b, content=b"first", name="report.pdf", *, request_uid=None, **decision):
    handler = McpTool.get_tool("upload_card_attachment")["handler"]
    return handler(
        b.source_board.get_uid(),
        b.source.get_uid(),
        name,
        base64.b64encode(content).decode("ascii"),
        b.actor,
        b.http_service,
        request_uid=request_uid or str(uuid4()),
        **decision,
    )


def choose(b, preview, *, action="replace", content=b"second", name="report.pdf", **changes):
    decision = dict(
        request_uid=preview["request_uid"],
        duplicate_action=action,
        confirmation_token=preview["confirmation_token"],
        replacement_uid=preview["conflicts"][0]["uid"] if action == "replace" else None,
    )
    decision.update(changes)
    return call(b, content, name, **decision)


def test_native_mcp_conflicts_cancel_and_same_bytes_replace_have_no_duplicate_processing(uploads, monkeypatch):  # noqa: F811
    b = uploads
    storage = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", storage)
    native = Mock(wraps=b.uploads.upload)
    monkeypatch.setattr(b.uploads, "upload", native)
    original = call(b)
    assert native.call_args.kwargs["channel"] is CollaborationChannel.Mcp
    assert original["request_uid"] and "user" not in original
    storage.reset_mock()
    b.uploads.dispatch_created.reset_mock()
    preview = call(b, name="other.pdf")
    assert preview["confirmation_required"]
    assert preview["conflicts"] == [
        dict(uid=original["uid"], name="report.pdf", same_name=False, same_content=True, can_replace=True)
    ]
    storage.assert_not_called()
    b.uploads.dispatch_created.assert_not_called()
    assert len(rows(CardAttachment)) == len(rows(ProjectActivity)) == 1
    cancelled = call(b, name="other.pdf", request_uid=preview["request_uid"], duplicate_action="cancel")
    assert cancelled == {"cancelled": True, "request_uid": preview["request_uid"]}
    storage.assert_not_called()
    b.uploads.dispatch_created.assert_not_called()
    replaced = choose(b, preview, content=b"first", name="other.pdf")
    assert replaced["uid"] == original["uid"]
    storage.assert_not_called()
    assert b.uploads.dispatch_created.call_args.kwargs["include_document_processing"] is False


def test_native_mcp_explicit_replace_add_stale_token_and_retry(uploads, monkeypatch):  # noqa: F811
    b = uploads
    original = call(b)
    preview = call(b, b"second")
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).values(filename="renamed.pdf"))
    refreshed = choose(b, preview)
    assert refreshed["confirmation_required"] and refreshed["conflicts"] == []
    assert Storage.get_file(rows(CardAttachment)[0].file) == b"first"
    assert len(rows(CardAttachment)) == 1
    # A stale replace never becomes an implicit Add when its old target disappears.
    added = choose(b, refreshed, action="add")
    assert added["uid"] != original["uid"]
    assert choose(b, refreshed, action="add")["replayed"]
    preview = call(b, b"third")
    replaced = choose(b, preview, content=b"third")
    assert replaced["uid"] == added["uid"]
    assert choose(b, preview, content=b"third")["replayed"]
    assert len(rows(CardAttachment)) == 2
    assert len(rows(ProjectActivity)) == 3
    preview = call(b, b"third", name="independent.pdf")
    added = choose(b, preview, action="add", content=b"third", name="independent.pdf")
    assert added["uid"] != replaced["uid"]
    assert len(rows(CardAttachment)) == 3


def test_native_mcp_lost_success_retries_exact_request_without_new_upload(uploads, monkeypatch):  # noqa: F811
    b = uploads
    native = b.uploads.upload
    outcomes = []

    def lose_result(*args, **kwargs):
        result = native(*args, **kwargs)
        outcomes.append(result)
        if len(outcomes) == 1:
            raise RuntimeError("private storage receipt and token")
        return result

    monkeypatch.setattr(b.uploads, "upload", lose_result)
    request_uid = str(uuid4())
    with pytest.raises(ValueError, match="same file, request UUID and decision") as failure:
        call(b, request_uid=request_uid)
    assert "private" not in str(failure.value)
    storage = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", storage)
    replay = call(b, request_uid=request_uid)
    assert replay["replayed"] and replay["uid"] == outcomes[0]["uid"]
    assert replay["request_uid"] == request_uid
    storage.assert_not_called()
    assert len(rows(CardAttachment)) == len(rows(ProjectActivity)) == 1
    with pytest.raises(ValueError):
        call(b, b"different", request_uid=request_uid)
    storage.assert_not_called()


@pytest.mark.parametrize("change", ["rights", "account", "private", "moved", "mirror", "app"])
def test_native_mcp_current_authority_rechecked_before_decision_or_replay(uploads, monkeypatch, change):  # noqa: F811
    b = uploads
    initial_uid = str(uuid4())
    call(b, request_uid=initial_uid)
    preview = call(b, b"second")
    with b.engine.begin() as db:
        if change == "rights":
            db.execute(update(ProjectRole).values(actions=[]))
        elif change == "account":
            db.execute(update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif change == "private":
            db.execute(
                update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id)
            )
        elif change == "moved":
            db.execute(
                update(Card)
                .where(Card.id == b.source.id)
                .values(project_id=b.destination.id, project_column_id=b.column.id)
            )
        elif change == "mirror":
            db.execute(
                update(Card).where(Card.id == b.source.id).values(source_type="project_card", source_uid="foreign")
            )
        else:
            with DbSession.use(readonly=False) as session:
                session.insert(CardAppOwnership(card_id=b.source.id, app_key="owned-app"))
    storage = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", storage)
    if change == "app":
        # Human user uploads retain the native app ownership policy.
        result = choose(b, preview)
        assert result.get("confirmation_required") or result.get("uid")
    else:
        with pytest.raises(ValueError, match="unavailable"):
            choose(b, preview)
        with pytest.raises(ValueError, match="unavailable"):
            call(b, request_uid=initial_uid)
        storage.assert_not_called()
        assert len(rows(CardAttachment)) == 1


@pytest.mark.parametrize("invalid", ["base64", "encoded_limit", "decoded_limit", "uuid", "filename"])
def test_native_mcp_input_bounds_fail_before_storage(uploads, monkeypatch, invalid):  # noqa: F811
    b = uploads
    data = base64.b64encode(b"ab").decode("ascii")
    request_uid = str(uuid4())
    filename = "report.pdf"
    if invalid == "base64":
        data = "not-base64!"
    if invalid == "encoded_limit":
        monkeypatch.setattr(type(Env), "MAX_FILE_SIZE_MB", property(lambda _: 1 / (1024 * 1024)))
        data = "A" * 8
    if invalid == "decoded_limit":
        monkeypatch.setattr(type(Env), "MAX_FILE_SIZE_MB", property(lambda _: 1 / (1024 * 1024)))
    if invalid == "uuid":
        request_uid = "not-a-uuid"
    if invalid == "filename":
        filename = "../private.pdf"
    storage = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", storage)
    with pytest.raises(ValueError):
        CardMcp.upload_card_attachment(
            b.source_board.get_uid(),
            b.source.get_uid(),
            filename,
            data,
            b.actor,
            b.http_service,
            request_uid=request_uid,
        )
    storage.assert_not_called()
    assert rows(CardAttachment) == []


async def test_actual_registered_mcp_schema_and_typed_union_roundtrip(uploads):  # noqa: F811
    b = uploads
    metadata = McpTool.get_tool("upload_card_attachment")
    assert metadata["accessible_type"] == "user"
    schema = metadata["input_schema"]
    assert set(schema["properties"]) == {"project_uid", "card_uid", "filename", "file_data_base64"}
    assert metadata["modern_only"] == ("request_uid", "duplicate_action", "confirmation_token", "replacement_uid")

    def wrap(_name, handler):
        @wraps(handler)
        async def invoke(**kwargs):
            return handler(user=b.actor, service=b.http_service, **kwargs)

        invoke.__signature__ = signature(handler).replace(
            parameters=[p for name, p in signature(handler).parameters.items() if name not in metadata["exclude"]]
        )
        return invoke

    server = FastMCP("Native duplicate fixture", strict_input_validation=True)
    server.add_tool(create_native_tool("upload_card_attachment", metadata, wrap, modern_annotations=True))
    async with Client(server) as client:
        tools = await client.list_tools()
        assert tools[0].inputSchema["properties"]["request_uid"]["anyOf"][0]["format"] == "uuid"
        assert "request_uid" not in tools[0].inputSchema["required"]
        assert tools[0].outputSchema
        payload = dict(
            project_uid=b.source_board.get_uid(),
            card_uid=b.source.get_uid(),
            filename="report.pdf",
            file_data_base64="Zmlyc3Q=",
            request_uid=str(uuid4()),
        )
        missing = await client.call_tool(
            "upload_card_attachment",
            {
                **{key: value for key, value in payload.items() if key != "request_uid"},
                "filename": "legacy.txt",
                "file_data_base64": "bGVnYWN5",
            },
            raise_on_error=False,
        )
        assert not missing.is_error and len(rows(CardAttachment)) == 1
        assert missing.structured_content["request_uid"]
        assert Storage.get_file(rows(CardAttachment)[0].file) == b"legacy"
        created = await client.call_tool("upload_card_attachment", payload)
        assert not created.is_error
        payload["request_uid"] = str(uuid4())
        preview = await client.call_tool("upload_card_attachment", payload)
        data = preview.structured_content
        assert "confirmation_required" in data, data
        assert data["confirmation_required"] and data["conflicts"][0]["uid"] == created.structured_content["uid"]
        assert "user" not in data and "file" not in data
        cancelled = await client.call_tool("upload_card_attachment", {**payload, "duplicate_action": "cancel"})
        assert cancelled.structured_content == {"cancelled": True, "request_uid": payload["request_uid"]}
        confirmed = await client.call_tool(
            "upload_card_attachment",
            {
                **payload,
                "duplicate_action": "replace",
                "replacement_uid": data["conflicts"][0]["uid"],
                "confirmation_token": data["confirmation_token"],
            },
        )
        assert confirmed.structured_content["uid"] == created.structured_content["uid"]
        for result in [created, preview, cancelled, confirmed]:
            RESOURCE_OUTPUTS["upload_card_attachment"].model_validate(result.structured_content)


def test_selected_replace_lost_result_retries_exact_decision_and_never_falls_back_to_add(uploads, monkeypatch):  # noqa: F811
    b = uploads
    original = call(b)
    preview = call(b, b"second")
    native = b.uploads.upload
    results = []

    def lose_once(*args, **kwargs):
        result = native(*args, **kwargs)
        results.append(result)
        if len(results) == 1:
            raise RuntimeError("private receipt")
        return result

    monkeypatch.setattr(b.uploads, "upload", lose_once)
    with pytest.raises(ValueError, match="same file, request UUID and decision"):
        choose(b, preview)
    storage = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", storage)
    replay = choose(b, preview)
    assert replay["replayed"] and replay["uid"] == original["uid"]
    assert len(rows(CardAttachment)) == 1 and len(rows(ProjectActivity)) == 2
    with pytest.raises(ValueError):
        choose(b, preview, action="add")
    storage.assert_not_called()
    assert len(rows(CardAttachment)) == 1


def test_native_mcp_forbidden_target_and_other_account_token_do_not_replace(uploads, monkeypatch):  # noqa: F811
    b = uploads
    original = call(b)
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).values(user_id=b.outsider.id))
    preview = call(b, b"second")
    assert preview["conflicts"][0]["can_replace"] is False
    storage = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", storage)
    with pytest.raises(ValueError, match="unavailable"):
        choose(b, preview)
    # An opaque token is bound to the authenticated account, not just these bytes.
    other = CardMcp.upload_card_attachment(
        b.source_board.get_uid(),
        b.source.get_uid(),
        "report.pdf",
        "c2Vjb25k",
        b.outsider,
        b.http_service,
        request_uid=preview["request_uid"],
        duplicate_action="replace",
        replacement_uid=original["uid"],
        confirmation_token=preview["confirmation_token"],
    )
    assert other["confirmation_required"] and other["confirmation_token"] != preview["confirmation_token"]
    storage.assert_not_called()
    assert Storage.get_file(rows(CardAttachment)[0].file) == b"first"


async def test_registered_legacy_upload_uses_native_uuid_fallback_and_duplicate_preview(uploads):  # noqa: F811
    b = uploads
    metadata = McpTool.get_tool("upload_card_attachment")

    def wrap(_name, handler):
        @wraps(handler)
        async def invoke(**kwargs):
            return handler(user=b.actor, service=b.http_service, **kwargs)

        invoke.__signature__ = signature(handler).replace(
            parameters=[p for name, p in signature(handler).parameters.items() if name not in metadata["exclude"]]
        )
        return invoke

    server = FastMCP("Frozen upload native fallback", strict_input_validation=True)
    server.add_tool(create_native_tool("upload_card_attachment", metadata, wrap, modern_annotations=False))
    payload = dict(
        project_uid=b.source_board.get_uid(),
        card_uid=b.source.get_uid(),
        filename="legacy.pdf",
        file_data_base64="Zmlyc3Q=",
    )
    async with Client(server) as client:
        schema = (await client.list_tools())[0].input_schema
        assert set(schema["properties"]) == set(payload)
        first = (await client.call_tool("upload_card_attachment", payload)).structured_content
        assert first["request_uid"] and len(rows(CardAttachment)) == 1
        assert Storage.get_file(rows(CardAttachment)[0].file) == b"first"
        preview = (await client.call_tool("upload_card_attachment", payload)).structured_content
        assert preview["confirmation_required"] is True
        assert preview["request_uid"] != first["request_uid"]
        assert preview["conflicts"][0]["uid"] == first["uid"]
        assert len(rows(CardAttachment)) == len(rows(ProjectActivity)) == 1
