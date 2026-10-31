"""Authenticated actors reach native checklist writers without weakening app guards."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.card_workspace.domain import ChecklistProjectionItem
from langboard.card_workspace.infrastructure.native import NativeCardWorkspaceAdapter
from langboard.mcp_tools import CardMcp
from langboard.routes.board.BoardCardCheckitemApi import (
    change_checkitem_deadline,
    change_checkitem_order_or_move_checklist,
)
from langboard.routes.board.BoardCardChecklistApi import change_checklist_order
from langboard.routes.board.forms import ChangeCardCheckitemDeadlineForm, ChangeChildOrderForm, ChangeRootOrderForm
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing.ApiException import NotFoundException
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardAppOwnership,
    CardChangeReceipt,
    CardMetadata,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    EmployeeMembershipPolicy,
    ProjectRole,
    UserIdentityLink,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.filter import RoleFilter
from langboard_shared.publishers import CardPublisher, CheckitemPublisher, ChecklistPublisher
from sqlalchemy import select, text


@pytest.fixture
def mutation_board(board, monkeypatch):
    _, user, project, _, role, columns, _ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (
        Card, Checklist, Checkitem, CheckitemTimerRecord, CardChangeReceipt, CardAppOwnership,
        CardMetadata, EmployeeMembershipPolicy, UserIdentityLink,
    ):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        if engine.dialect.name == "postgresql":
            db.exec(text("CREATE SEQUENCE content_change_seq"))
        role.actions = ["read", "card_update"]
        db.update(role)
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Native", visibility="SHARED")
        db.insert(card)
        checklist = Checklist(card_id=card.id, title="Tasks", order=0)
        db.insert(checklist)
        item = Checkitem(checklist_id=checklist.id, title="Task", order=0)
        db.insert(item)
        db.insert(CardMetadata(card_id=card.id, key="projection.checklist.fixture", value=json.dumps({
            "version": 1, "checklist_uid": checklist.get_uid(), "items": {"task": item.get_uid()},
        })))
    events = [Mock() for _ in range(4)]
    monkeypatch.setattr(CardPublisher, "metadata_changed", events[0])
    monkeypatch.setattr(ChecklistPublisher, "order_changed", events[1])
    monkeypatch.setattr(CheckitemPublisher, "order_changed", events[2])
    monkeypatch.setattr(CheckitemPublisher, "deadline_changed", events[3])
    service = DomainService()
    deadline = SafeDateTime.fromisoformat("2026-11-01T12:00:00+00:00")
    yield SimpleNamespace(
        user=user, project=project, card=card, checklist=checklist, item=item, service=service,
        engine=engine, events=events, deadline=deadline,
    )
    service.close()


@pytest.mark.parametrize("operation", ["checklist_order", "checkitem_order", "checkitem_deadline"])
def test_native_mutations_require_actor_and_preserve_app_guard(mutation_board, operation):
    fixture = mutation_board
    args = {"project": fixture.project.get_uid(), "card": fixture.card.get_uid()}
    if operation == "checklist_order":
        mutation = fixture.service.checklist.change_order
        args.update(checklist=fixture.checklist.get_uid(), order=1)
    else:
        mutation = getattr(fixture.service.checkitem, "change_" + operation.removeprefix("checkitem_"))
        args["checkitem"] = fixture.item.get_uid()
        args.update({"order": 1} if operation == "checkitem_order" else {"deadline_at": fixture.deadline})
    with pytest.raises(TypeError, match="user_or_bot"):
        mutation(**args)
    with pytest.raises(AppGovernanceDenied):
        mutation(object(), **args)
    with DbSession.atomic() as db:
        db.insert(CardAppOwnership(card_id=fixture.card.id, app_key="fixture-app"))
    bot = Bot(name="Automation", bot_uname="fixture-bot", app_api_token="test-only",
              platform="default", platform_running_type="default")
    with pytest.raises(AppGovernanceDenied):
        mutation(bot, **args)
    assert mutation(fixture.user, **{**args, "project": 11}) is None
    assert not any(event.called for event in fixture.events)
    with fixture.engine.connect() as db:
        assert db.execute(select(Card.last_change_seq)).scalar_one() == 0
        assert db.execute(select(CardChangeReceipt.id)).all() == []
    with DbSession.atomic():
        assert mutation(fixture.user, **args) is True
        assert not any(event.called for event in fixture.events)
    fixture.events[0].assert_called_once()
    with fixture.engine.connect() as db:
        assert db.execute(select(CardChangeReceipt.change_seq)).scalars().all() == [1]
        if operation == "checklist_order":
            assert db.execute(select(Checklist.order)).scalar_one() == 1
        elif operation == "checkitem_order":
            assert db.execute(select(Checkitem.order)).scalar_one() == 1
        else:
            assert db.execute(
                select(Checkitem.id).where(Checkitem.deadline_at == fixture.deadline)
            ).scalar_one() == fixture.item.id


@pytest.mark.parametrize("operation", ["checklist_order", "checkitem_order", "checkitem_deadline"])
def test_rest_forwards_actor_through_native_scope_and_writer(mutation_board, monkeypatch, operation):
    fixture = mutation_board
    if operation == "checklist_order":
        route, owner, method, child = change_checklist_order, fixture.service.checklist, "change_order", fixture.checklist
        form = ChangeRootOrderForm(order=1)
    elif operation == "checkitem_order":
        route = change_checkitem_order_or_move_checklist
        owner, method, child = fixture.service.checkitem, "change_order", fixture.item
        form = ChangeChildOrderForm(order=1)
    else:
        route, owner, method, child = change_checkitem_deadline, fixture.service.checkitem, "change_deadline", fixture.item
        form = ChangeCardCheckitemDeadlineForm(deadline_at=fixture.deadline.isoformat())
    assert AuthFilter.exists(route)
    role, actions, _, _ = RoleFilter.get_filtered(route)
    assert role is ProjectRole and actions == ["card_update"]
    mutation = Mock(wraps=getattr(owner, method))
    monkeypatch.setattr(owner, method, mutation)
    request = SimpleNamespace(scope={})
    with pytest.raises(NotFoundException):
        route(fixture.project.get_uid(), fixture.card.get_uid(), request, fixture.card.get_uid(), form,
              fixture.user, fixture.service)
    mutation.assert_not_called()
    response = route(fixture.project.get_uid(), fixture.card.get_uid(), request, child.get_uid(), form,
                     fixture.user, fixture.service)
    assert response.status_code == 200
    mutation.assert_called_once()
    assert mutation.call_args.args[0] is fixture.user
    with fixture.engine.connect() as db:
        assert db.execute(select(CardChangeReceipt.change_seq)).scalars().all() == [1]


@pytest.mark.parametrize("caller", ["mcp", "workspace"])
def test_deadline_callers_forward_actor_to_native_storage(mutation_board, monkeypatch, caller):
    fixture = mutation_board
    mutation = Mock(wraps=fixture.service.checkitem.change_deadline)
    monkeypatch.setattr(fixture.service.checkitem, "change_deadline", mutation)
    if caller == "mcp":
        result = CardMcp.update_card_checkitem(
            fixture.project.get_uid(), fixture.card.get_uid(), fixture.item.get_uid(),
            fixture.user, fixture.service, deadline_at=fixture.deadline.isoformat(),
        )
        assert result["checklists"].items[0]["checkitems"][0]["uid"] == fixture.item.get_uid()
    else:
        result = NativeCardWorkspaceAdapter(fixture.user, fixture.service).reconcile_card_checklist_projection(
            fixture.project.get_uid(), fixture.card.get_uid(), "fixture", "Tasks",
            [ChecklistProjectionItem("task", "Task", False, fixture.deadline.isoformat())], None,
        )
        assert result["changed"] is True
        assert result["checklist"]["checkitems"][0]["uid"] == fixture.item.get_uid()
    mutation.assert_called_once()
    assert mutation.call_args.args[0] is fixture.user
    with fixture.engine.connect() as db:
        assert db.execute(
            select(Checkitem.id).where(Checkitem.deadline_at == fixture.deadline)
        ).scalar_one() == fixture.item.id
        assert db.execute(select(CardChangeReceipt.change_seq)).scalars().all() == [1]
