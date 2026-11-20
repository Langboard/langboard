"""Actual SQLite/PostgreSQL Team persistence and native authenticated HTTP proof."""

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models as m
from langboard_shared.domain.services.BoardTeams import BoardTeams, TeamConflict, TeamDenied
from langboard_shared.Env import Env
from sqlalchemy import create_engine, delete, event, inspect, select, text, update
from sqlalchemy.exc import IntegrityError


@pytest.fixture(params=["sqlite", "postgresql"])
def native(request, tmp_path, monkeypatch):
    admin = None
    schema = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_TEAM_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires explicitly disposable allocated PostgreSQL17")
        admin = create_engine(url, connect_args={"client_encoding": "utf8"})
        schema = "board_team_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"client_encoding": "utf8", "options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'team.sqlite'}", connect_args={"timeout": 10})

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
            m.ProjectAssignedUser,
            m.ProjectRole,
            m.ProjectInvitation,
            m.ScimGroup,
            m.ScimGroupMember,
            m.BoardTeam,
            m.BoardTeamMember,
            m.BoardTeamCommand,
            m.Card,
            m.DecisionCase,
            m.DecisionOption,
            m.DecisionResponse,
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
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    with DbSession.atomic() as db:
        users = []
        for i in range(6):
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
        projects = []
        for i in range(2):
            project = m.Project(title=f"Board{i}", owner_id=users[1].id, organization_id=org.id)
            db.insert(project)
            projects.append(project)
        # Deliberately no owner assignment: native owner authority must remain valid.
        for user in (users[2], users[3], users[4]):
            db.insert(m.ProjectAssignedUser(project_id=projects[0].id, user_id=user.id))
        db.insert(m.ProjectRole(project_id=projects[0].id, user_id=users[2].id, actions=["read", "update"]))
        db.insert(m.ProjectRole(project_id=projects[0].id, user_id=users[3].id, actions=["read"]))
        db.insert(m.SettingRole(user_id=users[0].id, actions=["group_read"]))
        db.insert(m.ProjectInvitation(project_id=projects[0].id, email=users[5].email, token="pending"))
        group = m.ScimGroup(source="local", display_name="Native group")
        db.insert(group)
        for user in users[1:]:
            db.insert(m.ScimGroupMember(group_id=group.id, user_id=user.id))
    service = BoardTeams()
    yield engine, service, users, projects, group, org
    engine.dispose()
    if admin is not None:
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def command(f, action, *, actor=1, board=0, request_id=None, **kwargs):
    return f[1].mutate(f[2][actor].id, f[3][board].get_uid(), action, request_id=request_id or str(uuid4()), **kwargs)


def grants(f):
    with f[0].connect() as db:
        return {
            model.__name__: db.execute(select(model.__table__).order_by(model.id)).all()
            for model in (m.ProjectAssignedUser, m.ProjectRole, m.ProjectInvitation)
        }


def test_crud_no_grants_name_locality_cross_board_and_retained_replay(native):
    f = native
    before = grants(f)
    request_id = str(uuid4())
    created = command(f, "create", name="Engineering", request_id=request_id)
    uid = created["team_uid"]
    assert command(f, "create", name="Engineering", request_id=request_id)["replayed"]
    other = command(f, "create", name="Engineering", board=1)
    assert other["team_uid"] != uid
    with pytest.raises(TeamConflict):
        command(f, "create", name="Engineering")
    with pytest.raises(TeamDenied):
        f[1].get(f[2][1].id, f[3][1].get_uid(), uid)
    changed = command(
        f, "add_members", team_uid=uid, expected_revision=1, user_uids=[f[2][1].get_uid(), f[2][2].get_uid()]
    )
    assert changed["revision"] == 2 and changed["changed_count"] == 2
    assert set(f[1].get(f[2][3].id, f[3][0].get_uid(), uid)["team"]["member_uids"]) == {
        f[2][1].get_uid(),
        f[2][2].get_uid(),
    }
    with pytest.raises(TeamDenied):
        command(f, "rename", actor=3, team_uid=uid, expected_revision=2, name="Denied")
    with pytest.raises(TeamConflict):
        command(f, "rename", team_uid=uid, expected_revision=1, name="Stale")
    command(f, "rename", actor=2, team_uid=uid, expected_revision=2, name="Renamed")
    removed = command(f, "remove_members", team_uid=uid, expected_revision=3, user_uids=[f[2][2].get_uid()])
    delete_id = str(uuid4())
    deleted = command(f, "delete", team_uid=uid, expected_revision=removed["revision"], request_id=delete_id)
    assert deleted["deleted"] and deleted["decision_impact"]["status"] == "unavailable"
    assert command(f, "delete", team_uid=uid, expected_revision=removed["revision"], request_id=delete_id)["replayed"]
    with f[0].connect() as db:
        assert not db.execute(select(m.BoardTeamMember)).all()
        assert len(db.execute(select(m.BoardTeamCommand)).all()) == 6
    assert grants(f) == before


