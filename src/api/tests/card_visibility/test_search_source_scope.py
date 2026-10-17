# ruff: noqa: F811
"""Bounded search must not disclose foreign or removed workflow sources."""

import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, CardAttachment, CardComment, ProjectColumn
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository


@pytest.mark.parametrize("projection", ["cards", "documents"])
def test_search_filters_workflow_source_before_limit_and_snippets(board, projection):
    _, user, project, *_rest = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardAttachment, CardComment):
        model.__table__.create(engine)
    context = CardVisibilityContext(
        channel=CollaborationChannel.HumanUI,
        active=True,
        project_member=True,
        internal_member=True,
        actor_user_id=user.id,
    )
    repository = CardRepository(None, None)
    file = FileModel(
        storage_type="test",
        storage_name="test",
        original_filename="source.pdf",
        filename="source.pdf",
        path="/test/source.pdf",
    )
    with DbSession.use(readonly=False) as db:
        local = ProjectColumn(project_id=project.id, name="Current workflow")
        foreign = ProjectColumn(project_id=11, name="Foreign private workflow title")
        removed = ProjectColumn(project_id=project.id, name="Removed workflow", deleted_at=SafeDateTime.now())
        for column in (local, foreign, removed):
            db.insert(column)
        cards = []
        for index, column in enumerate((local, foreign, removed)):
            card = Card(
                project_id=project.id,
                project_column_id=column.id,
                title="source 한글",
                visibility="SHARED",
                updated_at=SafeDateTime(2026, 10, index + 1),
            )
            db.insert(card)
            cards.append(card)
            db.insert(
                CardAttachment(
                    card_id=card.id,
                    user_id=user.id,
                    filename="source.pdf",
                    file=file,
                    document_text="source 한글 authorized transcription",
                )
            )
    if projection == "cards":
        for include_closed in (True, False):
            found = repository.search_context_by_project(
                project,
                "source 한글",
                limit=1,
                context=context,
                include_closed=include_closed,
            )
            assert [card.id for card, _ in found] == [cards[0].id]
            assert [column.name for _, column in found] == [local.name]
    else:
        found = repository.search_document_matches(project, [card.id for card in cards], "source 한글", context=context)
        assert set(found) == {cards[0].id}
        assert "source 한글" in found[cards[0].id][0]["snippet"]
