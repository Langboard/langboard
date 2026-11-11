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


def test_private_card_cannot_change_without_the_reviewed_version(transition_card):
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


@pytest.mark.parametrize("offset_hours", [0, 9, -5])
def test_mcp_sharing_requires_the_reviewed_current_version(transition_card, offset_hours):
    from datetime import timedelta, timezone

    user, project, card, service = transition_card
    assert service.change_visibility(
        user, project, card, CardVisibility.Shared,
        channel=CollaborationChannel.Mcp, confirmed=True, expected_visibility="INTERNAL",
        expected_updated_at=card.updated_at.astimezone(timezone(timedelta(hours=offset_hours))),
    )["visibility"] == "SHARED"


def test_same_visibility_with_a_stale_review_version_cannot_be_shared(transition_card):
    from datetime import timedelta

    user, project, card, service = transition_card
    with pytest.raises(ValueError):
        service.change_visibility(
            user, project, card, CardVisibility.Shared,
            channel=CollaborationChannel.Mcp, confirmed=True, expected_visibility="INTERNAL",
            expected_updated_at=card.updated_at - timedelta(seconds=1),
        )
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(CardVisibilityChange)).all()
    assert not ProjectPublisher.updated.called


@pytest.fixture
def visibility_relationship(transition_card):
    from langboard_shared.domain.models import CardRelationship, GlobalCardRelationshipType

    user, project, card, service = transition_card
    for model in (GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        kind = GlobalCardRelationshipType(parent_name="Reference", child_name="Related", machine_semantic="references")
        peer = Card(project_id=project.id, title="Relationship peer", created_by_user_id=user.id)
        db.insert(kind)
        db.insert(peer)
    return user, project, card, service, peer, kind


@pytest.mark.parametrize("is_parent", [True, False])
@pytest.mark.parametrize("source,target", [("INTERNAL", "PRIVATE"), ("PRIVATE", "INTERNAL"), ("PRIVATE", "SHARED")])
@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Mcp])
def test_incident_relationship_prevents_privacy_scope_change_without_a_hint(visibility_relationship, is_parent, source, target, channel):
    from langboard_shared.domain.models import CardRelationship

    user, project, card, service, peer, kind = visibility_relationship
    with DbSession.use(readonly=False) as db:
        if source == "PRIVATE":
            for item in (card, peer):
                item.visibility = "PRIVATE"
                item.owner_user_id = user.id
                db.update(item)
        parent, child = (card, peer) if is_parent else (peer, card)
        db.insert(CardRelationship(card_id_parent=parent.id, card_id_child=child.id, relationship_type_id=kind.id))
    with pytest.raises(PermissionError, match="^Card visibility transition is not allowed$"):
        service.change_visibility(
            user, project, card, CardVisibility(target), channel=channel, confirmed=True,
            expected_visibility=source, expected_updated_at=card.updated_at,
        )
    with DbSession.use(readonly=False) as db:
        persisted = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        assert persisted.visibility == source
        assert not db.exec(SqlBuilder.select.table(CardVisibilityChange)).all()
    assert not ProjectPublisher.updated.called


def test_unrelated_private_round_trip_keeps_owner_and_review_evidence(visibility_relationship):
    from datetime import timezone
    from langboard_shared.core.types import SafeDateTime

    user, project, card, service, _, _ = visibility_relationship
    first = service.change_visibility(
        user, project, card, CardVisibility.Private, channel=CollaborationChannel.HumanUI, confirmed=True,
        expected_visibility="INTERNAL", expected_updated_at=card.updated_at,
    )
    with DbSession.use(readonly=False) as db:
        private = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        assert private.visibility == "PRIVATE" and private.owner_user_id == user.id
    second = service.change_visibility(
        user, project, card, CardVisibility.Internal, channel=CollaborationChannel.Mcp, confirmed=True,
        expected_visibility="PRIVATE", expected_updated_at=SafeDateTime.fromisoformat(first["updated_at"]),
    )
    assert second["visibility"] == "INTERNAL"
    with DbSession.use(readonly=False) as db:
        audits = db.exec(SqlBuilder.select.table(CardVisibilityChange).order_by(CardVisibilityChange.id)).all()
        persisted = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        assert persisted.owner_user_id is None and persisted.created_by_user_id == user.id
        assert len(audits) == 2 and all(item.reviewed_updated_at is not None for item in audits)
        assert audits[0].reviewed_updated_at.replace(tzinfo=timezone.utc) == card.updated_at
        assert all("reviewed_updated_at" not in item.api_response() for item in audits)


