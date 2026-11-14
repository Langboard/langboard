"""Actual current native actor resolution, source isolation and authenticated HTTP."""

import os
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models as m
from langboard_shared.domain.models.UserIdentityLink import IdentityProvider
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.DecisionActors import DecisionActors
from langboard_shared.domain.services.DecisionFlow import DecisionDenied, DecisionMissing
from langboard_shared.Env import Env
from sqlalchemy import create_engine, delete, event, select, text, update


@pytest.fixture(params=["sqlite", "postgresql"])
def native(request, tmp_path, monkeypatch):
    admin = None
    schema = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_ACTORS_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires curator-allocated disposable PostgreSQL17")
        admin = create_engine(url, connect_args={"client_encoding": "utf8"})
        schema = "decision_actors_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"client_encoding": "utf8", "options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'actors.sqlite'}")

        @event.listens_for(engine, "connect")
        def foreign_keys(db, _):
            db.execute("PRAGMA foreign_keys=ON")

    required = {
        model.__table__
        for model in (
            m.User,
            m.Organization,
            m.SettingRole,
            m.ApiKeyRole,
            m.McpRole,
            m.Project,
            m.ProjectRole,
            m.ProjectAssignedUser,
            m.ProjectInvitation,
            m.Card,
            m.ProjectColumn,
            m.ScimGroup,
            m.ScimGroupMember,
            m.BoardTeam,
            m.BoardTeamMember,
            m.EmployeeMembershipPolicy,
            m.UserIdentityLink,
        )
    }
    pending = list(required)
    while pending:
        for foreign in pending.pop().foreign_keys:
            if foreign.column.table not in required:
                required.add(foreign.column.table)
                pending.append(foreign.column.table)
    m.User.metadata.create_all(engine, tables=list(required))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    with DbSession.atomic() as db:
        users = []
        for i in range(7):
            user = m.User(
                firstname=f"Person{i}",
                lastname="Example",
                username=f"person-{i}",
                email=f"person{i}@example.invalid",
                password="$2b$12$" + "x" * 53,
                is_admin=i == 0,
                activated_at=None if i == 4 else SafeDateTime.now(),
            )
            db.insert(user)
            users.append(user)
        org = m.Organization(name="Native", slug="native", owner_user_id=users[0].id)
        db.insert(org)
        board = m.Project(title="Actors", owner_id=users[0].id, organization_id=org.id)
        db.insert(board)
        foreign = m.Project(title="Foreign", owner_id=users[0].id, organization_id=org.id)
        db.insert(foreign)
        column = m.ProjectColumn(project_id=board.id, name="Doing", order=0)
        db.insert(column)
        card = m.Card(
            project_id=board.id,
            project_column_id=column.id,
            title="Visible",
            visibility="SHARED",
        )
        db.insert(card)
        for i in (1, 2, 4, 5):
            db.insert(m.ProjectAssignedUser(project_id=board.id, user_id=users[i].id))
        for i in (0, 1, 2, 3, 4):
            db.insert(
                m.ProjectRole(
                    project_id=board.id, user_id=users[i].id, actions=["read", "card_update"] if i == 0 else ["read"]
                )
            )
        db.insert(m.SettingRole(user_id=users[0].id, actions=["group_read"]))
        group = m.ScimGroup(source="local", display_name="Directory")
        db.insert(group)
        for user in users[1:]:
            db.insert(m.ScimGroupMember(group_id=group.id, user_id=user.id))
        second = m.ScimGroup(source="local", display_name="Overlap")
        db.insert(second)
        db.insert(m.ScimGroupMember(group_id=second.id, user_id=users[1].id))
        team = m.BoardTeam(project_id=board.id, name="Review")
        db.insert(team)
        for i in (1, 2, 5):
            db.insert(m.BoardTeamMember(team_id=team.id, user_id=users[i].id))
        other = m.BoardTeam(project_id=foreign.id, name="Private foreign team")
        db.insert(other)
    service = DomainService()
    yield engine, DecisionActors(service), users, board, foreign, card, group, second, team, other, org
    service.close()
    engine.dispose()
    if admin is not None:
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def preview(f, sources=None, *, actor=0, kind="proposal", channel=CollaborationChannel.Api):
    return f[1].preview(
        f[2][actor],
        f[3].get_uid(),
        f[5].get_uid(),
        sources or [{"kind": "group", "uid": f[6].get_uid()}],
        source_kind=kind,
        channel=channel,
    )


def grants(f):
    with f[0].connect() as db:
        return {
            model.__name__: db.execute(select(model.__table__).order_by(model.id)).all()
            for model in (
                m.ProjectAssignedUser,
                m.ProjectRole,
                m.ProjectInvitation,
                m.BoardTeam,
                m.BoardTeamMember,
                m.ScimGroup,
                m.ScimGroupMember,
            )
        }


