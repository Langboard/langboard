# ruff: noqa: F811
"""Bounded search must not disclose foreign or removed workflow sources."""

import json
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, CardAttachment, CardComment, CardMetadata, ProjectColumn
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from sqlalchemy import text


@pytest.mark.parametrize("source", ["body", "comment"])
@pytest.mark.parametrize("encoding", ["model-string", "json-object"])
def test_search_matches_editor_text_without_json_keys_or_escape_syntax(board, source, encoding):
    _, user, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardAttachment, CardComment, CardMetadata):
        model.__table__.create(engine)
    body = 'alpha\nbeta\tשלום\\path "quoted" 100%_done 日本語 中文'
    with DbSession.use(readonly=False) as db:
        plain = Card(project_id=project.id, project_column_id=columns[0].id, title="Plain", visibility="SHARED")
        actual_word = Card(
            project_id=project.id, project_column_id=columns[0].id, title="Real word", visibility="SHARED"
        )
        for card in (plain, actual_word):
            db.insert(card)
        if source == "body":
            plain.description = EditorContentModel(content=body)
            actual_word.description = EditorContentModel(content="content is a real word here")
            db.update(plain)
            db.update(actual_word)
            table, field, identifier = "card", "description", int(plain.id)
        else:
            comment = CardComment(card_id=plain.id, user_id=user.id, content=EditorContentModel(content=body))
            db.insert(comment)
            db.insert(
                CardComment(
                    card_id=actual_word.id,
                    user_id=user.id,
                    content=EditorContentModel(content="content is a real word here"),
                )
            )
            table, field, identifier = "card_comment", "content", int(comment.id)
    payload = {"content": body, "audit": "private-json-marker"}
    stored = json.dumps(json.dumps(payload)) if encoding == "model-string" else json.dumps(payload)
    assignment = "CAST(:body AS JSON)" if engine.dialect.name == "postgresql" else ":body"
    with engine.begin() as connection:
        # Fixture covers both persisted formats accepted by ModelColumnType.
        connection.execute(
            text(f"UPDATE {table} SET {field}={assignment} WHERE id=:id"), {"body": stored, "id": identifier}
        )
    repository = CardRepository(None, None)
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True, actor_user_id=user.id)

    def matches(query):
        return {card.id for card, _ in repository.search_context_by_project(project, query, context=context)}

    assert matches("content") == {actual_word.id}
    assert matches("private-json-marker") == set()
    for query in ("alpha\nbeta", "beta\tשלום", '"quoted"', "שלום\\path", "100%_done", "日本語 中文"):
        assert matches(query) == {plain.id}, query
    payload["content"] = "prefix\x00suffix"
    stored = json.dumps(json.dumps(payload)) if encoding == "model-string" else json.dumps(payload)
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {table} SET {field}={assignment} WHERE id=:id"), {"body": stored, "id": identifier}
        )
    assert matches("suffix") == {plain.id}


@pytest.mark.parametrize("projection", ["cards", "documents"])
def test_search_filters_workflow_source_before_limit_and_snippets(board, projection):
    _, user, project, *_rest = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardAttachment, CardComment, CardMetadata):
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
