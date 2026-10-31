"""Native priority persistence, migrations, current audience and committed events."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard.card_workspace.application.projections import public_card_summary
from langboard.card_workspace.application.queries import get_card_bundle, list_project_cards
from langboard.card_workspace.domain import CommentPage, SectionPage
from langboard.card_workspace.domain.value_objects import ProjectCardCursor
from langboard.card_workspace.infrastructure.native import NativeCardWorkspaceAdapter
from langboard.mcp_tools import CardMcp
from langboard.mcp_tools.CardActionsMcp import CardChange
from langboard.routes.board import BoardCardApi
from langboard.routes.board.forms.Card import ChangeCardDetailsForm, CreateCardForm
from langboard_shared.core.db import DbSession
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.contracts.CardPriority import validate_card_priority
from langboard_shared.domain.models import Card
from langboard_shared.domain.services.BoardFilterPreferences import BoardFilterPin
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
from pydantic import TypeAdapter, ValidationError
from src.api.tests.card_visibility.conftest import current_card as current_card
from src.api.tests.card_workspace.test_card_change_counts import counts as counts
from src.api.tests.card_workspace.test_card_change_counts import native_delivery as native_delivery
from src.api.tests.card_workspace.test_work_plan_followup_fields import followup_board as followup_board


def test_saved_priority_filter_accepts_native_values_only():
    for priority in ("P0", "P1", "P2", "P3"):
        assert BoardFilterPin(key="priorities", value=priority).value == priority
    for priority in ("P4", "now", "p0", "", " P0"):
        with pytest.raises(ValidationError):
            BoardFilterPin(key="priorities", value=priority)


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_engine(request):
    schema = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_COUNT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_COUNT_TEST_DATABASE_URL to disposable PostgreSQL")
        admin = sa.create_engine(url)
        schema = "card_priority_" + uuid4().hex
        with admin.begin() as db:
            db.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        engine = sa.create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = sa.create_engine("sqlite://")
    try:
        yield engine
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as db:
                db.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def test_migration_backfill_defaults_constraints_and_guarded_downgrade(migration_engine):
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261031010000-efab123d4567.py"
    spec = importlib.util.spec_from_file_location("priority_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with migration_engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE card (id BIGINT PRIMARY KEY, title TEXT NOT NULL)")
        db.exec_driver_sql("INSERT INTO card VALUES (1, '[P0] Legacy title')")
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        assert db.execute(sa.text("SELECT priority FROM card")).scalar_one() == "P2"
        db.exec_driver_sql("INSERT INTO card (id, title) VALUES (2, 'New default')")
        assert db.execute(sa.text("SELECT priority FROM card WHERE id=2")).scalar_one() == "P2"
        for value in ("P0", "P1", "P2", "P3"):
            db.execute(sa.text("UPDATE card SET priority=:value WHERE id=1"), {"value": value})
            assert db.execute(sa.text("SELECT priority FROM card WHERE id=1")).scalar_one() == value
        for value in (None, "P4", "p0", ""):
            with pytest.raises(sa.exc.IntegrityError), db.begin_nested():
                db.execute(sa.text("UPDATE card SET priority=:value WHERE id=1"), {"value": value})
        with pytest.raises(RuntimeError, match="Cannot remove authored"):
            migration.downgrade()
        db.exec_driver_sql("UPDATE card SET priority='P2'")
        migration.downgrade()
        assert db.execute(sa.text("SELECT title FROM card WHERE id=1")).scalar_one() == "[P0] Legacy title"
        migration.upgrade()
        assert db.execute(sa.text("SELECT priority FROM card WHERE id=1")).scalar_one() == "P2"


@pytest.mark.parametrize("value", [None, "P4", "p0", " P0", 0, False, []])
def test_invalid_null_priority_rejected(value):
    with pytest.raises(ValueError):
        validate_card_priority(value)
    for cls, kwargs in ((ChangeCardDetailsForm, {}), (CreateCardForm, {"title": "Card", "project_column_uid": "column"})):
        with pytest.raises(ValidationError):
            cls(**kwargs, priority=value)
    with pytest.raises(ValidationError):
        TypeAdapter(CardChange).validate_python({"action": "priority", "priority": value})


@pytest.mark.parametrize("priority", ["P0", "P1", "P2", "P3"])
def test_creation_roundtrip_and_authorized_read(followup_board, priority):  # noqa: F811 - imported pytest fixture
    b = followup_board
    card, response = b.service.card.create(b.f.user, b.f.project, b.f.column, "Independent", priority=priority)
    assert card.priority == response["priority"] == priority
    assert b.service.card.get_by_id_like(card.id).priority == priority
    assert public_card_summary(response)["priority"] == priority
    with DbSession.atomic() as db:
        card.visibility = "SHARED"
        db.update(card)
    bundle = get_card_bundle(NativeCardWorkspaceAdapter(b.f.user, b.service), b.f.project.get_uid(), card.get_uid(),
                             CommentPage(), SectionPage())
    assert bundle.card.core["priority"] == priority
    default, _ = b.service.card.create(b.f.user, b.f.project, b.f.column, "[P0] title only")
    assert default.priority == "P2"


@pytest.mark.parametrize("rollback", [False, True])
def test_update_preserves_receipts_socket_and_rollback(followup_board, rollback):  # noqa: F811 - imported pytest fixture
    b = followup_board
    seq = b.f.card.last_change_seq
    with pytest.raises(RuntimeError) if rollback else __import__("contextlib").nullcontext():
        with DbSession.atomic():
            assert b.service.card.update(b.f.user, b.f.project, b.f.card, {"priority": "P0"}) == {"priority": "P0"}
            assert not b.observed
            if rollback:
                raise RuntimeError("rollback priority")
    current = b.service.card.get_by_id_like(b.f.card.id)
    if rollback:
        assert current.priority == "P2" and current.last_change_seq == seq and not b.observed
    else:
        assert current.priority == "P0" and current.last_change_seq > seq
        assert any(kind == "socket" and "P0" in str(payload) for kind, _, payload in b.observed)
        assert all(not active for _, active, _ in b.observed)
        assert b.service.card.update(b.f.user, b.f.project, current, {"priority": "P0"}) is True
        assert b.service.card.get_by_id_like(current.id).last_change_seq == current.last_change_seq
        assert b.service.card.update(b.f.user, b.f.project, current, {"title": "[P3] preserve native"})
        assert b.service.card.get_by_id_like(current.id).priority == "P0"
    with pytest.raises(ValueError):
        b.service.card.update(b.f.user, b.f.project, current, {"priority": None})


def test_rest_mcp_current_audience_and_omission(current_card):  # noqa: F811 - imported pytest fixture
    user, project, card, reader = current_card
    update = Mock(return_value=True)
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=reader.resolve_readable_card, update=update))
    request = SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.HumanUI})
    BoardCardApi.change_card_details(project.get_uid(), card.get_uid(), request, ChangeCardDetailsForm(priority="P1"), user, service)
    update.assert_called_with(user, project.get_uid(), card.get_uid(), {"priority": "P1"})
    BoardCardApi.change_card_details(project.get_uid(), card.get_uid(), request, ChangeCardDetailsForm(title="Title"), user, service)
    update.assert_called_with(user, project.get_uid(), card.get_uid(), {"title": "Title"})
    assert CardMcp.change_card_details(project.get_uid(), card.get_uid(), user, service, priority="P3") == {"priority": "P3"}
    with DbSession.atomic() as db:
        card.owner_user_id = user.id + 1
        card.created_by_user_id = card.owner_user_id
        db.update(card)
    with pytest.raises(Exception, match="CARD_UNAVAILABLE"):
        CardMcp.change_card_details(project.get_uid(), card.get_uid(), user, service, priority="P0")
    with pytest.raises(Exception):
        BoardCardApi.change_card_details(project.get_uid(), card.get_uid(), request, ChangeCardDetailsForm(priority="P0"), user, service)
    assert update.call_count == 3


def test_bounded_priority_filter_keyset_and_context(followup_board):  # noqa: F811 - imported pytest fixture
    b = followup_board
    # Keep the normal updated ordering independent, and test priority ties across pages.
    with DbSession.atomic() as db:
        db.insert_all([Card(project_id=b.f.project.id, project_column_id=b.f.column.id, title=f"Card {i}", priority=value,
                            visibility="SHARED", created_by_user_id=b.f.user.id)
                       for i, value in enumerate(["P3", "P0", "P1", "P0", "P2"])])
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=int(b.f.user.id))
    repo = b.f.repo.card
    first = repo.get_page_by_project(b.f.project, 2, context=context, priorities=["P0", "P1"], priority_order=True)
    assert [c.priority for c, _ in first] == ["P0", "P0", "P1"]
    last = first[1][0]
    second = repo.get_page_by_project(b.f.project, 2, last.updated_at, last.id, context=context,
                                    priorities=["P0", "P1"], priority_order=True, before_priority=last.priority)
    assert [c.priority for c, _ in second] == ["P1"]
    assert repo.count_by_project(b.f.project, context=context, priorities=["P0", "P1"]) == 3
    assert repo.count_by_project(b.f.project, context=context, priorities=[]) == 0
    adapter = NativeCardWorkspaceAdapter(b.f.user, b.service)
    page = list_project_cards(adapter, b.f.project.get_uid(), limit=2, priorities=["P0", "P1"], priority_order=True)
    cursor = page.cards.next_cursor
    assert cursor and ProjectCardCursor.decode(cursor).priority == "P0"
    resumed = list_project_cards(adapter, b.f.project.get_uid(), limit=2, cursor=cursor, priorities=["P1", "P0"], priority_order=True)
    assert [c["priority"] for c in resumed.cards.items] == ["P1"]
    with pytest.raises(ValueError, match="context changed"):
        list_project_cards(adapter, b.f.project.get_uid(), cursor=cursor, priorities=["P0"], priority_order=True)


def test_priority_cannot_mutate_linked_or_app_owned_bot_card(followup_board):  # noqa: F811 - imported pytest fixture
    from langboard_shared.domain.models import Bot, CardAppOwnership
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied

    b = followup_board
    with DbSession.atomic() as db:
        linked = Card(project_id=b.f.project.id, project_column_id=b.f.column.id, title="Mirror",
                      source_type="project_card", source_uid=b.f.card.get_uid())
        db.insert(linked)
    assert b.service.card.update(b.f.user, b.f.project, linked, {"priority": "P0"}) is None
    assert b.service.card.get_by_id_like(linked.id).priority == "P2"
    with DbSession.atomic() as db:
        db.insert(CardAppOwnership(card_id=b.f.card.id, app_key="fixture"))
    with pytest.raises(AppGovernanceDenied):
        b.service.card.update(Bot.model_construct(name="Fixture", bot_uname="fixture", app_api_token="fixture"),
                              b.f.project, b.f.card, {"priority": "P0"})
    assert b.service.card.get_by_id_like(b.f.card.id).priority == "P2"
