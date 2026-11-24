"""Persisted transitions must preserve disclosure boundaries and audit evidence."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Card, CardVisibilityChange, ProjectRole
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.domain.services.factory.ProjectService import ProjectService
from langboard_shared.Env import Env
from langboard_shared.publishers import ProjectPublisher


@pytest.fixture
def transition_card(current_card, monkeypatch):
    user, project, card, service = current_card
    CardVisibilityChange.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    with DbSession.use(readonly=False) as db:
        card.visibility = "INTERNAL"
        card.owner_user_id = None
        db.update(card)
        db.insert(ProjectRole(project_id=project.id, user_id=user.id, actions=["card_update"]))
    project_service = SimpleNamespace(get_user_role_actions_by_project=lambda *_: ["card_update"])
    service._get_service = lambda kind: project_service if kind is ProjectService else None
    monkeypatch.setattr(ProjectPublisher, "updated", Mock())
    return user, project, card, service


def change(fixture, target, *, channel=CollaborationChannel.HumanUI, confirmed=True, expected="INTERNAL"):
    user, project, card, service = fixture
    return service.change_visibility(user, project, card, target, channel=channel,
                                     confirmed=confirmed, expected_visibility=expected)


def test_both_directions_are_persisted_and_audited(transition_card):
    user, project, card, _ = transition_card
    assert change(transition_card, CardVisibility.Shared)["visibility"] == "SHARED"
    assert change(transition_card, CardVisibility.Internal, expected="SHARED")["visibility"] == "INTERNAL"
    with DbSession.use(readonly=False) as db:
        persisted = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        audits = db.exec(SqlBuilder.select.table(CardVisibilityChange)).all()
    assert persisted.title == card.title
    assert persisted.visibility == "INTERNAL"
    assert [(entry.previous_visibility, entry.next_visibility) for entry in audits] == [("INTERNAL", "SHARED"), ("SHARED", "INTERNAL")]
    assert all(entry.changed_by_user_id == user.id for entry in audits)
    assert ProjectPublisher.updated.call_count == 2
    assert ProjectPublisher.updated.call_args.args[1] == {"card_visibility_changed": True}


@pytest.mark.parametrize("channel,confirmed", [(CollaborationChannel.Mcp, True), (CollaborationChannel.Api, True), (CollaborationChannel.HumanUI, False)])
def test_nonhuman_or_unconfirmed_sharing_is_denied(transition_card, channel, confirmed):
    with pytest.raises(PermissionError):
        change(transition_card, CardVisibility.Shared, channel=channel, confirmed=confirmed)
    assert not ProjectPublisher.updated.called


def test_stale_confirmation_does_not_reverse_another_change(transition_card):
    change(transition_card, CardVisibility.Shared)
    with pytest.raises(ValueError):
        change(transition_card, CardVisibility.Internal)
    assert ProjectPublisher.updated.call_count == 1


@pytest.mark.parametrize("channel", [CollaborationChannel.Mcp, CollaborationChannel.Api])
def test_authorized_automation_can_reduce_visibility(transition_card, channel):
    change(transition_card, CardVisibility.Shared)
    assert change(transition_card, CardVisibility.Internal, channel=channel, expected="SHARED")["visibility"] == "INTERNAL"


def test_private_card_is_immutable(transition_card):
    user, _, card, _ = transition_card
    with DbSession.use(readonly=False) as db:
        card.visibility = "PRIVATE"
        card.owner_user_id = user.id
        db.update(card)
    with pytest.raises(PermissionError):
        change(transition_card, CardVisibility.Shared, expected="PRIVATE")


def test_revoked_update_permission_is_denied(transition_card):
    with DbSession.use(readonly=False) as db:
        role = db.exec(SqlBuilder.select.table(ProjectRole)).first()
        role.actions = ["read"]
        db.update(role)
    with pytest.raises(PermissionError):
        change(transition_card, CardVisibility.Shared)


def test_route_maps_forbidden_and_stale_transition_errors(transition_card):
    from langboard.routes.board import BoardCardApi
    from langboard.routes.board.forms.Card import ChangeCardVisibilityForm
    from langboard_shared.core.routing import ApiException
    user, project, card, service = transition_card
    host = SimpleNamespace(card=service)
    form = ChangeCardVisibilityForm(visibility="SHARED", expected_visibility="INTERNAL", confirmed=True)
    request = SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.Mcp})
    with pytest.raises(ApiException.Forbidden_403):
        BoardCardApi.change_card_visibility(project.get_uid(), card.get_uid(), request, form, user, host)
    request.scope["collaboration_channel"] = CollaborationChannel.HumanUI
    BoardCardApi.change_card_visibility(project.get_uid(), card.get_uid(), request, form, user, host)
    stale = ChangeCardVisibilityForm(visibility="INTERNAL", expected_visibility="INTERNAL", confirmed=True)
    with pytest.raises(ApiException.Conflict_409):
        BoardCardApi.change_card_visibility(project.get_uid(), card.get_uid(), request, stale, user, host)


def test_external_member_visibility_follows_transition(transition_card):
    from langboard_shared.domain.models import ProjectAssignedUser, User
    user, project, card, service = transition_card
    with DbSession.use(readonly=False) as db:
        external = User(firstname="External", lastname="Fixture", email="external@example.invalid", password="test-only", activated_at=user.activated_at)
        db.insert(external)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=external.id))
    original = service._resolve_internal_access
    service._resolve_internal_access = lambda actor: actor.id == user.id
    assert service.resolve_readable_card(project, card, external, CollaborationChannel.HumanUI) is None
    change(transition_card, CardVisibility.Shared)
    assert service.resolve_readable_card(project, card, external, CollaborationChannel.HumanUI) is not None
    change(transition_card, CardVisibility.Internal, expected="SHARED")
    assert service.resolve_readable_card(project, card, external, CollaborationChannel.HumanUI) is None
    service._resolve_internal_access = original
