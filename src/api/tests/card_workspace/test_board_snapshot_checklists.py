"""The board response must reuse its authorized checklist read without leaking system lists."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.routes.board.BoardApi import JsonResponse, get_project_cards
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing.ApiException import NotFoundException
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, CardComment, Checklist
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.ChecklistService import ChecklistService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.ChecklistRepository import ChecklistRepository
from sqlalchemy import event


def test_board_missing_current_context_stops_before_projection():
    resolver = Mock(return_value=None)
    service = SimpleNamespace(card=SimpleNamespace(resolve_visibility_context=resolver))
    actor = object()
    with pytest.raises(NotFoundException) as error:
        get_project_cards("missing-project", SimpleNamespace(scope={}), user_or_bot=actor, service=service)
    assert error.value.status_code == 404
    resolver.assert_called_once_with("missing-project", actor, CollaborationChannel.Api)


@pytest.mark.parametrize("has_excluded_cards", [False, True])
def test_empty_visible_board_stops_after_its_scoped_card_query(board, monkeypatch, has_excluded_cards):
    _, user, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (Bot, Card, CardComment):
        model.__table__.create(engine)
    cutoff = SafeDateTime.now() - timedelta(days=project.archive_visible_days)
    if has_excluded_cards:
        with DbSession.use(readonly=False) as db:
            for fields in (
                {"visibility": "INTERNAL"},
                {"visibility": "PRIVATE", "owner_user_id": 2, "created_by_user_id": 2},
                {"deleted_at": cutoff},
                {"archived_at": cutoff - timedelta(seconds=1)},
            ):
                db.insert(
                    Card(
                        project_id=project.id,
                        project_column_id=columns[0].id,
                        title="Excluded",
                        **{"visibility": "SHARED", **fields},
                    )
                )
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=int(user.id))
    repository = Mock()
    repository.card = CardRepository(None, None)
    service = CardService(None, None, repository)
    service.resolve_visibility_context = Mock(return_value=(project, context))
    statements = []

    def capture(_conn, _cursor, statement, *_):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert service.get_board_snapshot(project, user, cutoff) == ([], [])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1 and "FROM card" in statements[0]
    assert repository.mock_calls == [], "Empty visible boards must not issue enrichment or seen-state operations"


def test_board_response_reads_checklists_once_and_preserves_scope(board, monkeypatch):  # noqa: F811
    _, user, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (Bot, Card, CardComment, Checklist):
        model.__table__.create(engine)
    now = SafeDateTime.now()
    cutoff = now - timedelta(days=project.archive_visible_days)
    monkeypatch.setattr(SafeDateTime, "now", lambda: now)
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=int(user.id))
    rows = {}
    with DbSession.use(readonly=False) as db:
        for title, values in (
            ("Visible", {}),
            ("Recent archive", {"archived_at": cutoff}),
            ("Cold archive", {"archived_at": cutoff - timedelta(seconds=1)}),
            ("Internal", {"visibility": "INTERNAL"}),
            ("Foreign", {"project_id": 11}),
            ("Deleted", {"deleted_at": now}),
            ("Other private", {"visibility": "PRIVATE", "owner_user_id": 2, "created_by_user_id": 2}),
            ("Own private", {"visibility": "PRIVATE", "owner_user_id": user.id, "created_by_user_id": user.id}),
        ):
            card = Card(project_id=project.id, project_column_id=columns[0].id, title=title, visibility="SHARED")
            for key, value in values.items():
                setattr(card, key, value)
            db.insert(card)
            rows[title] = card
            db.insert(Checklist(card_id=card.id, title=title + " checklist"))
        db.insert(Checklist(card_id=rows["Visible"].id, title="Hidden completion", is_system=True, is_checked=True))
        db.insert(Checklist(card_id=rows["Visible"].id, title="Deleted checklist", deleted_at=now))
    card_repository = CardRepository(None, None)
    monkeypatch.setattr(card_repository, "get_board_creators", lambda *args, **kwargs: {})
    repository = SimpleNamespace(
        card=card_repository,
        card_assigned_user=SimpleNamespace(get_all_by_project=lambda *args, context: []),
        card_relationship=SimpleNamespace(get_all_by_project=Mock(return_value=[])),
        project_label=SimpleNamespace(get_all_card_labels_by_project=lambda *args, context: []),
        checklist=ChecklistRepository(None, None),
    )
    cards = CardService(None, None, repository)
    cards.resolve_visibility_context = Mock(return_value=(project, context))
    cards.get_active_workers = Mock(return_value={})
    cards.get_work_states = Mock(
        side_effect=lambda batch, **kwargs: {
            card.id: {"checklist_progress": {"total": 0, "completed": 0}} for card in batch
        }
    )
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=Mock(side_effect=AssertionError("Redundant project lookup"))),
        card=cards,
        checklist=ChecklistService(None, None, repository),
        app_setting=SimpleNamespace(get_api_global_relationship_list=lambda: []),
        project_column=SimpleNamespace(
            get_api_list_by_project=lambda *args, **kwargs: [{"uid": columns[0].get_uid(), "name": "Column"}],
            get_api_bot_scopes_by_project=lambda *args: [],
            get_api_bot_schedule_list_by_project=lambda *args: [],
        ),
    )
    selects = []

    def capture(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith("SELECT") and "FROM checklist" in statement:
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = get_project_cards(
            project.get_uid(),
            SimpleNamespace(scope={"collaboration_channel": context.channel}),
            user_or_bot=object(),
            service=service,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    payload = json.loads(response.body)
    assert {card["title"] for card in payload["cards"]} == {"Visible", "Recent archive", "Own private"}
    assert {item["title"] for item in payload["checklists"]} == {
        "Visible checklist",
        "Recent archive checklist",
        "Own private checklist",
    }
    visible = next(card for card in payload["cards"] if card["title"] == "Visible")
    assert visible["completed"] is True
    cards.get_active_workers.assert_called_once_with(project, context=context)
    repository.card_relationship.get_all_by_project.assert_called_once_with(project, cutoff, context=context)
    legacy = JsonResponse(
        content=service.checklist.get_api_list_only_by_project(project, archive_visible_since=cutoff, context=context)
    )
    assert payload["checklists"] == json.loads(legacy.body)
    assert len(selects) == 1, "The board response must reuse its already-read checklist rows"
    assert cards.resolve_visibility_context.call_args_list[0].args[0] == project.get_uid()
    assert cards.resolve_visibility_context.call_args_list[0].args[2] == context.channel
    service.project.get_by_id_like.assert_not_called()

    # If the second current-access check rejects the request, no old-context
    # checklist read may populate the response independently of its cards.
    cards.resolve_visibility_context.side_effect = [(project, context), None]
    selects.clear()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        rejected = get_project_cards(
            project.get_uid(),
            SimpleNamespace(scope={"collaboration_channel": context.channel}),
            user_or_bot=object(),
            service=service,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    rejected_payload = json.loads(rejected.body)
    assert rejected_payload["cards"] == rejected_payload["checklists"] == []
    assert selects == []
