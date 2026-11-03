"""Strict registered MCP transport preserves native version-bound image evidence."""

from functools import wraps
from inspect import signature
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from langboard.card_workspace.application.dtos import CardBundleDto
from langboard.card_workspace.application.projections import public_comment
from langboard.mcp_integration.Providers import create_native_tool
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardImagesMcp, CardMcp
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.domain.models import Card, CardAttachment, CardComment
from langboard_shared.domain.models.CardComment import CardCommentImageAnchorModel
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardCommentRepository import CardCommentRepository
from PIL import Image
from src.api.tests.card_cross_board.test_card_mirrors import mirror_board as mirror_board


@pytest.fixture
def image_board(mirror_board, monkeypatch):  # noqa: F811 - imported native fixture
    b = mirror_board
    output = BytesIO()
    Image.new("RGB", (1920, 1080)).save(output, format="PNG")
    b.image_bytes = output.getvalue()

    def download(storage, name, filename, destination):
        destination.write(b.image_bytes)
        return True

    monkeypatch.setattr(Storage, "_download", download)
    for model in (CardComment, CardAttachment):
        model.__table__.create(b.engine)
    b.service.mark_card_changed = Mock()
    comments = CardCommentService(
        lambda cls: b.service if cls is CardService else None,
        lambda _: None,
        SimpleNamespace(card_comment=CardCommentRepository(None, None)),
    )
    comments.dispatch_created = Mock()
    attachments = CardAttachmentService(lambda _: None, lambda _: None, None)
    with DbSession.use(readonly=False) as db:
        attachment = CardAttachment(
            card_id=b.source.id,
            user_id=b.actor.id,
            filename="image.png",
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="immutable.png",
                original_filename="image.png",
                path="/file/local/card_attachment/immutable.png",
            ),
        )
        db.insert(attachment)
    b.attachment = attachment
    b.comments = comments
    b.native = SimpleNamespace(
        card=b.service,
        card_comment=comments,
        card_attachment=attachments,
        card_content_block=SimpleNamespace(get_blocks_by_card=lambda _: []),
    )
    return b


def selector(b):
    return dict(
        type="ImageRegionSelector",
        version=1,
        source=dict(
            type="card_attachment", attachment_uid=b.attachment.get_uid(), revision=b.attachment.image_revision
        ),
        shape="point",
        image_width=1920,
        image_height=1080,
        x=12.5,
        y=20,
        width=0,
        height=0,
    )


def registered(b, name):
    metadata = McpTool.get_tool(name)

    def wrap(_name, handler):
        @wraps(handler)
        async def invoke(**kwargs):
            return handler(user_or_bot=b.actor, service=b.native, **kwargs)

        invoke.__signature__ = signature(handler).replace(
            parameters=[p for key, p in signature(handler).parameters.items() if key not in metadata["exclude"]]
        )
        return invoke

    server = FastMCP("Native image annotation", strict_input_validation=True)
    server.add_tool(create_native_tool(name, metadata, wrap, modern_annotations=True))
    return server


async def test_strict_native_create_retry_and_safe_projection(image_board, monkeypatch):
    b = image_board

    def replica_forbidden():
        raise AssertionError("Immediate image comment readback must use the primary database")

    monkeypatch.setattr(DbEngine, "get_readonly_engine", replica_forbidden)
    payload = dict(
        project_uid=b.source_board.get_uid(),
        card_uid=b.source.get_uid(),
        content="Review pixels",
        anchor=selector(b),
        request_uid=str(uuid4()),
    )
    async with Client(registered(b, "add_card_comment")) as client:
        schema = (await client.list_tools())[0]
        assert schema.input_schema["properties"]["request_uid"]["anyOf"][0]["format"] == "uuid"
        assert schema.output_schema["properties"]["comment"]["properties"]["anchor"]
        first = (await client.call_tool("add_card_comment", payload)).structured_content
        second = (await client.call_tool("add_card_comment", payload)).structured_content
        assert first["replayed"] is False and second["replayed"] is True
        assert first["comment"] == second["comment"]
        assert first["comment"]["anchor"] == selector(b)
        assert "image_request_uid" not in str(first)
        with pytest.raises(ToolError):
            await client.call_tool("add_card_comment", {**payload, "content": "Mismatched retry"})
        for invalid in (None, "invalid-uuid", 1):
            with pytest.raises(ToolError):
                await client.call_tool("add_card_comment", {**payload, "request_uid": invalid})
        with pytest.raises(ToolError):
            await client.call_tool("add_card_comment", {**payload, "anchor": {**selector(b), "image_width": "1920"}})
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(CardComment)).all()
    assert len(rows) == 1 and b.comments.dispatch_created.call_count == 1
    projected = public_comment(rows[0].api_response())
    page = dict(items=[projected], total_count=1, next_cursor=None, limit=10)
    dto = CardBundleDto(core={}, workflow={}, comments=page)
    assert dto.model_dump(mode="json")["comments"]["items"][0]["anchor"] == selector(b)