def test_current_membership_account_tenant_and_payload_bound_replay(native):
    f = native
    request_id = str(uuid4())
    created = command(f, "create", actor=2, name="Allowed", request_id=request_id)
    with pytest.raises(TeamConflict):
        command(f, "create", actor=2, name="Different", request_id=request_id)
    with f[0].begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == f[2][2].id).values(actions=["read"]))
    with pytest.raises(TeamDenied):
        command(f, "create", actor=2, name="Allowed", request_id=request_id)
    for user in (f[2][4], f[2][5]):
        with pytest.raises(ValueError):
            command(f, "add_members", team_uid=created["team_uid"], expected_revision=1, user_uids=[user.get_uid()])
    with f[0].begin() as db:
        db.execute(update(m.Organization).values(suspended_at=SafeDateTime.now()))
    with pytest.raises(TeamDenied):
        f[1].list(f[2][1].id, f[3][0].get_uid())


def test_db_constraints_reject_nonparticipants_and_clean_native_lifecycle(native):
    f = native
    created = command(f, "create", name="DB guarded")
    uid = created["team_uid"]
    command(
        f,
        "add_members",
        team_uid=uid,
        expected_revision=1,
        user_uids=[f[2][1].get_uid(), f[2][2].get_uid(), f[2][3].get_uid()],
    )
    from langboard_shared.core.types import SnowflakeID

    team_id = SnowflakeID.from_short_code(uid)
    for user in (f[2][4], f[2][5]):
        with pytest.raises(IntegrityError):
            with DbSession.atomic() as db:
                db.insert(m.BoardTeamMember(team_id=team_id, user_id=user.id))
    with pytest.raises(IntegrityError):
        with DbSession.atomic() as db:
            db.insert(m.BoardTeamMember(team_id=team_id, user_id=f[2][2].id))
    with f[0].begin() as db:
        db.execute(delete(m.ProjectAssignedUser).where(m.ProjectAssignedUser.user_id == f[2][2].id))
    current = f[1].get(f[2][1].id, f[3][0].get_uid(), uid)["team"]
    assert set(current["member_uids"]) == {f[2][1].get_uid(), f[2][3].get_uid()} and current["revision"] == 3
    with f[0].begin() as db:
        db.execute(update(m.User).where(m.User.id == f[2][3].id).values(activated_at=None))
    assert f[1].get(f[2][1].id, f[3][0].get_uid(), uid)["team"]["revision"] == 4
    with f[0].begin() as db:
        db.execute(update(m.Project).where(m.Project.id == f[3][0].id).values(owner_id=f[2][5].id))
    assert f[1].get(f[2][0].id, f[3][0].get_uid(), uid)["team"]["member_uids"] == []
    with f[0].connect() as db:
        assert not db.execute(select(m.BoardTeamMember)).all()
    # Native lifecycle changes do not mutate the historic command receipt.
    with f[0].connect() as db:
        assert len(db.execute(select(m.BoardTeamCommand)).all()) == 2


