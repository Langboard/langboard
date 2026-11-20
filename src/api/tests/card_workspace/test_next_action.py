"""Authored next actions retain native persistence, event and audience boundaries."""

import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard.card_workspace.application.work_plan import PlanCard
from langboard.routes.board.forms.Card import ChangeCardDetailsForm, CreateCardForm
from langboard_shared.domain.contracts.CardNextAction import normalize_next_action
from pydantic import ValidationError
from src.api.tests.card_workspace.test_card_change_counts import counts as counts
from src.api.tests.card_workspace.test_card_change_counts import native_delivery as native_delivery
from src.api.tests.card_workspace.test_work_plan_followup_fields import followup_board as followup_board


def test_migration_legacy_clear_and_roundtrip():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261027010000-defa012c3456.py"
    spec = importlib.util.spec_from_file_location("next_action_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE card (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
        db.exec_driver_sql("INSERT INTO card VALUES (1, 'legacy')")
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        assert db.execute(sa.text("SELECT next_action FROM card")).scalar() is None
        db.execute(sa.text("UPDATE card SET next_action=:value"), {"value": "Review 자료"})
        assert db.execute(sa.text("SELECT next_action FROM card")).scalar() == "Review 자료"
        with pytest.raises(RuntimeError, match="Cannot remove"):
            migration.downgrade()
        db.exec_driver_sql("UPDATE card SET next_action=NULL")
        migration.downgrade()
        assert db.execute(sa.text("SELECT title FROM card")).scalar() == "legacy"
        migration.upgrade()
        assert db.execute(sa.text("SELECT next_action FROM card")).scalar() is None


@pytest.mark.parametrize("value", [1, False, [], "x" * 1001, "bad\x00input"])
def test_invalid_input_rejected_at_all_native_boundaries(value):
    with pytest.raises(ValueError):
        normalize_next_action(value)
    for cls, fields in [
        (ChangeCardDetailsForm, {}),
        (CreateCardForm, {"title": "Card", "project_column_uid": "column"}),
        (PlanCard, {"title": "Card", "client_ref": "new:1"}),
    ]:
        with pytest.raises(ValidationError):
            cls(**fields, next_action=value)


def test_omission_and_explicit_clear_remain_distinct():
    assert "next_action" not in ChangeCardDetailsForm(title="new").model_fields_set
    assert "next_action" in ChangeCardDetailsForm(next_action=None).model_fields_set
    assert ChangeCardDetailsForm(next_action="  \n ").next_action is None
    assert ChangeCardDetailsForm(next_action="  Confirm deployment  ").next_action == "Confirm deployment"
    assert "next_action" not in PlanCard(client_ref="new:1", title="Legacy plan").model_dump()


def test_native_work_plan_and_update_roundtrip(followup_board):  # noqa: F811
    b = followup_board
    payload = b.plan.model_dump()
    payload["new_cards"][0]["next_action"] = "Confirm deployment"
    plan = type(b.plan).model_validate(payload)
    preview = b.owner.preview(plan)
    result = b.owner.apply(plan, preview["revision"], "next-action-plan")
    created = result["graph"]["created_cards"][0]
    assert created["next_action"] == "Confirm deployment"
    card = b.service.card.get_by_id_like(created["uid"])
    assert card.next_action == "Confirm deployment"
    seq = card.last_change_seq
    assert b.service.card.update(b.f.user, b.f.project, card, {"title": "Renamed"})
    card = b.service.card.get_by_id_like(card.id)
    assert card.next_action == "Confirm deployment"
    assert b.service.card.update(b.f.user, b.f.project, card, {"next_action": "  Request more 자료  "}) == {
        "next_action": "Request more 자료"
    }
    card = b.service.card.get_by_id_like(card.id)
    assert card.next_action == "Request more 자료" and card.last_change_seq > seq
    seq = card.last_change_seq
    assert b.service.card.update(b.f.user, b.f.project, card, {"next_action": ""}) == {"next_action": None}
    card = b.service.card.get_by_id_like(card.id)
    assert card.next_action is None and card.last_change_seq > seq
    assert b.service.card.update(b.f.user, b.f.project, card, {"next_action": None}) is True
    with pytest.raises(ValueError):
        b.service.card.update(b.f.user, b.f.project, card, {"next_action": "x" * 1001})
    assert b.service.card.get_by_id_like(card.id).next_action is None


@pytest.mark.parametrize("rollback", [False, True])
def test_next_action_uses_existing_commit_owned_socket_and_activity(followup_board, rollback):  # noqa: F811
    from langboard_shared.core.db import DbSession

    b = followup_board
    observed = b.observed
    assert not observed
    before = b.service.card.get_by_id_like(b.f.card.id).last_change_seq
    try:
        with DbSession.atomic():
            assert b.service.card.update(b.f.user, b.f.project, b.f.card, {"next_action": "Request evidence"}) == {
                "next_action": "Request evidence"
            }
            assert observed == []
            if rollback:
                raise RuntimeError("Rollback test")
    except RuntimeError:
        assert rollback
    card = b.service.card.get_by_id_like(b.f.card.id)
    if rollback:
        assert card.next_action is None and card.last_change_seq == before
        assert observed == []
    else:
        assert card.next_action == "Request evidence" and card.last_change_seq > before
        assert observed and all(active is False for _, active, _ in observed)
        assert any(kind == "socket" and "Request evidence" in str(payload) for kind, _, payload in observed)
        assert any("card_updated" in kind for kind, _, _ in observed)


def test_normal_creation_returns_authored_action(followup_board):  # noqa: F811
    from langboard_shared.core.db import EditorContentModel

    b = followup_board
    card, response = b.service.card.create(
        b.f.user,
        b.f.project,
        b.f.column,
        "Authored card",
        EditorContentModel(content="Body"),
        next_action="  Confirm deployment  ",
    )
    assert card.next_action == response["next_action"] == "Confirm deployment"
    assert b.service.card.get_by_id_like(card.id).next_action == "Confirm deployment"
