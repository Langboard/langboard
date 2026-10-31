"""Native MCP composed-write rollback and independent follow-up field proof."""

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.mcp_tools.CardMcp import change_card_details, set_card_people_and_labels
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import (
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardChangeReceipt,
    EmployeeMembershipPolicy,
    ProjectLabel,
    ScimGroup,
    ScimGroupMember,
    UserIdentityLink,
)
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.ProjectLabelService import ProjectLabelService
from langboard_shared.domain.services.factory.ProjectService import ProjectService
from sqlalchemy import func, select
from src.api.tests.card_workspace.test_card_change_counts import counts  # noqa: F401


@pytest.fixture
def native_followup(counts, monkeypatch):  # noqa: F811
    f = counts
    for model in (EmployeeMembershipPolicy, ScimGroup, ScimGroupMember, UserIdentityLink):
        model.__table__.create(f.engine, checkfirst=True)
    module = import_module(CardService.__module__)
    effects = []
    for publisher, names in (
        (module.CardPublisher, ("assigned_users_updated", "labels_updated", "updated")),
        (module.CardActivityTask, ("card_assigned_users_updated", "card_labels_updated", "card_updated")),
        (module.CardBotTask, ("card_labels_updated", "card_updated")),
    ):
        for name in names:
            effect = Mock()
            monkeypatch.setattr(publisher, name, effect)
            effects.append(effect)
    # Native notification code suppresses self-assignment; all users are disposable fixtures.
    with DbSession.atomic() as db:
        label = ProjectLabel(project_id=f.project.id, name="Follow-up", color="blue", description="")
        db.insert(label)
    service = SimpleNamespace(card=f.cards, project=f.get(ProjectService), project_label=f.get(ProjectLabelService))
    return SimpleNamespace(f=f, label=label, service=service, effects=effects)


def persisted_counts(f):
    with f.engine.connect() as db:
        return [
            db.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (CardAssignedUser, CardAssignedProjectLabel, CardChangeReceipt)
        ]


@pytest.mark.parametrize("failure", ["rejected", "after_label_write"])
def test_second_mutation_failure_rolls_back_members_receipts_and_effects(native_followup, monkeypatch, failure):
    n = native_followup
    original = n.f.cards.update_labels

    def fail(*args, **kwargs):
        if failure == "after_label_write":
            assert original(*args, **kwargs) is True
            raise RuntimeError("Injected second-write failure")
        return False

    monkeypatch.setattr(n.f.cards, "update_labels", fail)
    with pytest.raises(RuntimeError, match="second-write failure|label replacement failed"):
        set_card_people_and_labels(
            n.f.project.get_uid(), n.f.card.get_uid(), n.f.user, n.service,
            assign_user_uids=[n.f.user.get_uid()], label_uids=[n.label.get_uid()],
        )
    assert (persisted_counts(n.f), [effect.call_count for effect in n.effects]) == ([0, 0, 0], [0] * len(n.effects))


def test_valid_member_label_write_then_separate_deadline_remain_supported(native_followup):
    n = native_followup
    result = set_card_people_and_labels(
        n.f.project.get_uid(), n.f.card.get_uid(), n.f.user, n.service,
        assign_user_uids=[n.f.user.get_uid()], label_uids=[n.label.get_uid()],
    )
    assert result["member_uids"] == [n.f.user.get_uid()]
    assert result["labels"][0]["uid"] == n.label.get_uid()
    assert persisted_counts(n.f) == [1, 1, 2]
    assert any(effect.called for effect in n.effects)
    deadline = "2026-10-23T14:59:00+00:00"
    change_card_details(n.f.project.get_uid(), n.f.card.get_uid(), n.f.user, n.service, deadline_at=deadline)
    with n.f.engine.connect() as db:
        actual = db.execute(select(Card.deadline_at).where(Card.id == n.f.card.id)).scalar_one()
    assert actual is not None
    if actual.tzinfo is None:
        actual = actual.replace(tzinfo=timezone.utc)
    assert actual == datetime.fromisoformat(deadline)
