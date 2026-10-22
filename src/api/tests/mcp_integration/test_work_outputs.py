"""Native projections and pagination must survive typed transport serialization."""

import json
from types import SimpleNamespace
import pytest
from fastmcp import Client
from langboard.card_workspace.application.projections import public_checklist
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context


@pytest.mark.parametrize("profile", ["agent", "raw", "compatibility"])
@pytest.mark.parametrize("malformed", [False, True])
async def test_nested_work_output_omission_pagination_and_unknown_after_write(monkeypatch, profile, malformed):
    from langboard.card_workspace.application.projections import bounded_items

    name = "update_card_checklist" if profile == "raw" else "update_card_checkitem"
    projected = public_checklist(
        {
            "uid": "list",
            "title": "Tasks",
            "is_checked": False,
            "checkitems": [{"uid": str(i), "title": "t" * 1001, "deadline_at": None} for i in range(30)],
        }
    )
    if malformed:
        projected["checkitems"][0]["uid"] = 1
    payload = {"checklists": bounded_items([projected], "checklists", 25)}
    expected = {"checklists": payload["checklists"].model_dump()}
    effects = []

    def command(value: int) -> dict:
        effects.append(value)
        return payload

    metadata = {"handler": command, "description": "Update checklist", "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {name: metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda tool_name: metadata if tool_name == name else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    _, server = McpServer.get_http_app(profile)
    token = mcp_auth_context.set(
        {"user_or_bot": object(), "tool_group": SimpleNamespace(activated_at=object(), tools=[name])}
    )
    try:
        async with Client(server) as client:
            if profile == "raw":
                search = await client.call_tool("search_raw_tools", {"pattern": "^" + name + r"\b"})
                schema = json.loads(search.content[0].text)[0]["outputSchema"]
            else:
                schema = (await client.list_tools())[0].output_schema
            if profile != "compatibility":
                page = schema["properties"]["checklists"]
                nested = page["properties"]["items"]["items"]["properties"]["checkitems"]["items"]
                assert nested["properties"]["uid"]["type"] == "string"
                assert page["properties"]["next_cursor"]
            result = await client.call_tool(
                "call_raw_tool" if profile == "raw" else name,
                {"name": name, "arguments": {"value": 1}} if profile == "raw" else {"value": 1},
                raise_on_error=False,
            )
            assert effects == [1]
            if malformed and profile != "compatibility":
                assert result.is_error
                assert result.meta["mutation_receipt"]["outcome"] == "unknown"
                assert result.meta["mutation_receipt"]["retryable"] is False
            else:
                assert not result.is_error
                assert result.structured_content == expected
                item = result.structured_content["checklists"]["items"][0]
                assert item["checkitems_next_cursor"]
                assert item["checkitems"][0]["deadline_at"] is None
                assert "updated_at" not in item["checkitems"][0]
                assert item["checkitems"][0]["title_truncated"] is True
    finally:
        mcp_auth_context.reset(token)


def test_real_native_models_preserve_dates_and_comment_fields():
    from typing import Any
    from langboard.card_workspace.application.projections import public_checkitem, public_comment
    from langboard.mcp_integration.WorkOutputs import CheckitemOutput, ChecklistOutput, CommentOutput
    from langboard_shared.core.db import EditorContentModel
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import CardComment, Checkitem, Checklist
    from pydantic import TypeAdapter

    now = SafeDateTime.now()
    checklist = Checklist(id=123, card_id=456, title="t" * 1001, created_at=now, updated_at=now)
    item = Checkitem(id=789, checklist_id=123, title="Task", created_at=now, updated_at=now)
    comment = CardComment(
        id=101, card_id=456, content=EditorContentModel(content="한글 댓글"), created_at=now, updated_at=now
    )
    for projection, model in [
        (public_checklist({**checklist.api_response(), "checkitems": [item.api_response()]}), ChecklistOutput),
        (public_checkitem(item.api_response()), CheckitemOutput),
        (public_comment(comment.api_response()), CommentOutput),
    ]:
        assert model.model_validate(projection).model_dump(mode="json") == TypeAdapter(dict[str, Any]).dump_python(
            projection, mode="json"
        )


def test_comment_selector_projection_is_validated_and_typed():
    from langboard.card_workspace.application.dtos import CardBundleContinuationDto
    from langboard.card_workspace.application.projections import public_comment
    from langboard.mcp_integration.WorkOutputs import CommentOutput
    from pydantic import ValidationError

    text = public_comment(dict(uid="comment", content="Evidence", anchor={"exact": "legacy text"}))
    assert text["anchor"]["type"] == "TextQuoteSelector"
    assert CommentOutput.model_validate(text).model_dump()["anchor"] == text["anchor"]
    page = dict(items=[text], total_count=1, next_cursor=None, limit=10)
    continuation = CardBundleContinuationDto(section="comments", page=page)
    assert continuation.model_dump()["page"]["items"][0]["anchor"] == text["anchor"]
    with pytest.raises(ValidationError):
        public_comment(dict(uid="comment", content="Evidence", anchor={"exact": "quote", "url": "private"}))


def test_attachment_revision_is_safe_optional_native_evidence():
    from langboard.card_workspace.application.projections import public_attachment
    from langboard.mcp_integration.ResourceOutputs import PublicAttachmentOutput
    from pydantic import ValidationError
    projected = public_attachment(dict(uid="image", image_revision="a" * 64,
        url="/private/storage", file={"storage_name": "private"}))
    assert PublicAttachmentOutput.model_validate(projected).model_dump() == {
        "uid": "image", "image_revision": "a" * 64}
    assert PublicAttachmentOutput.model_validate({"uid": "legacy"}).model_dump() == {"uid": "legacy"}
    for invalid in ("A" * 64, "a" * 63, 1):
        with pytest.raises(ValidationError):
            PublicAttachmentOutput.model_validate({"uid": "image", "image_revision": invalid})


def test_card_bundle_native_output_schema_preserves_frozen_contract():
    from pathlib import Path
    from langboard.mcp_integration.Providers import create_native_tool
    from langboard.mcp_tools import CardMcp

    name = CardMcp.get_card_bundle.__name__
    baseline = json.loads((Path(__file__).parent / "fixtures" / "legacy-native-v1.json").read_text())
    expected = next(tool["output_schema"] for tool in baseline["contract"]["tools"] if tool["name"] == name)
    native = create_native_tool(name, McpTool.get_tool(name), McpServer._wrap_tool, modern_annotations=False)
    assert native.output_schema == expected


@pytest.mark.parametrize("model_page", [False, True])
def test_comment_continuation_preserves_image_activity_and_compaction(model_page):
    from langboard.card_workspace.application.dtos import BoundedItemsDto, CardBundleResponse
    from langboard.card_workspace.application.projections import public_comment

    anchor = {
        "type": "ImageRegionSelector", "version": 1,
        "source": {"type": "card_attachment", "attachment_uid": "image", "revision": "a" * 64},
        "shape": "point", "image_width": 100, "image_height": 100,
        "x": 10, "y": 20, "width": 0, "height": 0,
    }
    comment = public_comment({"uid": "comment", "content": "Image review", "anchor": anchor,
                              "user": {"uid": "user"}, "reactions": {"like": ["user"]},
                              "reaction_counts": {"like": 1}})
    activity = {
        "kind": "activity", "uid": "move", "evidence_ref": "activity:move",
        "created_at": None, "actor": {"uid": "user"},
        "from_column": {"name": "Doing"}, "to_column": {"name": "Review"},
    }
    compaction = {
        "kind": "compaction", "uid": "summary", "evidence_ref": "compaction:summary",
        "created_at": None,
        "summary": {
            "source_revision": "revision", "covered_from": "comment", "covered_through": "move",
            "evidence_refs": ["comment:comment", "activity:move"],
            "claims": [{"kind": "decision", "text": "Review image", "evidence_refs": ["comment:comment"]}],
            "search_metadata": {"source": "comment_compaction", "derived": True,
                                "keywords": [{"value": "image", "evidence_refs": ["comment:comment"]}],
                                "entities": []},
            "generated_by": {"uid": "bot", "display_name": "Summarizer"},
        },
    }
    page = dict(items=[comment, activity, compaction], total_count=9, next_cursor="opaque-next", limit=3)
    payload = {"card_uid": "card", "continuation": {
        "section": "comments", "page": BoundedItemsDto(**page) if model_page else page,
    }}
    serialized = CardBundleResponse.model_validate(payload).model_dump(mode="json")
    assert serialized["continuation"]["page"] == page
    assert serialized["continuation"]["page"]["items"][0]["anchor"] == anchor


@pytest.mark.parametrize("model_page", [False, True])
@pytest.mark.parametrize("malformed", [
    {"old_generic_field": True},
    {"uid": 1, "content": "Evidence", "content_format": "text",
     "content_total_chars": 8, "content_truncated": False},
    {"uid": "comment", "content": "Evidence", "content_format": "text",
     "content_total_chars": 8, "content_truncated": False, "anchor": {"exact": "quote", "url": "private"}},
    {"kind": "activity", "uid": "move"},
    {"kind": "compaction", "uid": "summary", "evidence_ref": "compaction:summary", "created_at": None},
])
def test_comment_continuation_rejects_malformed_entries(model_page, malformed):
    from langboard.card_workspace.application.dtos import BoundedItemsDto, CardBundleResponse
    from pydantic import ValidationError

    page = dict(items=[malformed], total_count=1, next_cursor=None, limit=10)
    with pytest.raises(ValidationError):
        CardBundleResponse.model_validate({"card_uid": "card", "continuation": {
            "section": "comments", "page": BoundedItemsDto(**page) if model_page else page,
        }})
