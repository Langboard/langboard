"""Final compaction reads must respect invalidation observed later in the request."""

import json
from unittest.mock import AsyncMock
import pytest
from langboard_shared.ai.comment_compaction import CompactionDraft
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import CardComment, InternalBot, ProjectRole
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardCommentCompactionService import CommentCompactionError
from sqlalchemy import update
from src.api.tests.model_providers.conftest import compactions  # noqa: F401
from src.api.tests.model_providers.test_comment_compaction_command import command, compact_command, draft  # noqa: F401
from src.api.tests.model_providers.test_compaction_semantic_index import generate, index, semantic  # noqa: F401


def change_source(change, comment, role):
    # Use a separate native connection, so PostgreSQL commits the change while
    # the read transaction remains open. SQLite uses its fixture's StaticPool.
    with DbEngine.get_main_engine().begin() as db:
        if change == "permission":
            changed = db.execute(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=[]))
        else:
            values = ({"deleted_at": SafeDateTime.now()} if change == "delete"
                      else {"content": EditorContentModel(content="Corrected after the earlier validation")})
            changed = db.execute(update(CardComment).where(CardComment.id == comment.id).values(**values))
        assert changed.rowcount == 1


@pytest.mark.parametrize("read", ["direct", "history"])
@pytest.mark.parametrize("change", ["edit", "delete", "permission"])
async def test_completed_read_rechecks_after_later_source_validation(compact_command, monkeypatch, read, change):  # noqa: F811
    service, actor, project, card, _, comment, _, role = compact_command
    monkeypatch.setattr(command, "request_compaction_draft", AsyncMock(side_effect=lambda _, snapshot: draft(snapshot)))
    completed = await service.generate(actor, project, card)
    assert completed["status"] == "completed"
    assert service.get(actor, project, card, completed["uid"])["draft"] == completed["draft"]
    assert service.get_page(actor, project, card)["records"][0]["uid"] == completed["uid"]
    original_block, original_get = service._completed_block, service.get
    armed, changed, projections = False, False, []

    def project_then_change(*args, **kwargs):
        nonlocal changed
        block = original_block(*args, **kwargs)
        if armed:
            projections.append(block["status"])
            if not changed:
                assert block["status"] == "completed"
                change_source(change, comment, role)
                changed = True
        return block

    def get_during_page(*args, **kwargs):
        nonlocal armed
        armed = True
        return original_get(*args, **kwargs)

    monkeypatch.setattr(service, "_completed_block", project_then_change)
    monkeypatch.setattr(service, "get", get_during_page)

    def read_result():
        if read == "direct":
            return service.get(actor, project, card, completed["uid"])
        return service.get_page(actor, project, card)["records"][0]

    if change == "permission":
        with pytest.raises(CommentCompactionError) as denied:
            read_result()
        assert denied.value.status == 403
    else:
        result = read_result()
        assert projections[0] == "completed" and "invalidated" in projections[1:]
        assert result["status"] == "invalidated" and result["reason"] == "source_changed"
        assert result["has_more_sources"] is True
        assert not {"draft", "evidence_refs", "source_revision", "generated_by"}.intersection(result)
        assert result["request_key"] == completed["request_key"]
    assert changed


@pytest.fixture(params=["keyword", "entity"])
def lexical_semantic(semantic, monkeypatch, request):  # noqa: F811
    async def enriched(_, snapshot):
        refs = [source.evidence_ref for source in snapshot.sources if "outage" in source.text]
        return CompactionDraft(
            claims=[{"text": "Reported availability issue", "kind": "unresolved", "evidence_refs": refs}],
            keywords=[{"value": "outage", "evidence_refs": refs}] if request.param == "keyword" else [],
            entities=[{"name": "Service", "kind": "technology", "aliases": ["outage"], "evidence_refs": refs}]
            if request.param == "entity" else [],
        )

    monkeypatch.setattr(command, "request_compaction_draft", enriched)
    return semantic, request.param


def search(native, actor, project, card, boundary):
    if boundary == "board":
        return native.card.search_context_by_project(project, "outage", user=actor, paginated=True)
    project, context = native.card.resolve_visibility_context(project, actor, CollaborationChannel.Api)
    return native.card_comment_compaction.search_metadata_candidates(actor, project, context, "outage")