def test_group_snapshot_intersection_no_sync_current_group_permission_and_staleness(native):
    f = native
    before = grants(f)
    uid = command(f, "create", name="Snapshot")["team_uid"]
    with pytest.raises(TeamDenied):
        f[1].group_preview(f[2][1].id, f[3][0].get_uid(), uid, f[4].get_uid())
    preview = f[1].group_preview(f[2][0].id, f[3][0].get_uid(), uid, f[4].get_uid())
    assert preview["eligible_count"] == 3 and preview["excluded_count"] == 2
    args = {
        "team_uid": uid,
        "expected_revision": 1,
        "group_uid": f[4].get_uid(),
        "snapshot": preview["snapshot"],
        "confirmed_count": 3,
        "confirmed": True,
    }
    request_id = str(uuid4())
    assert command(f, "group_add", actor=0, request_id=request_id, **args)["changed_count"] == 3
    with f[0].begin() as db:
        db.execute(delete(m.ScimGroupMember).where(m.ScimGroupMember.group_id == f[4].id))
    assert f[1].get(f[2][0].id, f[3][0].get_uid(), uid)["team"]["member_count"] == 3
    assert command(f, "group_add", actor=0, request_id=request_id, **args)["replayed"]
    with pytest.raises(TeamConflict):
        command(f, "group_add", actor=0, **args)
    with f[0].begin() as db:
        db.execute(update(m.SettingRole).values(actions=[]))
    with pytest.raises(TeamDenied):
        command(f, "group_add", actor=0, request_id=request_id, **args)
    assert grants(f) == before


def test_atomic_failure_and_concurrent_exactly_once_commands(native, monkeypatch):
    f = native
    uid = command(f, "create", name="Atomic")["team_uid"]
    real_insert = DbSession.insert

    def fail_receipt(db, obj):
        if isinstance(obj, m.BoardTeamCommand):
            raise RuntimeError("controlled receipt failure")
        return real_insert(db, obj)

    monkeypatch.setattr(DbSession, "insert", fail_receipt)
    with pytest.raises(RuntimeError):
        command(f, "add_members", team_uid=uid, expected_revision=1, user_uids=[f[2][2].get_uid()])
    assert f[1].get(f[2][1].id, f[3][0].get_uid(), uid)["team"]["revision"] == 1
    monkeypatch.setattr(DbSession, "insert", real_insert)
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(
            pool.map(
                lambda _: command(
                    f,
                    "add_members",
                    team_uid=uid,
                    expected_revision=1,
                    user_uids=[f[2][2].get_uid()],
                    request_id=request_id,
                ),
                range(2),
            )
        )
    assert sorted(r["replayed"] for r in result) == [False, True]
    assert f[1].get(f[2][1].id, f[3][0].get_uid(), uid)["team"]["member_count"] == 1


