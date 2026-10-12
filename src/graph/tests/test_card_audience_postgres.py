"""Current directory/membership facts on primary PostgreSQL, without auth-cache grants."""

from langboard_shared.core.db import DbSession
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    EmployeeMembershipPolicy,
    IdentityProvider,
    ProjectAssignedUser,
    ScimGroup,
    ScimGroupMember,
    UserIdentityLink,
)
from langboard_shared.domain.services.CardAudience import resolve_card_audience
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.Env import Env
from sqlalchemy.orm import Session
from test_app_governance_postgres import governance  # noqa: F401


def test_primary_card_audience_scim_oidc_and_current_membership(governance, monkeypatch):  # noqa: F811
    engine, (_, owner, outsider), _, project, _, _ = governance
    for model in (EmployeeMembershipPolicy, UserIdentityLink, ScimGroup, ScimGroupMember):
        model.__table__.create(engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: "https://identity.example"))
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: []))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_OIDC_ISSUERS", property(lambda _: ["https://oidc.example"]))

    def audience(actor=owner):
        with DbSession.atomic() as db:
            return resolve_card_audience(db, project.id, actor.id)

    unknown = audience()
    assert unknown.can_read_card(CardVisibility.Shared)
    assert not unknown.can_read_card(CardVisibility.Internal)
    assert not unknown.can_read_card(CardVisibility.Private, owner_user_id=int(owner.id))
    assert not unknown.can_use_whisper
    with Session(engine) as db:
        db.add(ScimGroup(id=301, display_name="Employees", external_id="staff"))
        db.add(
            UserIdentityLink(
                id=302,
                user_id=owner.id,
                provider=IdentityProvider.Scim,
                external_id="owner",
                issuer="https://identity.example/",
            )
        )
        db.add(
            UserIdentityLink(
                id=303,
                user_id=outsider.id,
                provider=IdentityProvider.Oidc,
                external_id="external",
                issuer="https://oidc.example",
            )
        )
        db.add(
            EmployeeMembershipPolicy(
                id=304, key="global", issuer="https://identity.example", group_uids=[SnowflakeID(301).to_short_code()]
            )
        )
        db.commit()
        db.add(ScimGroupMember(id=305, group_id=301, user_id=owner.id))
        db.add(ProjectAssignedUser(id=306, project_id=project.id, user_id=outsider.id))
        db.commit()
    internal = audience()
    assert internal.can_read_card(CardVisibility.Internal)
    assert not internal.can_read_card(CardVisibility.Private, owner_user_id=int(owner.id))
    assert not internal.can_use_whisper
    # OIDC fallback must not override a present operator-selected SCIM policy.
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "oidc_issuers"))
    assert not audience(outsider).can_read_card(CardVisibility.Internal)
    with Session(engine) as db:
        db.query(EmployeeMembershipPolicy).delete()
        db.commit()
    assert audience(outsider).can_read_card(CardVisibility.Internal)
    with Session(engine) as db:
        db.query(ProjectAssignedUser).delete()
        db.commit()
    assert not audience(outsider).can_read_card(CardVisibility.Shared)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: ["staff"]))
    assert audience().can_read_card(CardVisibility.Internal)
    with Session(engine) as db:
        db.query(ScimGroupMember).delete()
        db.commit()
    assert not audience().can_read_card(CardVisibility.Internal)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    assert audience().can_read_card(CardVisibility.Internal)
    with DbSession.atomic() as db:
        human = resolve_card_audience(db, project.id, owner.id, channel=CollaborationChannel.HumanUI)
        assert human.can_read_card(CardVisibility.Private, owner_user_id=int(owner.id)) and human.can_use_whisper
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "invalid"))
    assert not audience().can_read_card(CardVisibility.Internal)


def test_employee_membership_policy_migration_roundtrip(governance):  # noqa: F811
    import importlib.util
    from pathlib import Path
    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError

    engine = governance[0]
    path = Path(__file__).parents[2] / "api/langboard/migrations/versions/20261013020000-b6c7d8e9f0a1.py"
    spec = importlib.util.spec_from_file_location("employee_membership_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "a5b6c7d8e9f0"
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
    with Session(engine) as db:
        db.add(EmployeeMembershipPolicy(id=401, key="global", issuer="https://identity.example", group_uids=["group"]))
        db.commit()
    with Session(engine) as db:
        retained = db.get(EmployeeMembershipPolicy, 401)
        assert retained.group_uids == ["group"] and retained.created_at is not None
        db.add(EmployeeMembershipPolicy(id=402, key="global", issuer="https://other.example", group_uids=[]))
        with pytest.raises(IntegrityError):
            db.commit()
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
    assert "employee_membership_policy" not in inspect(engine).get_table_names()