def search_projection(result, card, boundary):
    if boundary == "board":
        assert result["total_count"] == 1 and result["next_fields"] is None
        assert [item["uid"] for item in result["cards"]] == [card.get_uid()]
        return result["cards"][0]["search_metadata"]
    assert list(result) == [card.id]
    return result[card.id]


@pytest.mark.parametrize("boundary", ["candidate", "board"])
@pytest.mark.parametrize("change", ["edit", "delete", "permission", "embedding"])
async def test_final_semantic_rejection_cannot_restore_stale_lexical_metadata(
    lexical_semantic, monkeypatch, boundary, change,
):
    fixture, term_kind = lexical_semantic
    _, actor, project, card, comment, binding, provider = fixture
    row = await generate(fixture)
    native = DomainService()
    assert await index.index_metadata(native, row.id)
    normal = search_projection(search(native, actor, project, card, boundary), card, boundary)
    assert normal["source"] == "comment_compaction" and normal["derived"] is True
    assert normal["similarity"] and f"comment:{comment.get_uid()}" in normal["similarity"][0]["evidence_refs"]
    field = "keywords" if term_kind == "keyword" else "entities"
    assert normal[field][0]["evidence_refs"] == [f"comment:{comment.get_uid()}"]
    assert normal[field][0]["compaction_uid"] == normal["similarity"][0]["compaction_uid"]
    assert normal[field][0]["source_revision"] == normal["similarity"][0]["source_revision"]
    original = index.validate_metadata_hits
    checks = []
    target = 3 if boundary == "board" else 2
    with DbSession.use(readonly=False) as db:
        role = db.exec(SqlBuilder.select.table(ProjectRole).where(
            ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id,
        )).first()

    def validate_after_change(*args, **kwargs):
        if len(checks) + 1 == target:
            if change == "embedding":
                with DbEngine.get_main_engine().begin() as db:
                    assert db.execute(update(InternalBot).where(InternalBot.id == binding.id)
                                      .values(is_default=False)).rowcount == 1
            else:
                change_source(change, comment, role)
        valid = original(*args, **kwargs)
        checks.append(len(valid))
        return valid

    monkeypatch.setattr(index, "validate_metadata_hits", validate_after_change)
    result = search(native, actor, project, card, boundary)
    assert checks == [1] * (target - 1) + [0]
    if change == "embedding":
        # Embedding authority is separate: valid lexical evidence still matches.
        fallback = search_projection(result, card, boundary)
        assert "similarity" not in fallback
        assert fallback[field] == normal[field]
    elif boundary == "board":
        assert result == {"cards": [], "total_count": 0, "next_fields": None}
    else:
        assert result == {}
    assert provider.document_calls == 1 and provider.query_calls == 2
    assert "fixture-private-model-key" not in json.dumps(result)


@pytest.mark.parametrize("boundary", ["candidate", "board"])
@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_final_metadata_invalidation_cannot_preserve_earlier_semantic_hit(
    semantic, monkeypatch, boundary, change,  # noqa: F811
):
    _, actor, project, card, comment, _, _ = semantic
    row = await generate(semantic)
    native = DomainService()
    assert await index.index_metadata(native, row.id)
    assert search_projection(search(native, actor, project, card, boundary), card, boundary)["similarity"]
    compactor = native.card_comment_compaction
    original = compactor.get_search_metadata
    projections = []
    target = 4 if boundary == "board" else 3

    def metadata_after_change(*args, **kwargs):
        if len(projections) + 1 == target:
            change_source(change, comment, None)
        metadata = original(*args, **kwargs)
        projections.append(len(metadata["keywords"]))
        return metadata

    monkeypatch.setattr(compactor, "get_search_metadata", metadata_after_change)
    result = search(native, actor, project, card, boundary)
    assert projections[:target] == [1] * (target - 1) + [0]
    assert not any(projections[target:])
    if boundary == "board":
        assert result == {"cards": [], "total_count": 0, "next_fields": None}
    else:
        assert result == {}