async def test_real_native_foreign_body_provenance_never_downloads(image_board, monkeypatch):
    b = image_board
    with DbSession.use(readonly=False) as db:
        attachment = db.exec(SqlBuilder.select.table(CardAttachment)).first()
        attachment.card_id = b.source.id + 100
        db.update(attachment)
    with DbSession.use(readonly=False) as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == b.source.id)).first()
        card.description = EditorContentModel(content="![foreign](/file/local/card_attachment/immutable.png)")
        db.update(card)
    monkeypatch.setattr(
        CardImagesMcp,
        "Env",
        SimpleNamespace(PUBLIC_UI_URL="https://board.example", API_URL="https://board.example/api"),
    )
    download = Mock()
    monkeypatch.setattr(CardImagesMcp.Storage, "download", download)
    result = CardImagesMcp.read_card_images(b.source_board.get_uid(), b.source.get_uid(), b.actor, b.native)
    assert result.structured_content["included"] == []
    assert result.structured_content["omitted"][0]["reason"] == "attachment_not_in_card"
    download.assert_not_called()
    anchor = CardCommentImageAnchorModel.model_validate(selector(b))
    with pytest.raises(ValueError, match="unavailable"):
        CardMcp.add_card_comment(
            b.source_board.get_uid(),
            b.source.get_uid(),
            "Foreign",
            b.actor,
            b.native,
            anchor=anchor,
            request_uid=uuid4(),
        )


async def test_registered_image_read_uses_real_native_three_value_resolver(image_board, monkeypatch):
    b = image_board
    resolved = b.service.resolve_readable_card(b.source_board.get_uid(), b.source.get_uid(), b.actor)
    assert resolved is not None and len(resolved) == 3
    monkeypatch.setattr(
        CardImagesMcp,
        "Env",
        SimpleNamespace(PUBLIC_UI_URL="https://board.example", API_URL="https://board.example/api"),
    )
    calls = []

    def download(storage, name, filename, destination):
        calls.append((storage, name, filename))
        destination.write(b.image_bytes)
        return True

    monkeypatch.setattr(CardImagesMcp.Storage, "download", download)
    async with Client(registered(b, "read_card_images")) as client:
        result = await client.call_tool(
            "read_card_images",
            dict(
                project_uid=b.source_board.get_uid(),
                card_uid=b.source.get_uid(),
                include_attachment_uids=[b.attachment.get_uid()],
                include_body_images=False,
            ),
        )
    assert not result.is_error
    assert [block.type for block in result.content] == ["text", "image"]
    assert calls == [("local", "card_attachment", "immutable.png")]
    assert result.structured_content["included"][0]["attachment_uid"] == b.attachment.get_uid()
    assert result.structured_content["included"][0]["image_revision"] == b.attachment.image_revision
    assert result.structured_content["included"][0]["image_width"] == 1920
    assert result.structured_content["included"][0]["image_height"] == 1080


async def test_registered_mismatched_dimensions_rejected_then_valid_retry(image_board):
    b = image_board
    payload = dict(
        project_uid=b.source_board.get_uid(),
        card_uid=b.source.get_uid(),
        content="Review pixels",
        anchor={**selector(b), "image_height": 1079},
        request_uid=str(uuid4()),
    )
    async with Client(registered(b, "add_card_comment")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("add_card_comment", payload)
        with DbSession.use(readonly=False) as db:
            assert db.exec(SqlBuilder.select.table(CardComment)).all() == []
        b.comments.dispatch_created.assert_not_called()
        payload["anchor"] = selector(b)
        first = (await client.call_tool("add_card_comment", payload)).structured_content
        again = (await client.call_tool("add_card_comment", payload)).structured_content
        assert first["replayed"] is False and again["replayed"] is True
        assert first["comment"] == again["comment"]
    b.comments.dispatch_created.assert_called_once()
