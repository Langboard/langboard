"""Deployment compatibility must preserve board ACLs and personal vaults."""

import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import IdentityProvider, ProjectAssignedUser, User, UserIdentityLink
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility, CollaborationChannel
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.UserIdentityLinkRepository import UserIdentityLinkRepository
from sqlalchemy import event


@pytest.mark.parametrize("mode,allowed", [("project_members", True), ("scim", False), ("invalid", False)])
def test_internal_access_requires_explicit_policy_and_current_membership(current_card, monkeypatch, mode, allowed):
    user, project, card, service = current_card
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: mode))
    _, context = service.resolve_visibility_context(project, user, CollaborationChannel.HumanUI)
    assert context.can_read_card(CardVisibility.Internal) is allowed
    assert context.can_read_card(CardVisibility.Private, owner_user_id=int(user.id))
    assert not context.can_read_card(CardVisibility.Private, owner_user_id=int(user.id) + 1)
    # An explicit compatibility mode never turns revoked accounts into readers.
    with DbSession.use(readonly=False) as db:
        user.activated_at = None
        db.update(user)
    assert service.resolve_visibility_context(project, user, CollaborationChannel.HumanUI) is None


@pytest.mark.parametrize("issuer,subject,allowed", [
    ("https://internal.example/", "staff-1", True),
    ("https://customer.example", "customer-1", False),
    ("https://internal.example", "", False),
    (None, None, False),
])
def test_selected_identity_authority_separates_customer_without_email_inference(current_card, monkeypatch, issuer, subject, allowed):
    user, project, card, service = current_card
    UserIdentityLink.__table__.create(DbEngine.get_main_engine())
    service.repo.user_identity_link = UserIdentityLinkRepository(None, None)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "oidc_issuers"))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_OIDC_ISSUERS", property(lambda _: ["https://internal.example"]))
    if issuer is not None:
        with DbSession.use(readonly=False) as db:
            db.insert(UserIdentityLink(user_id=user.id, provider=IdentityProvider.Oidc, issuer=issuer, external_id=subject))
    service._raw_get_service = lambda _: type("ScimFixture", (), {"_employee_policy": lambda _: None})()
    _, context = service.resolve_visibility_context(project, user, CollaborationChannel.HumanUI)
    assert context.can_read_card(CardVisibility.Internal) is allowed
    assert context.can_read_card(CardVisibility.Shared)
    assert not context.can_read_card(CardVisibility.Private, owner_user_id=int(user.id) + 1)


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("membership", ["owner", "assigned", "outsider"])
def test_visibility_resolution_uses_one_current_primary_read(current_card, monkeypatch, membership):
    user, project, _, service = current_card
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("Visibility must read the primary"))
    if membership != "owner":
        with DbSession.use(readonly=False) as db:
            owner = User(firstname="Other", lastname="Owner", email="other-owner@example.invalid",
                         password="test-only", activated_at=user.activated_at)
            db.insert(owner)
            project.owner_id = owner.id
            db.update(project)
            if membership == "assigned":
                db.insert(ProjectAssignedUser(project_id=project.id, user_id=user.id))
    queries = []
    engine = DbEngine.get_main_engine()

    def capture(_conn, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = service.resolve_visibility_context(project, user, CollaborationChannel.HumanUI)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result is not None
    assert result[0].id == project.id
    assert result[1].actor_user_id == int(user.id)
    assert result[1].project_member is (membership != "outsider")
    assert result[1].can_read_card(CardVisibility.Internal) is (membership != "outsider")
    assert len(queries) == 1, queries
