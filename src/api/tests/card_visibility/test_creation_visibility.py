from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ProjectAssignedUser, User
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility


def test_personal_board_defaults_private_and_live_membership_changes_default(current_card):
    actor, project, _, service = current_card
    assert service.default_creation_visibility(project, actor) == CardVisibility.Private
    with DbSession.use(readonly=False) as db:
        customer = User(firstname="Customer", lastname="External", email="fixture@example.invalid",
                        password="fixture", activated_at=SafeDateTime.now())
        db.insert(customer)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=customer.id))
    assert service.default_creation_visibility(project, actor) == CardVisibility.Shared
    assert service.default_creation_visibility(project, customer) == CardVisibility.Shared
    with DbSession.use(readonly=False) as db:
        customer.activated_at = None
        db.update(customer)
    assert service.default_creation_visibility(project, actor) == CardVisibility.Private
    assert service.default_creation_visibility(project, object()) == CardVisibility.Internal


def test_nonmember_never_selects_shared_default(current_card):
    actor, project, _, service = current_card
    outsider = User(firstname="Outside", lastname="Fixture", email="outside@example.invalid",
                    password="test-only", activated_at=SafeDateTime.now())
    with DbSession.use(readonly=False) as db:
        db.insert(outsider)
    assert service.default_creation_visibility(project, outsider) == CardVisibility.Internal


def test_internal_member_keeps_internal_default(current_card, monkeypatch):
    actor, project, _, service = current_card
    with DbSession.use(readonly=False) as db:
        member = User(firstname="Internal", lastname="Fixture", email="internal@example.invalid",
                      password="test-only", activated_at=SafeDateTime.now())
        db.insert(member)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=member.id))
    monkeypatch.setattr(service, "_resolve_internal_access", lambda _: True)
    assert service.default_creation_visibility(project, member) == CardVisibility.Internal
    assert service.default_creation_visibility(project, actor) == CardVisibility.Internal


def test_unknown_policy_does_not_publish_customer_card(current_card, monkeypatch):
    actor, project, _, service = current_card
    with DbSession.use(readonly=False) as db:
        member = User(firstname="External", lastname="Fixture", email="unknown-policy@example.invalid",
                      password="test-only", activated_at=SafeDateTime.now())
        db.insert(member)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=member.id))
    from langboard_shared.Env import Env
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "unknown"))
    assert service.default_creation_visibility(project, member) == CardVisibility.Internal