@pytest.mark.parametrize("kind", ["proposal", "poll"])
def test_overlap_provenance_active_board_read_intersection_no_grants(native, kind):
    f = native
    before = grants(f)
    sources = [
        {"kind": "group", "uid": f[6].get_uid()},
        {"kind": "group", "uid": f[7].get_uid()},
        {"kind": "team", "uid": f[8].get_uid()},
        {"kind": "user", "uid": f[2][1].get_uid()},
    ]
    result = preview(f, sources, kind=kind)
    assert result["actor_uids"] == [f[2][1].get_uid(), f[2][2].get_uid()]
    assert result["eligible_count"] == 2 and result["excluded_count"] == 4
    assert len(result["actors"][0]["sources"]) == 4
    assert result["source_kind"] == kind and result["authorization"] == f"{kind}_response_candidate"
    assert result["execution_authority"] is False
    assert result == preview(f, list(reversed(sources)), kind=kind)
    groups = f[1].candidates(f[2][0], f[3].get_uid(), f[5].get_uid(), source_kind=kind, kind="group", query="Direct")
    assert groups["records"] == [{"kind": "group", "uid": f[6].get_uid(), "label": "Directory"}]
    teams = f[1].candidates(f[2][0], f[3].get_uid(), f[5].get_uid(), source_kind=kind, kind="team")
    assert teams["records"] == [{"kind": "team", "uid": f[8].get_uid(), "label": "Review"}]
    assert grants(f) == before
    # Ineligible directory identities never appear in public preview data.
    for user in f[2][3:]:
        assert user.get_uid() not in str(result) and user.firstname not in str(result)
    assert result["sources"][0]["exclusions"]["not_currently_eligible"] >= 0


def test_current_group_authority_foreign_team_and_creator_card_privacy(native):
    f = native
    with f[0].begin() as db:
        db.execute(update(m.SettingRole).values(actions=[]))
    for group_uid in (f[6].get_uid(), f[9].get_uid()):
        with pytest.raises(DecisionDenied):
            preview(f, [{"kind": "group", "uid": group_uid}])
    with pytest.raises(DecisionDenied):
        preview(f, [{"kind": "team", "uid": f[9].get_uid()}])
    # Read-only board users cannot enumerate responder candidates or source labels.
    with pytest.raises(DecisionDenied):
        f[1].candidates(f[2][1], f[3].get_uid(), f[5].get_uid(), source_kind="proposal", kind="team")
    with f[0].begin() as db:
        db.execute(update(m.Card).values(visibility="PRIVATE", owner_user_id=f[2][1].id, created_by_user_id=f[2][1].id))
    with pytest.raises(DecisionMissing):
        preview(f, [{"kind": "team", "uid": f[8].get_uid()}])


@pytest.mark.parametrize("kind", ["proposal", "poll"])
def test_candidates_card_specific_private_internal_and_partial_email(native, monkeypatch, kind):
    f = native

    def candidates():
        return f[1].candidates(
            f[2][0],
            f[3].get_uid(),
            f[5].get_uid(),
            source_kind=kind,
            kind="user",
            query="person",
            channel=CollaborationChannel.HumanUI,
        )

    assert {record["uid"] for record in candidates()["records"]} == {f[2][i].get_uid() for i in (0, 1, 2)}
    email = f[1].candidates(f[2][0], f[3].get_uid(), f[5].get_uid(), source_kind=kind, kind="user", query="person1@")
    assert email["records"] == [{"kind": "user", "uid": f[2][1].get_uid(), "label": "Person1 Example"}]
    with f[0].begin() as db:
        db.execute(update(m.Card).values(visibility="PRIVATE", owner_user_id=f[2][0].id, created_by_user_id=f[2][0].id))
    assert [record["uid"] for record in candidates()["records"]] == [f[2][0].get_uid()]
    private = preview(f, [{"kind": "team", "uid": f[8].get_uid()}], kind=kind, channel=CollaborationChannel.HumanUI)
    assert private["eligible_count"] == 0
    with f[0].begin() as db:
        db.execute(update(m.Card).values(visibility="INTERNAL", owner_user_id=None))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "oidc_issuers"))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_OIDC_ISSUERS", property(lambda _: ["https://issuer.invalid"]))
    with DbSession.atomic() as db:
        db.insert(
            m.UserIdentityLink(
                user_id=f[2][0].id,
                provider=IdentityProvider.Oidc,
                issuer="https://issuer.invalid",
                external_id="creator",
            )
        )
    assert [record["uid"] for record in candidates()["records"]] == [f[2][0].get_uid()]


def test_digest_revalidation_source_membership_policy_and_tenant(native):
    f = native
    first = preview(f)
    with f[0].begin() as db:
        db.execute(
            delete(m.ScimGroupMember).where(
                m.ScimGroupMember.group_id == f[6].id, m.ScimGroupMember.user_id == f[2][2].id
            )
        )
    second = preview(f)
    assert second["membership_revision"] != first["membership_revision"] and second["snapshot"] != first["snapshot"]
    with f[0].begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == f[2][1].id).values(actions=[]))
    third = preview(f)
    assert third["policy_revision"] != second["policy_revision"] and third["eligible_count"] == 0
    with f[0].begin() as db:
        db.execute(update(m.Organization).values(suspended_at=SafeDateTime.now()))
    with pytest.raises(DecisionDenied):
        preview(f)