def test_native_authenticated_http_contract(native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardTeamApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity

    f = native
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "board-team-http-key-minimum-32-bytes-value"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(f[2][1].id)
    headers = {"Authorization": f"Bearer {access}"}
    base = f"/board/{f[3][0].get_uid()}/teams"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.post(base, headers=headers, json={"request_id": str(uuid4()), "name": "HTTP"})
        assert response.status_code == 200, response.text
        uid = response.json()["team_uid"]
        assert client.get(base, headers=headers).json()["records"][0]["uid"] == uid
        assert (
            client.get(base + "/candidates", headers=headers, params={"query": "person2@"}).json()["records"][0]["uid"]
            == f[2][2].get_uid()
        )
        response = client.post(
            base + f"/{uid}/members",
            headers=headers,
            json={"request_id": str(uuid4()), "expected_revision": 1, "user_uids": [f[2][1].get_uid()]},
        )
        assert response.status_code == 200, response.text
        assert client.get(base + f"/{uid}", headers=headers).json()["team"]["member_count"] == 1
        assert client.get(f"/board/{f[3][1].get_uid()}/teams/{uid}", headers=headers).status_code == 403
        assert (
            client.get(
                base + f"/{uid}/group-preview", headers=headers, params={"group_uid": f[4].get_uid()}
            ).status_code
            == 403
        )


def test_actual_migration_and_downgrade_receipt_guard(native):
    f = native
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024070000-12ab34cde678.py"
    spec = importlib.util.spec_from_file_location("team_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "b1640a24d001"
    tables = [m.BoardTeamMember.__table__, m.BoardTeamCommand.__table__, m.BoardTeam.__table__]
    with f[0].begin() as connection:
        for table in tables:
            table.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    uid = command(f, "create", name="Migrated")["team_uid"]
    command(f, "add_members", team_uid=uid, expected_revision=1, user_uids=[f[2][1].get_uid()])
    with pytest.raises(RuntimeError):
        with f[0].begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
    assert f[1].get(f[2][1].id, f[3][0].get_uid(), uid)["team"]["member_count"] == 1
    with f[0].begin() as connection:
        connection.execute(delete(m.BoardTeamMember))
        connection.execute(delete(m.BoardTeam))
        connection.execute(delete(m.BoardTeamCommand))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "board_team" not in inspect(connection).get_table_names()


@pytest.mark.parametrize("lifecycle", ["assignment_removal", "deactivation", "owner_transfer"])
@pytest.mark.parametrize("first", ["team", "native"])
def test_postgresql_supported_command_serializes_native_lifecycle(native, monkeypatch, lifecycle, first):
    from concurrent.futures import TimeoutError
    from threading import Event

    f = native
    if f[0].dialect.name != "postgresql":
        pytest.skip("PostgreSQL lock conflict proof")
    uid = command(f, "create", actor=0, name="Native race")["team_uid"]
    target = f[2][1] if lifecycle == "owner_transfer" else f[2][2]
    if lifecycle == "assignment_removal":
        statement = delete(m.ProjectAssignedUser).where(
            m.ProjectAssignedUser.project_id == f[3][0].id, m.ProjectAssignedUser.user_id == target.id
        )
    elif lifecycle == "deactivation":
        statement = update(m.User).where(m.User.id == target.id).values(activated_at=None)
    else:
        statement = update(m.Project).where(m.Project.id == f[3][0].id).values(owner_id=f[2][5].id)
    held, release, attempted = Event(), Event(), Event()
    real_insert = DbSession.insert

    def insert_held(db, obj):
        result = real_insert(db, obj)
        if first == "team" and isinstance(obj, m.BoardTeamMember):
            held.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(DbSession, "insert", insert_held)

    def team_command():
        if first == "native":
            attempted.set()
        return command(f, "add_members", actor=0, team_uid=uid, expected_revision=1, user_uids=[target.get_uid()])

    def native_command():
        with f[0].begin() as db:
            if first == "team":
                attempted.set()
            db.execute(statement)
            if first == "native":
                held.set()
                assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(team_command if first == "team" else native_command)
        assert held.wait(5)
        following = pool.submit(native_command if first == "team" else team_command)
        try:
            assert attempted.wait(5)
            with pytest.raises(TimeoutError):
                following.result(timeout=0.15)
        finally:
            release.set()
        initial.result(timeout=5)
        if first == "native":
            with pytest.raises(ValueError):
                following.result(timeout=5)
        else:
            following.result(timeout=5)
    with f[0].connect() as db:
        assert not db.execute(select(m.BoardTeamMember)).all()
    assert f[1].get(f[2][0].id, f[3][0].get_uid(), uid)["team"]["member_count"] == 0


@pytest.mark.parametrize("schema_source", ["metadata", "migration"])
def test_sqlite_fk_parity_purge_and_soft_delete(native, schema_source):
    """Enforced native purge cascades organization data, retaining command history."""
    f = native
    if f[0].dialect.name != "sqlite":
        pytest.skip("Focused SQLite correction proof")
    if schema_source == "migration":
        path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024070000-12ab34cde678.py"
        spec = importlib.util.spec_from_file_location("team_fk_correction", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with f[0].begin() as db:
            for model in (m.BoardTeamMember, m.BoardTeamCommand, m.BoardTeam):
                model.__table__.drop(db)
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
    with f[0].connect() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        inspector = inspect(db)
        for model in (m.BoardTeam, m.BoardTeamMember, m.BoardTeamCommand):
            expected = {
                (
                    tuple(fk.parent.name for fk in constraint.elements),
                    constraint.referred_table.name,
                    tuple(fk.column.name for fk in constraint.elements),
                    constraint.ondelete,
                    constraint.onupdate,
                )
                for constraint in model.__table__.foreign_key_constraints
            }
            actual = {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk.get("options", {}).get("ondelete"),
                    fk.get("options", {}).get("onupdate"),
                )
                for fk in inspector.get_foreign_keys(model.__tablename__)
            }
            assert actual == expected
        assert len(inspector.get_foreign_keys("board_team")) == 1
        assert len(inspector.get_foreign_keys("board_team_member")) == 2
        assert inspector.get_foreign_keys("board_team_command") == []
    soft = command(f, "create", board=0, name="Soft preserved")["team_uid"]
    purge = command(f, "create", board=1, name="Purge cascade")["team_uid"]
    for board, uid in ((0, soft), (1, purge)):
        command(f, "add_members", board=board, team_uid=uid, expected_revision=1, user_uids=[f[2][1].get_uid()])
    with f[0].connect() as db:
        history = db.execute(select(m.BoardTeamCommand.__table__).order_by(m.BoardTeamCommand.id)).all()
        assert len(history) == 4
    # Native soft delete keeps Team organization/history; existing lifecycle triggers
    # prune members of the now-inactive board and advance only the affected Team.
    with f[0].begin() as db:
        db.execute(update(m.Project).where(m.Project.id == f[3][0].id).values(deleted_at=SafeDateTime.now()))
    with f[0].connect() as db:
        teams = db.execute(select(m.BoardTeam.__table__).order_by(m.BoardTeam.id)).mappings().all()
        assert len(teams) == 2 and teams[0]["revision"] == 3 and teams[1]["revision"] == 2
        members = db.execute(select(m.BoardTeamMember.__table__)).mappings().all()
        assert len(members) == 1 and members[0]["team_id"] == teams[1]["id"]
    # Actual hard purge of the second board must cascade its still-present member.
    with f[0].begin() as db:
        db.execute(delete(m.Project).where(m.Project.id == f[3][1].id))
    with f[0].connect() as db:
        remaining = db.execute(select(m.BoardTeam.__table__)).mappings().all()
        assert len(remaining) == 1 and remaining[0]["project_id"] == f[3][0].id
        assert not db.execute(select(m.BoardTeamMember)).all()
        assert db.execute(select(m.BoardTeamCommand.__table__).order_by(m.BoardTeamCommand.id)).all() == history
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []


def decision_fixture(f, *, kind="proposal"):
    """Persist real source resolution; no mock authorization or evaluation."""
    from datetime import datetime, timedelta, timezone
    from langboard.routes.board.forms.Decision import CreateDecisionForm
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.DecisionActors import DecisionActors
    from langboard_shared.domain.services.DecisionFlow import DecisionFlow, card_target_revision

    uid = command(f, "create", name="Decision source")["team_uid"]
    command(f, "add_members", team_uid=uid, expected_revision=1, user_uids=[f[2][2].get_uid()])
    with DbSession.atomic() as db:
        role = db.exec(SqlBuilder.select.table(m.ProjectRole).where(m.ProjectRole.user_id == f[2][2].id)).first()
        role.actions = ["read", "update", "card_update"]
        db.update(role)
        column = m.ProjectColumn(project_id=f[3][0].id, name="Decision")
        db.insert(column)
        card = m.Card(project_id=f[3][0].id, project_column_id=column.id, title="Visible", visibility="SHARED")
        db.insert(card)
    service = DomainService()
    sources = [{"kind": "team", "uid": uid}]
    resolved = DecisionActors(service).preview(f[2][2], f[3][0].get_uid(), card.get_uid(), sources, source_kind=kind)
    data = dict(
        request_id=str(uuid4()),
        source_kind=kind,
        source_ref=card.get_uid(),
        question="Team decision",
        target_revision=card_target_revision(card),
        participant_uids=resolved["actor_uids"],
        actor_sources=sources,
        expected_actor_snapshot=resolved["snapshot"],
        policy={"actor_uid": f[2][2].get_uid()} if kind == "proposal" else None,
    )
    if kind == "poll":
        data.update(
            deadline=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            poll=dict(
                options=[dict(key="a", label="A"), dict(key="b", label="B")],
                participation_quorum=1,
                participation_policy="vote_only",
                winner_policy="plurality",
                tie_policy="needs_info",
                no_quorum_policy="expired",
                no_winner_policy="needs_info",
                needs_info_policy="record",
                allow_change=True,
                tally_visibility="after_close",
            ),
        )
    DecisionFlow(service).create(f[2][2], f[3][0].get_uid(), card.get_uid(), CreateDecisionForm(**data))
    service.close()
    return uid, card


@pytest.mark.parametrize("kind", ["proposal", "poll"])
@pytest.mark.parametrize(
    "variation",
    [
        "pending",
        "terminal",
        "expired",
        "stale",
        "absent",
        "foreign_snapshot",
        "foreign_case",
        "other_team",
        "private",
        "deleted",
    ],
)
def test_current_readable_pending_decision_impact(native, kind, variation):
    from copy import deepcopy
    from datetime import timedelta

    f = native
    uid, card = decision_fixture(f, kind=kind)
    with DbSession.atomic() as db:
        case = db.exec(SqlBuilder.select.table(m.DecisionCase)).first()
        if variation == "terminal":
            case.state = "passed"
        elif variation == "expired":
            case.deadline = SafeDateTime.now() - timedelta(hours=1)
        elif variation == "stale":
            case.target_revision = "stale"
        elif variation == "absent":
            case.actor_snapshot = None
        elif variation in ("foreign_snapshot", "other_team"):
            snapshot = deepcopy(case.actor_snapshot)
            if variation == "foreign_snapshot":
                snapshot["project_uid"] = f[3][1].get_uid()
            else:
                snapshot["sources"] = [{"kind": "team", "uid": f[2][5].get_uid()}]
            case.actor_snapshot = snapshot
        elif variation == "foreign_case":
            case.project_id = f[3][1].id
        elif variation == "private":
            card.visibility = "PRIVATE"
            card.owner_user_id = f[2][1].id
            card.created_by_user_id = f[2][1].id
            db.update(card)
        elif variation == "deleted":
            card.deleted_at = SafeDateTime.now()
            db.update(card)
        db.update(case)
    # Managers and board owners must see the same safe empty result for private cards.
    for actor in (1, 2):
        impact = f[1].get(f[2][actor].id, f[3][0].get_uid(), uid)["decision_impact"]
        assert impact == {"status": "available", "ongoing": variation == "pending", "scope": "readable_cards"}
    with f[0].connect() as db:
        before = db.execute(select(m.DecisionCase.__table__)).all()
    command(f, "delete", team_uid=uid, expected_revision=2)
    with f[0].connect() as db:
        assert db.execute(select(m.DecisionCase.__table__)).all() == before


def test_current_impact_permission_change_fails_closed(native):
    f = native
    uid, _ = decision_fixture(f)
    assert f[1].get(f[2][2].id, f[3][0].get_uid(), uid)["decision_impact"]["ongoing"]
    with f[0].begin() as db:
        db.execute(delete(m.ProjectAssignedUser).where(m.ProjectAssignedUser.user_id == f[2][2].id))
    with pytest.raises(TeamDenied):
        f[1].get(f[2][2].id, f[3][0].get_uid(), uid)
    with pytest.raises(TeamDenied):
        command(f, "delete", actor=2, team_uid=uid, expected_revision=2)


def test_authenticated_http_current_pending_impact_and_private_non_disclosure(native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardTeamApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.services.DecisionFlow import card_target_revision

    f = native
    uid, card = decision_fixture(f)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "board-team-http-key-minimum-32-bytes-value"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(f[2][2].id)
    headers = {"Authorization": f"Bearer {access}"}
    path = f"/board/{f[3][0].get_uid()}/teams/{uid}"
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["decision_impact"] == {"status": "available", "ongoing": True, "scope": "readable_cards"}
        with DbSession.atomic() as db:
            card.visibility = "PRIVATE"
            card.owner_user_id = f[2][1].id
            card.created_by_user_id = f[2][1].id
            db.update(card)
            case = db.exec(SqlBuilder.select.table(m.DecisionCase)).first()
            case.target_revision = card_target_revision(card)
            db.update(case)
        response = client.get(path, headers=headers)
        assert response.json()["decision_impact"] == {
            "status": "available",
            "ongoing": False,
            "scope": "readable_cards",
        }
        # No hidden-card titles, identifiers, counts or global-none implication.
        assert set(response.json()["decision_impact"]) == {"status", "ongoing", "scope"}
        with f[0].begin() as db:
            db.execute(delete(m.ProjectAssignedUser).where(m.ProjectAssignedUser.user_id == f[2][2].id))
        assert client.get(path, headers=headers).status_code == 403
        assert (
            client.request(
                "DELETE", path, headers=headers, json={"request_id": str(uuid4()), "expected_revision": 2}
            ).status_code
            == 403
        )
