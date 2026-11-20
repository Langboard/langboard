"""Actual native SQLite/PostgreSQL rows and authenticated HTTP; no external effects."""

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from langboard.routes.board.forms.Decision import CreateDecisionForm, DecisionResponseForm
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models as m
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.DecisionFlow import (
    DecisionConflict,
    DecisionDenied,
    DecisionFlow,
    DecisionMissing,
    card_target_revision,
)
from langboard_shared.Env import Env
from pydantic import ValidationError
from sqlalchemy import DateTime, create_engine, event, inspect, select, update
from src.api.tests.native_fixtures import TEST_PASSWORD_HASH


def upgrade_actor_delta(connection):
    """Current metadata includes the nullable source-provenance column."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024090000-5def678a9012.py"
    spec = importlib.util.spec_from_file_location("decision_actor_fixture_delta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


def upgrade_clock_delta(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024170000-dfab456c7890.py"
    spec = importlib.util.spec_from_file_location("decision_clock_delta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


def set_session_timezone(engine, timezone):
    if engine.dialect.name == "postgresql":

        def set_timezone(connection, record, proxy):
            previous = connection.autocommit
            connection.autocommit = True
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f"SET TIME ZONE '{timezone}'")
            finally:
                connection.autocommit = previous

        event.listen(engine, "checkout", set_timezone)


def assert_native_clock_types(engine):
    if engine.dialect.name == "postgresql":
        for model in (m.DecisionCase, m.DecisionOption, m.DecisionResponse):
            actual = {column["name"]: column for column in inspect(engine).get_columns(model.__tablename__)}
            for column in model.__table__.columns:
                if isinstance(column.type, DateTime):
                    assert actual[column.name]["type"].timezone == column.type.timezone is True
                    expected_default = str(column.server_default.arg) if column.server_default is not None else None
                    assert actual[column.name]["default"] == expected_default


@pytest.fixture(params=["sqlite", "postgresql"])
def native(tmp_path, monkeypatch, request):
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_DECISION_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Explicit disposable PostgreSQL database required")
        engine = create_engine(url, connect_args={"client_encoding": "utf8"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'decisions.sqlite'}", connect_args={"timeout": 10})
    existing = set(inspect(engine).get_table_names())
    required = {
        model.__table__
        for model in (
            m.User,
            m.Project,
            m.ProjectAssignedUser,
            m.ProjectRole,
            m.SettingRole,
            m.BoardTeam,
            m.BoardTeamMember,
            m.Card,
            m.ProjectColumn,
            m.DecisionCase,
            m.DecisionOption,
            m.DecisionResponse,
            m.EmployeeMembershipPolicy,
            m.UserIdentityLink,
            m.ScimGroup,
            m.ScimGroupMember,
            m.ApiKeyRole,
            m.McpRole,
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
    with DbSession.atomic() as db:
        users = []
        for i in range(6):
            user = m.User(
                firstname=f"Member{i}",
                lastname="Decision",
                email=f"decision{i}@example.invalid",
                password=TEST_PASSWORD_HASH,
                activated_at=SafeDateTime.now(),
            )
            db.insert(user)
            users.append(user)
        board = m.Project(title="Decision", owner_id=users[0].id)
        foreign = m.Project(title="Foreign", owner_id=users[0].id)
        db.insert(board)
        db.insert(foreign)
        column = m.ProjectColumn(project_id=board.id, name="Doing", order=0)
        db.insert(column)
        card = m.Card(project_id=board.id, project_column_id=column.id, title="Question target", visibility="SHARED")
        db.insert(card)
        for user in users[:5]:
            db.insert(m.ProjectAssignedUser(project_id=board.id, user_id=user.id))
            db.insert(
                m.ProjectRole(
                    project_id=board.id,
                    user_id=user.id,
                    actions=["read", "card_update"] if user == users[0] else ["read"],
                )
            )
    service = DomainService()
    flow = DecisionFlow(service)
    yield engine, flow, users, board, foreign, card
    service.close()
    # Derived fixtures add approval and lifecycle tables referencing these rows.
    created = set(inspect(engine).get_table_names()) - existing
    owned = [table for table in m.User.metadata.sorted_tables if table.name in created]
    m.User.metadata.drop_all(engine, tables=owned)
    engine.dispose()


def create(fixture, participants=None, policy=None, **changes):
    _, flow, users, board, _, card = fixture
    participants = participants or [users[1].get_uid()]
    data = dict(
        request_id=str(uuid4()),
        source_kind="proposal",
        source_ref=card.get_uid(),
        question="Approve this revision?",
        target_revision=card_target_revision(card),
        participant_uids=participants,
        policy=policy or {"actor_uid": participants[0]},
    )
    data.update(changes)
    return flow.create(users[0], board.get_uid(), card.get_uid(), CreateDecisionForm(**data))


def form(case, **changes):
    data = dict(
        request_id=str(uuid4()),
        round_id=case["round_id"],
        expected_revision=case["revision"],
        target_revision=case["target_revision"],
        policy_revision=case["policy_revision"],
        choice="approve",
    )
    data.update(changes)
    return DecisionResponseForm(**data)


def respond(fixture, case, actor=1, payload=None):
    _, flow, users, board, _, _ = fixture
    return flow.respond(users[actor], board.get_uid(), case["uid"], payload or form(case))


@pytest.mark.parametrize("session_timezone", ["UTC", "Asia/Seoul"])
def test_native_snapshot_and_read_only_participant_response(native, session_timezone):
    engine, flow, users, board, _, card = native
    set_session_timezone(engine, session_timezone)
    assert_native_clock_types(engine)
    case = create(native)
    assert case["state"] == "pending" and case["effect_result"] is None
    assert case["allowed_actions"] == []
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        listing = flow.list(users[1], board.get_uid(), card.get_uid())
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any("LOCK TABLE" in sql or "BEGIN IMMEDIATE" in sql for sql in statements)
    expected = "REPEATABLE READ READ ONLY" if engine.dialect.name == "postgresql" else "BEGIN"
    assert any(expected in sql for sql in statements)
    assert listing["items"][0]["allowed_actions"] == ["respond"]
    outcome = respond(native, case)
    assert outcome["state"] == "passed" and outcome["revision"] == 1 and outcome["response_order"] == 1
    with engine.connect() as db:
        row = db.execute(select(m.DecisionCase.__table__)).mappings().one()
        responses = db.execute(select(m.DecisionResponse.__table__)).mappings().all()
    assert row["snapshot"] == {
        "participant_uids": [users[1].get_uid()],
        "policy": {"actor_uid": users[1].get_uid()},
        "authorization": "proposal_response",
    }
    assert row["source_kind"] == "proposal" and row["response_order"] == 1 and row["state"] == "passed"
    assert len(responses) == 1 and responses[0]["actor_id"] == users[1].id
    assert responses[0]["receipt"]["effect_result"] is None


def test_payload_bound_create_replay_and_current_acl(native):
    engine, flow, users, board, _, card = native
    request_id = str(uuid4())
    first = create(native, request_id=request_id)
    assert create(native, request_id=request_id)["replayed"]
    with pytest.raises(DecisionConflict):
        create(native, request_id=request_id, question="Different")
    with engine.begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[0].id).values(actions=["read"]))
    with pytest.raises(DecisionDenied):
        create(native, request_id=request_id)
    assert first["revision"] == 0


def test_response_replay_collision_and_revocation_before_replay(native):
    engine, _, users, _, _, _ = native
    case, payload = create(native), None
    payload = form(case)
    first = respond(native, case, payload=payload)
    assert respond(native, case, payload=payload) == {**first, "replayed": True}
    with pytest.raises(DecisionConflict):
        respond(native, case, payload=payload.model_copy(update={"choice": "reject"}))
    with engine.begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
    with pytest.raises(DecisionDenied):
        respond(native, case, payload=payload)


@pytest.mark.parametrize("change", ["membership", "account", "role", "hidden", "target"])
def test_current_authority_and_target_revision_fence(native, change):
    engine, flow, users, board, _, card = native
    case = create(native)
    with engine.begin() as db:
        if change == "membership":
            db.execute(m.ProjectAssignedUser.__table__.delete().where(m.ProjectAssignedUser.user_id == users[1].id))
        elif change == "account":
            db.execute(update(m.User).where(m.User.id == users[1].id).values(activated_at=None))
        elif change == "role":
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
        elif change == "hidden":
            db.execute(
                update(m.Card).values(visibility="PRIVATE", owner_user_id=users[0].id, created_by_user_id=users[0].id)
            )
        else:
            db.execute(update(m.Card).values(title="Changed"))
    with pytest.raises((DecisionDenied, DecisionMissing, DecisionConflict)):
        respond(native, case)
    with engine.connect() as db:
        assert len(db.execute(select(m.DecisionResponse)).all()) == 0
    if change == "target":
        listed = flow.list(users[0], board.get_uid(), card.get_uid())["items"][0]
        assert listed["state"] == "cancelled" and listed["blocked_reason"] == "target_revision_changed"


@pytest.mark.parametrize("change", ["round_id", "expected_revision", "target_revision", "policy_revision"])
def test_expected_round_revision_and_policy_conflicts(native, change):
    case = create(native)
    values = {"round_id": str(uuid4()), "expected_revision": 8, "target_revision": "old", "policy_revision": "old"}
    with pytest.raises(DecisionConflict):
        respond(native, case, payload=form(case, **{change: values[change]}))


def test_changed_policy_new_round_requires_fresh_votes_and_preserves_old_approval(native):
    engine, flow, users, board, _, card = native
    a, b = users[1].get_uid(), users[2].get_uid()
    children = [{"actor_uid": a}, {"actor_uid": b}]
    old = create(native, [a, b], {"op": "any", "children": children, "required": 0})
    old_payload = form(old)
    approved = respond(native, old, payload=old_payload)
    assert approved["state"] == "passed"

    revised = create(native, [a, b], {"op": "all", "children": children, "required": 0})
    assert revised["uid"] != old["uid"] and revised["round_id"] != old["round_id"]
    assert revised["policy_revision"] != old["policy_revision"]
    assert revised["target_revision"] == old["target_revision"]
    assert revised["state"] == "pending" and revised["revision"] == 0
    assert set(revised["active_responders"]) == {a, b}
    # The old request receipt cannot authorize a response against the new case.
    with pytest.raises(DecisionConflict):
        respond(native, revised, payload=old_payload)
    second = respond(native, revised, actor=2)
    assert second["state"] == "pending" and second["active_responders"] == [a]
    assert second["response_order"] == 1
    fresh_approval = respond(native, second)
    assert fresh_approval["state"] == "passed" and fresh_approval["response_order"] == 2

    listed = {item["uid"]: item for item in flow.list(users[0], board.get_uid(), card.get_uid())["items"]}
    assert listed[old["uid"]]["state"] == "passed"
    assert listed[old["uid"]]["policy"]["op"] == "any"
    assert listed[old["uid"]]["revision"] == 1
    assert listed[revised["uid"]]["policy"]["op"] == "all"
    assert listed[revised["uid"]]["revision"] == 2
    assert respond(native, old, payload=old_payload) == {**approved, "replayed": True}
    with engine.connect() as db:
        rows = db.execute(select(m.DecisionResponse.__table__)).mappings().all()
    assert sorted(row["response_order"] for row in rows if row["round_id"] == old["round_id"]) == [1]
    assert sorted(row["response_order"] for row in rows if row["round_id"] == revised["round_id"]) == [1, 2]


@pytest.mark.parametrize("changed", ["policy", "participants"])
def test_persisted_snapshot_change_cannot_reinterpret_or_replay_existing_round(native, changed):
    engine, flow, users, board, _, card = native
    a, b = users[1].get_uid(), users[2].get_uid()
    case = create(native, [a, b], {"op": "all", "children": [{"actor_uid": a}, {"actor_uid": b}], "required": 0})
    payload = form(case)
    first = respond(native, case, payload=payload)
    assert first["state"] == "pending"
    with engine.connect() as db:
        before = db.execute(select(m.DecisionCase.__table__)).mappings().one()
        responses_before = db.execute(select(m.DecisionResponse.__table__)).mappings().all()
    snapshot = dict(before["snapshot"])
    if changed == "policy":
        snapshot["policy"] = {**snapshot["policy"], "op": "any"}
    else:
        # A valid but different policy/participant configuration must use a new round.
        snapshot["participant_uids"] = [a]
        snapshot["policy"] = {"actor_uid": a}
    with engine.begin() as db:
        db.execute(update(m.DecisionCase).values(snapshot=snapshot))
    with pytest.raises(DecisionConflict, match="snapshot"):
        flow.list(users[0], board.get_uid(), card.get_uid())
    with pytest.raises(DecisionConflict, match="snapshot"):
        create(native, [a, b], case["policy"], request_id=before["request_id"])
    with pytest.raises(DecisionConflict, match="snapshot"):
        respond(native, case, payload=payload)
    with pytest.raises(DecisionConflict, match="snapshot"):
        respond(native, first, actor=2)
    with engine.connect() as db:
        after = db.execute(select(m.DecisionCase.__table__)).mappings().one()
        assert db.execute(select(m.DecisionResponse.__table__)).mappings().all() == responses_before
    assert (after["revision"], after["state"], after["policy_revision"], after["round_id"]) == (
        before["revision"], before["state"], before["policy_revision"], before["round_id"]
    )


def test_sources_cross_board_and_nonparticipant_do_not_leak(native):
    _, flow, users, board, foreign, card = native
    case = create(native)
    with pytest.raises(ValueError):
        create(native, source_ref=foreign.get_uid())
    with pytest.raises(DecisionMissing):
        flow.respond(users[1], foreign.get_uid(), case["uid"], form(case))
    with pytest.raises(DecisionMissing):
        flow.list(users[5], board.get_uid(), card.get_uid())
    with pytest.raises(DecisionMissing):
        flow.list(users[0], foreign.get_uid(), card.get_uid())
    with pytest.raises(DecisionDenied):
        respond(native, case, actor=2)


def test_sequence_rejects_premature_response_and_atomic_order(native):
    _, _, users, _, _, _ = native
    a, b = users[1].get_uid(), users[2].get_uid()
    case = create(native, [a, b], {"op": "sequence", "children": [{"actor_uid": a}, {"actor_uid": b}], "required": 0})
    with pytest.raises(DecisionConflict):
        respond(native, case, actor=2)
    first = respond(native, case)
    assert first["state"] == "pending" and first["active_responders"] == [b]
    second = respond(native, first, actor=2)
    assert second["state"] == "passed" and second["response_order"] == 2


def test_duplicate_concurrent_uuid_replay_and_distinct_actor_revision_race(native):
    engine, _, users, _, _, _ = native
    a, b = users[1].get_uid(), users[2].get_uid()
    case = create(native, [a, b], {"op": "all", "children": [{"actor_uid": a}, {"actor_uid": b}], "required": 0})
    payload = form(case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: respond(native, case, payload=payload), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    with pytest.raises(DecisionConflict):
        respond(native, case, actor=2)
    second = respond(native, results[0], actor=2)
    assert second["state"] == "passed" and second["response_order"] == 2
    with engine.connect() as db:
        assert len(db.execute(select(m.DecisionResponse)).all()) == 2


def test_distinct_simultaneous_actors_one_revision_winner_then_retry(native):
    _, _, users, _, _, _ = native
    a, b = users[1].get_uid(), users[2].get_uid()
    case = create(native, [a, b], {"op": "all", "children": [{"actor_uid": a}, {"actor_uid": b}], "required": 0})

    def attempt(actor):
        try:
            return actor, respond(native, case, actor=actor)
        except DecisionConflict:
            return actor, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    assert sum(result is not None for _, result in outcomes) == 1
    winner = next(result for _, result in outcomes if result is not None)
    loser = next(actor for actor, result in outcomes if result is None)
    assert respond(native, winner, actor=loser)["state"] == "passed"


@pytest.mark.parametrize(
    "choice,expected", [("reject", "rejected"), ("abstain", "pending"), ("needs_info", "needs_info")]
)
def test_nonapproval_states_persist_honestly(native, choice, expected):
    case = create(native)
    assert respond(native, case, payload=form(case, choice=choice))["state"] == expected


def test_unsupported_and_untrusted_inputs_are_rejected(native):
    for kind in ("poll", "hitl"):
        with pytest.raises(ValidationError):
            create(native, source_kind=kind)
    for raw in (
        {"role": "owner"},
        {"group": "all"},
        {"delegate": "someone"},
        {"op": "any", "children": [{"actor_uid": native[2][1].get_uid()}, {"actor_uid": "unknown"}], "required": 0},
    ):
        with pytest.raises(ValueError):
            create(native, policy=raw)
    with pytest.raises(ValueError):
        create(native, deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    case = create(native)
    with pytest.raises(ValidationError):
        form(case, actor_uid=native[2][0].get_uid())


@pytest.mark.parametrize("session_timezone", ["UTC", "Asia/Seoul"])
def test_migration_roundtrip_and_used_data_downgrade_guard(native, session_timezone):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine, _, _, _, _, _ = native
    set_session_timezone(engine, session_timezone)
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024060000-b1640a24d001.py"
    spec = importlib.util.spec_from_file_location("decision_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "72f4c6e8a190"
    for model in (m.DecisionResponse, m.DecisionOption, m.DecisionCase):
        model.__table__.drop(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
    poll_path = path.with_name("20261024080000-4abc567def90.py")
    poll_spec = importlib.util.spec_from_file_location("poll_delta", poll_path)
    poll_delta = importlib.util.module_from_spec(poll_spec)
    poll_spec.loader.exec_module(poll_delta)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            poll_delta.upgrade()
        upgrade_actor_delta(connection)
        upgrade_clock_delta(connection)
    assert_native_clock_types(engine)
    migrated = create(native)
    payload = form(migrated)
    approved = respond(native, migrated, payload=payload)
    assert approved["state"] == "passed" and approved["revision"] == 1
    assert respond(native, migrated, payload=payload) == {**approved, "replayed": True}
    with pytest.raises(RuntimeError, match="retained"):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()


def test_clock_migration_refuses_ambiguous_populated_wall_times_without_changes(native):
    engine, _, _, _, _, _ = native
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL timestamp conversion only")
    set_session_timezone(engine, "Asia/Seoul")
    create(native)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE decision_case ALTER COLUMN created_at TYPE timestamp "
            "USING created_at AT TIME ZONE 'Asia/Seoul'"
        )
    with engine.connect() as connection:
        before = connection.execute(select(m.DecisionCase.__table__)).mappings().all()
        before_columns = inspect(connection).get_columns("decision_case")
    with pytest.raises(RuntimeError, match="historical session timezones"):
        with engine.begin() as connection:
            upgrade_clock_delta(connection)
    with engine.connect() as connection:
        assert connection.execute(select(m.DecisionCase.__table__)).mappings().all() == before
        assert [str(column["type"]) for column in inspect(connection).get_columns("decision_case")] == [
            str(column["type"]) for column in before_columns
        ]


def test_native_authenticated_http_actor_inference_replay_and_visibility(native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardDecisionApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity

    engine, _, users, board, foreign, card = native
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "decision-http-native-key-at-least-32-bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    base = f"/board/{board.get_uid()}/card/{card.get_uid()}/decisions"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        inbox_url = f"/board/{board.get_uid()}/my-decisions"
        assert client.get(inbox_url).status_code == 401
        access, refresh = AuthSecurity.authenticate(users[0].id)
        headers = {"Authorization": f"Bearer {access}"}
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        listing = client.get(base, headers=headers)
        assert listing.status_code == 200, listing.text
        data = dict(
            request_id=str(uuid4()),
            source_kind="proposal",
            source_ref=card.get_uid(),
            question="HTTP judgment",
            target_revision=listing.json()["target_revision"],
            participant_uids=[users[1].get_uid()],
            policy={"actor_uid": users[1].get_uid()},
        )
        created = client.post(base, headers=headers, json=data)
        assert created.status_code == 200, created.text
        case = created.json()
        assert created.headers["cache-control"] == "no-store"
        assert client.post(base, headers=headers, json=data).json()["replayed"]
        # Participant has read only: immutable case selection authorizes judgment, not card editing.
        access, refresh = AuthSecurity.authenticate(users[1].id)
        headers = {"Authorization": f"Bearer {access}"}
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        inbox = client.get(inbox_url, headers=headers)
        assert inbox.status_code == 200, inbox.text
        assert inbox.headers["cache-control"] == "no-store"
        assert [item["uid"] for item in inbox.json()["items"]] == [case["uid"]]
        assert client.get(inbox_url, headers=headers, params={"limit": 0}).status_code == 400
        assert client.get(inbox_url, headers=headers, params={"before": "invalid"}).status_code == 400
        response_url = f"/board/{board.get_uid()}/decisions/{case['uid']}/responses"
        body = form(case).model_dump(mode="json")
        assert (
            client.post(response_url, headers=headers, json={**body, "actor_uid": users[0].get_uid()}).status_code
            == 400
        )
        assert (
            client.post(
                response_url.replace(board.get_uid(), foreign.get_uid()), headers=headers, json=body
            ).status_code
            == 404
        )
        approved = client.post(response_url, headers=headers, json=body)
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "passed"
        assert client.get(inbox_url, headers=headers).json()["items"] == []
        assert client.post(response_url, headers=headers, json=body).json()["replayed"]
        with engine.begin() as db:
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
        assert client.post(response_url, headers=headers, json=body).status_code == 403
        assert client.get(inbox_url, headers=headers).status_code == 403


def test_response_transaction_rolls_back_record_counter_and_receipt(native, monkeypatch):
    engine, _, _, _, _, _ = native
    case = create(native)
    original = DbSession.update

    def fail_receipt(self, row):
        if isinstance(row, m.DecisionResponse):
            raise RuntimeError("Injected receipt failure")
        return original(self, row)

    monkeypatch.setattr(DbSession, "update", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt"):
        respond(native, case)
    with engine.connect() as db:
        assert len(db.execute(select(m.DecisionResponse)).all()) == 0
        persisted = db.execute(select(m.DecisionCase.__table__)).mappings().one()
        assert persisted["revision"] == persisted["response_order"] == 0
        assert persisted["state"] == "pending"


def test_revoked_previous_vote_does_not_approve_another_actors_submission(native):
    engine, _, users, _, _, _ = native
    a, b = users[1].get_uid(), users[2].get_uid()
    case = create(native, [a, b], {"op": "all", "children": [{"actor_uid": a}, {"actor_uid": b}], "required": 0})
    first = respond(native, case)
    with engine.begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
    assert respond(native, first, actor=2)["state"] == "pending"


def test_current_acl_is_fenced_against_concurrent_noncooperating_revocation(native, monkeypatch):
    from threading import Event

    engine, flow, users, _, _, _ = native
    case, payload = create(native), None
    payload = form(case)
    evaluating, release, revocation_started, revocation_done = Event(), Event(), Event(), Event()
    original = flow._evaluation

    def held_evaluation(*args):
        evaluating.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(flow, "_evaluation", held_evaluation)

    def revoke():
        revocation_started.set()
        with engine.begin() as db:
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
        revocation_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        submitting = pool.submit(respond, native, case, payload=payload)
        assert evaluating.wait(5)
        revoking = pool.submit(revoke)
        assert revocation_started.wait(5)
        try:
            assert not revocation_done.wait(0.1)
        finally:
            release.set()
        assert submitting.result(timeout=5)["state"] == "passed"
        revoking.result(timeout=5)
    with pytest.raises(DecisionDenied):
        respond(native, case, payload=payload)


def test_terminal_passage_retains_its_resolution_clock_but_never_old_authority(native, monkeypatch):
    engine, flow, users, board, _, card = native
    case = create(native, deadline=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat())
    assert respond(native, case)["state"] == "passed"
    # A terminal round's policy clock is its committed response time, not a fresh open-round expiry.
    with DbSession.atomic() as db:
        from langboard_shared.core.db import SqlBuilder

        row = db.exec(SqlBuilder.select.table(m.DecisionCase)).first()
        current = flow._evaluation(
            db, row, card, CollaborationChannel.Api, datetime.now(timezone.utc) + timedelta(days=1)
        )
        assert current.state == "passed"
    with engine.begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
    summary = flow.list(users[0], board.get_uid(), card.get_uid())["items"][0]
    assert summary["state"] == "cancelled" and summary["blocked_reason"] == "terminal_authority_changed"
    assert summary["allowed_actions"] == []


def test_native_nested_two_of_three_plus_security_and_duplicate_new_uuid(native):
    _, _, users, _, _, _ = native
    a, b, c, security = [user.get_uid() for user in users[1:5]]
    root = {
        "op": "all",
        "required": 0,
        "children": [
            {"op": "k_of_n", "required": 2, "children": [{"actor_uid": uid} for uid in (a, b, c)]},
            {"actor_uid": security},
        ],
    }
    case = create(native, [a, b, c, security], root)
    first = respond(native, case)
    with pytest.raises(DecisionConflict):
        respond(native, first)
    second = respond(native, first, actor=2)
    assert second["state"] == "pending" and second["active_responders"] == [security]
    assert respond(native, second, actor=4)["state"] == "passed"


def test_open_round_expiry_blocks_new_response_without_effect(native, monkeypatch):
    import importlib

    module = importlib.import_module("langboard_shared.domain.services.DecisionFlow")
    _, flow, users, board, _, card = native
    deadline = datetime.now(timezone.utc) + timedelta(minutes=1)
    case = create(native, deadline=deadline.isoformat())

    class LaterClock:
        @staticmethod
        def now(tz):
            return deadline + timedelta(seconds=1)

    monkeypatch.setattr(module, "datetime", LaterClock)
    assert flow.list(users[1], board.get_uid(), card.get_uid())["items"][0]["state"] == "expired"
    with pytest.raises(DecisionConflict):
        respond(native, case)


def test_list_resolves_shared_participant_once_per_coherent_page(native, monkeypatch):
    engine, flow, users, board, _, card = native
    for _ in range(3):
        create(native)
    calls, statements = [], []
    original = flow.service.card.resolve_visibility_context

    def resolve(project, user, channel):
        calls.append(int(user.id))
        return original(project, user, channel)

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(flow.service.card, "resolve_visibility_context", resolve)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = flow.list(users[0], board.get_uid(), card.get_uid())
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(result["items"]) == 3
    assert calls.count(int(users[1].id)) == 1
    assert len(statements) <= 12


@pytest.mark.parametrize("schema_source", ["metadata", "migration"])
@pytest.mark.parametrize(
    "parent,parent_id,remaining_cases,remaining_responses",
    [("project", 10, 0, 0), ("card", 20, 0, 0), ("creator", 1, 0, 0), ("decision", 30, 0, 0), ("actor", 2, 1, 0)],
)
def test_decision_foreign_key_action_parity_and_explicit_purge(
    monkeypatch,
    schema_source,
    parent,
    parent_id,
    remaining_cases,
    remaining_responses,
):
    """Enforced SQLite proves native soft deletion and explicit purge are distinct.

    Minimal parent rows isolate these five Decision edges from unrelated native
    cascades. Both paths create the real Decision tables, not substitute children.
    """
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite://")

    def enforce_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", enforce_foreign_keys)
    parents = sa.MetaData()
    parent_tables = {
        name: sa.Table(
            name,
            parents,
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("updated_at", sa.DateTime),
            sa.Column("deleted_at", sa.DateTime),
        )
        for name in ("user", "project", "card")
    }
    parents.create_all(engine)
    try:
        if schema_source == "metadata":
            m.DecisionCase.__table__.create(engine)
            m.DecisionOption.__table__.create(engine)
            m.DecisionResponse.__table__.create(engine)
        else:
            path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024060000-b1640a24d001.py"
            spec = importlib.util.spec_from_file_location("decision_fk_migration", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                    poll_spec = importlib.util.spec_from_file_location(
                        "poll_fk_delta", path.with_name("20261024080000-4abc567def90.py")
                    )
                    poll_delta = importlib.util.module_from_spec(poll_spec)
                    poll_spec.loader.exec_module(poll_delta)
                    poll_delta.upgrade()
                upgrade_actor_delta(connection)
        expected = {
            ("decision_case", "project_id", "project", "id"): "CASCADE",
            ("decision_case", "card_id", "card", "id"): "CASCADE",
            ("decision_case", "creator_id", "user", "id"): "CASCADE",
            ("decision_response", "decision_id", "decision_case", "id"): "CASCADE",
            ("decision_response", "actor_id", "user", "id"): "CASCADE",
            ("decision_response", "option_id", "decision_option", "id"): "CASCADE",
        }
        native_actions = {
            (model.__tablename__, fk.parent.name, fk.column.table.name, fk.column.name): fk.ondelete
            for model in (m.DecisionCase, m.DecisionResponse)
            for fk in model.__table__.foreign_keys
        }
        reflected_actions = {
            (model.__tablename__, fk["constrained_columns"][0], fk["referred_table"], fk["referred_columns"][0]): fk[
                "options"
            ].get("ondelete")
            for model in (m.DecisionCase, m.DecisionResponse)
            for fk in sa.inspect(engine).get_foreign_keys(model.__tablename__)
        }
        assert native_actions == reflected_actions == expected
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            connection.execute(parent_tables["user"].insert(), [{"id": 1}, {"id": 2}])
            connection.execute(parent_tables["project"].insert(), {"id": 10})
            connection.execute(parent_tables["card"].insert(), {"id": 20})
            connection.execute(
                m.DecisionCase.__table__.insert().values(
                    id=30,
                    created_at=now,
                    updated_at=now,
                    project_id=10,
                    card_id=20,
                    creator_id=1,
                    request_id=str(uuid4()),
                    request_hash="fixture",
                    round_id=str(uuid4()),
                    source_kind="proposal",
                    question="Purge compatibility",
                    target_revision="target",
                    policy_revision="policy",
                    snapshot={"authorization": "proposal_response", "participant_uids": ["actor"]},
                    revision=1,
                    response_order=1,
                    state="passed",
                )
            )
            connection.execute(
                m.DecisionResponse.__table__.insert().values(
                    id=40,
                    created_at=now,
                    updated_at=now,
                    decision_id=30,
                    round_id="fixture",
                    actor_id=2,
                    request_id=str(uuid4()),
                    request_hash="fixture",
                    choice="approve",
                    response_order=1,
                    responded_at=now,
                    receipt={"state": "passed"},
                )
            )
        if parent != "decision":
            model = {"project": m.Project, "card": m.Card, "creator": m.User, "actor": m.User}[parent]
            monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
            with DbSession.use(readonly=False) as db:
                db.exec(sa.delete(model).where(model.id == parent_id))
            with engine.connect() as connection:
                assert connection.scalar(sa.select(sa.func.count()).select_from(m.DecisionCase.__table__)) == 1
                assert connection.scalar(sa.select(sa.func.count()).select_from(m.DecisionResponse.__table__)) == 1
                table = parent_tables["user" if parent in ("creator", "actor") else parent]
                assert connection.scalar(sa.select(table.c.deleted_at).where(table.c.id == parent_id)) is not None
        table = (
            m.DecisionCase.__table__
            if parent == "decision"
            else parent_tables["user" if parent in ("creator", "actor") else parent]
        )
        with engine.begin() as connection:
            connection.execute(table.delete().where(table.c.id == parent_id))
            assert (
                connection.scalar(sa.select(sa.func.count()).select_from(m.DecisionCase.__table__)) == remaining_cases
            )
            assert (
                connection.scalar(sa.select(sa.func.count()).select_from(m.DecisionResponse.__table__))
                == remaining_responses
            )
    finally:
        engine.dispose()