def test_supplied_atomic_transaction_and_bounded_sources(native):
    f = native
    with pytest.raises(RuntimeError):
        f[1].resolve_in_transaction(
            None,
            f[2][0],
            f[3].get_uid(),
            f[5].get_uid(),
            [{"kind": "team", "uid": f[8].get_uid()}],
            source_kind="proposal",
        )
    with DbSession.atomic(sensitive=True) as db:
        f[0].dialect.name == "sqlite" and db.exec(text("BEGIN IMMEDIATE"))
        db.exec(delete(m.ProjectRole).where(m.ProjectRole.user_id == f[2][2].id))
        result = f[1].resolve_in_transaction(
            db,
            f[2][0],
            f[3].get_uid(),
            f[5].get_uid(),
            [{"kind": "team", "uid": f[8].get_uid()}],
            source_kind="proposal",
        )
        assert result["actor_uids"] == [f[2][1].get_uid()]
    for sources in ([{"kind": "team", "uid": f[8].get_uid()}] * 65, [{"kind": "hitl", "uid": f[8].get_uid()}]):
        with pytest.raises(ValueError):
            preview(f, sources)
    with pytest.raises(ValueError):
        preview(f, kind="hitl")
    # Actual DB source expansion bound; no full directory expansion into memory.
    with DbSession.atomic() as db:
        for i in range(257):
            user = m.User(
                firstname="Bound",
                lastname=str(i),
                email=f"bound{i}@example.invalid",
                password="$2b$12$" + "x" * 53,
                activated_at=SafeDateTime.now(),
            )
            db.insert(user)
            db.insert(m.ScimGroupMember(group_id=f[7].id, user_id=user.id))
    with pytest.raises(ValueError):
        preview(f, [{"kind": "group", "uid": f[7].get_uid()}])


def test_native_authenticated_http_source_specific_contract(native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardDecisionActorsApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity

    f = native
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "decision-actors-http-key-minimum-32-bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(f[2][0].id)
    headers = {"Authorization": f"Bearer {access}"}
    base = f"/board/{f[3].get_uid()}/card/{f[5].get_uid()}/decision-actors"
    with TestClient(app) as client:
        assert client.get(base + "/candidates", params={"kind": "user", "source_kind": "proposal"}).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        for kind in ("proposal", "poll"):
            response = client.get(
                base + "/candidates", headers=headers, params={"kind": "user", "source_kind": kind, "query": "person1@"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["authorization"] == f"{kind}_response_candidate"
            assert response.headers["cache-control"] == "no-store"
            result = client.post(
                base + "/preview",
                headers=headers,
                json={"source_kind": kind, "sources": [{"kind": "team", "uid": f[8].get_uid()}]},
            )
            assert result.status_code == 200, result.text
            assert result.json()["authorization"] == f"{kind}_response_candidate"
        assert (
            client.post(
                base + "/preview",
                headers=headers,
                json={"source_kind": "hitl", "sources": [{"kind": "user", "uid": f[2][1].get_uid()}]},
            ).status_code
            == 400
        )
        assert (
            client.post(
                base + "/preview",
                headers=headers,
                json={"source_kind": "proposal", "sources": [{"kind": "team", "uid": f[9].get_uid()}]},
            ).status_code
            == 403
        )


def test_read_snapshot_and_current_principal_team_revalidation(native):
    f = native
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(f[0], "before_cursor_execute", capture)
    try:
        first = preview(f, [{"kind": "team", "uid": f[8].get_uid()}])
    finally:
        event.remove(f[0], "before_cursor_execute", capture)
    expected = "REPEATABLE READ READ ONLY" if f[0].dialect.name == "postgresql" else "BEGIN"
    assert any(expected in sql for sql in statements)
    assert not any("LOCK TABLE" in sql or "BEGIN IMMEDIATE" in sql for sql in statements)
    with f[0].begin() as db:
        db.execute(
            delete(m.ProjectAssignedUser).where(
                m.ProjectAssignedUser.project_id == f[3].id, m.ProjectAssignedUser.user_id == f[2][1].id
            )
        )
    second = preview(f, [{"kind": "team", "uid": f[8].get_uid()}])
    assert first["membership_revision"] != second["membership_revision"]
    assert second["actor_uids"] == [f[2][2].get_uid()]
    # The fixture principal is stale; server must reread its current account.
    with f[0].begin() as db:
        db.execute(update(m.User).where(m.User.id == f[2][0].id).values(activated_at=None))
    with pytest.raises(DecisionDenied):
        preview(f, [{"kind": "team", "uid": f[8].get_uid()}])