def test_private_noop_retains_relationships_and_returns_a_utc_review_version(visibility_relationship):
    from datetime import datetime
    from langboard_shared.domain.models import CardRelationship

    user, project, card, service, peer, kind = visibility_relationship
    with DbSession.use(readonly=False) as db:
        for item in (card, peer):
            item.visibility = "PRIVATE"
            item.owner_user_id = user.id
            db.update(item)
        edge = CardRelationship(card_id_parent=card.id, card_id_child=peer.id, relationship_type_id=kind.id)
        db.insert(edge)
    result = service.change_visibility(
        user, project, card, CardVisibility.Private, channel=CollaborationChannel.Mcp, confirmed=True,
        expected_visibility="PRIVATE", expected_updated_at=card.updated_at,
    )
    assert datetime.fromisoformat(result["updated_at"]).tzinfo is not None
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(CardRelationship)).first().id == edge.id
        assert not db.exec(SqlBuilder.select.table(CardVisibilityChange)).all()
    assert not ProjectPublisher.updated.called


def test_visibility_aba_rejects_a_previously_reviewed_timestamp(transition_card):
    user, project, card, service = transition_card
    reviewed = card.updated_at
    change(transition_card, CardVisibility.Shared)
    change(transition_card, CardVisibility.Internal, expected="SHARED")
    with pytest.raises(ValueError):
        service.change_visibility(
            user, project, card, CardVisibility.Shared, channel=CollaborationChannel.Mcp, confirmed=True,
            expected_visibility="INTERNAL", expected_updated_at=reviewed,
        )
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(CardVisibilityChange)).all()) == 2
    assert ProjectPublisher.updated.call_count == 2


def test_visibility_uses_the_existing_graph_lock_order(transition_card, monkeypatch):
    from sqlalchemy.dialects import postgresql

    locks = []
    original = DbSession.exec
    def record(self, statement, *args, **kwargs):
        sql = " ".join(str(statement.compile(dialect=postgresql.dialect())).split())
        if sql.endswith(("FOR UPDATE", "FOR NO KEY UPDATE")):
            locks.append(sql)
        return original(self, statement, *args, **kwargs)
    monkeypatch.setattr(DbSession, "exec", record)
    change(transition_card, CardVisibility.Shared)
    assert len(locks) >= 2
    assert "FROM project " in locks[0] and locks[0].endswith("FOR NO KEY UPDATE")
    assert "FROM card " in locks[1] and locks[1].endswith("FOR UPDATE")


@pytest.mark.parametrize("target", ["SHARED", "PRIVATE"])
def test_audit_requires_review_evidence_for_machine_disclosure(transition_card, target):
    from sqlalchemy.exc import IntegrityError

    user, _, card, _ = transition_card
    with pytest.raises(IntegrityError):
        with DbSession.atomic() as db:
            db.insert(CardVisibilityChange(
                card_id=card.id, changed_by_user_id=user.id, channel="mcp",
                previous_visibility="INTERNAL", next_visibility=target,
            ))



@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Mcp])
def test_route_passes_reviewed_private_transition_to_native_owner(transition_card, channel):
    from langboard.routes.board import BoardCardApi
    from langboard.routes.board.forms.Card import ChangeCardVisibilityForm
    from langboard_shared.domain.models import CardRelationship, GlobalCardRelationshipType
    user, project, card, service = transition_card
    for model in (GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.use(readonly=False) as db:
        version = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().updated_at.isoformat()
    form = ChangeCardVisibilityForm(
        visibility="PRIVATE", expected_visibility="INTERNAL", confirmed=True,
        expected_updated_at=version + "Z" if "+" not in version else version,
    )
    BoardCardApi.change_card_visibility(
        project.get_uid(), card.get_uid(), SimpleNamespace(scope={"collaboration_channel": channel}),
        form, user, SimpleNamespace(card=service),
    )
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        audit = db.exec(SqlBuilder.select.table(CardVisibilityChange)).first()
    assert current.visibility == "PRIVATE" and current.owner_user_id == user.id
    assert audit.reviewed_updated_at is not None and audit.channel == channel.value
