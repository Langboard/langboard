"""Read-boundary invalidation through native evidence pages and vector stores."""

from unittest.mock import AsyncMock
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.ActivityService import ActivityService
from langboard_shared.domain.services.factory.CardCommentCompactionService import CommentCompactionError
from test_comment_compaction_command import command, compact_command, draft  # noqa: F401
from test_compaction_semantic_index import generate, index, semantic  # noqa: F401


@pytest.mark.parametrize("change", ["edit", "delete", "permission"])
async def test_evidence_page_revalidates_after_original_projection(compact_command, monkeypatch, change):  # noqa: F811
    service, actor, project, card, _, comment, _, role = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    result = await service.generate(actor, project, card)
    assert result["status"] == "completed"
    ref = f"comment:{comment.get_uid()}"
    normal = service.get_evidence(actor, project, card, result["uid"], evidence_ref=ref)
    assert [item["evidence_ref"] for item in normal["records"]] == [ref]
    activity = service._get_service(ActivityService)
    original = activity.get_card_timeline
    projected = []

    def project_then_revoke(*args, **kwargs):
        page = original(*args, **kwargs)
        projected.extend(page["records"])
        with DbSession.atomic() as db:
            if change == "permission":
                role.actions = []
                db.update(role)
            else:
                if change == "edit":
                    comment.content = EditorContentModel(content="Corrected after original projection")
                else:
                    comment.deleted_at = SafeDateTime.now()
                db.update(comment)
        return page

    monkeypatch.setattr(activity, "get_card_timeline", project_then_revoke)
    with pytest.raises(CommentCompactionError) as denied:
        service.get_evidence(actor, project, card, result["uid"], evidence_ref=ref)
    assert projected and projected[0]["evidence_ref"] == ref
    if change == "permission":
        assert denied.value.status == 403
    else:
        assert denied.value.code == "compaction_invalidated"


@pytest.mark.parametrize("mode", ["card", "board"])
async def test_semantic_query_rejects_source_deleted_during_query_inference(semantic, mode):  # noqa: F811
    _, actor, project, card, comment, _, provider = semantic
    row = await generate(semantic)
    native = DomainService()
    assert await index.index_metadata(native, row.id)
    query = "availability interrupted"
    normal = index.search_metadata(native, actor, project, card, query, channel=CollaborationChannel.Api)
    assert normal and f"comment:{comment.get_uid()}" in normal[0]["evidence_refs"]

    def delete_source():
        with DbSession.atomic() as db:
            comment.deleted_at = SafeDateTime.now()
            db.update(comment)

    provider.query_hook = delete_source
    if mode == "card":
        assert index.search_metadata(native, actor, project, card, query, channel=CollaborationChannel.Api) == []
    else:
        assert native.card.search_context_by_project(project, query, user=actor) == []
    assert provider.query_hook is None
