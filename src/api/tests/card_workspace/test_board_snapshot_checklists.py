"""The board response must reuse its authorized checklist read without leaking system lists."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from langboard.routes.board.BoardApi import JsonResponse, get_project_cards
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, CardComment, Checklist
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.ChecklistService import ChecklistService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.ChecklistRepository import ChecklistRepository
from sqlalchemy import event


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
    monkeypatch.setattr(card_repository, "get_board_creators", lambda *args: {})
    repository = SimpleNamespace(
        card=card_repository,
        card_assigned_user=SimpleNamespace(get_all_by_project=lambda *args: []),
        card_relationship=SimpleNamespace(get_all_by_project=lambda *args: []),
        project_label=SimpleNamespace(get_all_card_labels_by_project=lambda *args: []),
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
        project=SimpleNamespace(get_by_id_like=lambda _: project),
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
    legacy = JsonResponse(
        content=service.checklist.get_api_list_only_by_project(project, archive_visible_since=cutoff, context=context)
    )
    assert payload["checklists"] == json.loads(legacy.body)
    assert len(selects) == 1, "The board response must reuse its already-read checklist rows"

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
