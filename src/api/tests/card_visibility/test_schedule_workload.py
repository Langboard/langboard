"""Executed native SQLite/PostgreSQL contract for Schedule summary totals."""

import os
from datetime import timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAppOwnership,
    CardAssignedUser,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    EmployeeMembershipPolicy,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    UserNotification,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool


def dt(value):
    return SafeDateTime.fromisoformat(value).astimezone(timezone.utc)


@pytest.fixture
def workload(monkeypatch):
    url = os.getenv("SCHEDULE_WORKLOAD_TEST_URL", "sqlite://")
    schema = None
    if url.startswith("postgresql"):
        admin = create_engine(url)
        schema = "schedule_workload_" + uuid4().hex
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url, connect_args={"options": f"-csearch_path={schema},public -ctimezone=America/New_York"}
        )
        User.metadata.create_all(engine)
    else:
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (
            User,
            EmployeeMembershipPolicy,
            Project,
            ProjectColumn,
            ProjectAssignedUser,
            ProjectRole,
            Card,
            CardAssignedUser,
            CardAppOwnership,
            UserNotification,
            Checklist,
            Checkitem,
            CheckitemTimerRecord,
            WorkflowStageDefinition,
        ):
            model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Actor",
            lastname="Fixture",
            email="actor@example.invalid",
            password="fixture",
            activated_at=SafeDateTime.now(),
        )
        owner = User(
            firstname="Owner",
            lastname="Fixture",
            email="owner@example.invalid",
            password="fixture",
            activated_at=SafeDateTime.now(),
        )
        db.insert(actor)
        db.insert(owner)
        project = Project(title="Schedule", owner_id=owner.id)
        db.insert(project)
        column = ProjectColumn(project_id=project.id, name="Doing")
        db.insert(column)
        membership = ProjectAssignedUser(project_id=project.id, user_id=actor.id)
        db.insert(membership)
        role = ProjectRole(project_id=project.id, user_id=actor.id, actions=["read"])
        db.insert(role)
    service = CardService(lambda _: None, lambda _: None, SimpleNamespace(card=CardRepository(None, None)))
    monkeypatch.setattr(service, "_resolve_internal_access", lambda _: False)
    real_now = SafeDateTime.now
    monkeypatch.setattr(SafeDateTime, "now", lambda: dt("2026-10-02T12:00:00Z"))
    f = SimpleNamespace(
        service=service,
        actor=actor,
        owner=owner,
        project=project,
        column=column,
        membership=membership,
        role=role,
        engine=engine,
        real_now=real_now,
    )
    try:
        yield f
    finally:
        if schema:
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                assert conn.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": schema}) == 0
            admin.dispose()
        engine.dispose()


def card(f, **kwargs):
    values = dict(
        project_id=f.project.id,
        project_column_id=f.column.id,
        title="Native",
        visibility="SHARED",
        created_at=dt("2026-09-01T00:00:00Z"),
    )
    values.update(kwargs)
    if values["visibility"] == "PRIVATE":
        values["created_by_user_id"] = values["owner_user_id"]
    with DbSession.use(readonly=False) as db:
        result = Card(**values)
        db.insert(result)
    return result


def item(c, transitions=(), *, current="stopped", accumulated=None, checked=False):
    if accumulated is None:
        accumulated = sum(
            int((dt(right[0]) - dt(left[0])).total_seconds())
            for left, right in zip(transitions, transitions[1:])
            if left[1] == "started"
        )
    with DbSession.use(readonly=False) as db:
        checklist = Checklist(card_id=c.id, title="Checklist")
        db.insert(checklist)
        result = Checkitem(
            checklist_id=checklist.id,
            title="Item",
            status=CheckitemStatus(current),
            accumulated_seconds=accumulated,
            is_checked=checked,
            created_at=dt("2026-09-01T00:00:00Z"),
        )
        db.insert(result)
        for timestamp, state in transitions:
            db.insert(
                CheckitemTimerRecord(checkitem_id=result.id, status=CheckitemStatus(state), created_at=dt(timestamp))
            )
    return result


def summary(f, **kwargs):
    values = dict(
        start="2026-10-01T00:00:00Z", end="2026-10-02T00:00:00Z", scope="board", project_uid=f.project.get_uid()
    )
    values.update(kwargs)
    return f.service.get_schedule_workload(f.actor, **values)


def test_clipping_predecessor_pauses_running_and_snapshot(workload):
    f = workload
    c = card(f)
    item(
        c,
        [
            ("2026-09-30T23:00:00Z", "started"),
            ("2026-10-01T01:00:00Z", "paused"),
            ("2026-10-01T02:00:00Z", "started"),
            ("2026-10-01T03:00:00Z", "paused"),
            ("2026-10-01T23:00:00Z", "started"),
        ],
        current="started",
        accumulated=10800,
    )
    result = summary(f)
    assert result["time"]["invested_seconds"] == pytest.approx(10800, abs=0.0001)
    assert result["time"]["running_item_count"] == 1
    assert result["workload"] == {"open_count": 1, "incomplete_count": 1}
    assert result["time_attribution"] == "all_work_on_selected_cards"
    assert result["as_of"] == "2026-10-02T12:00:00+00:00"
    future = summary(f, start="2026-10-02T00:00:00Z", end="2026-10-03T00:00:00Z")
    assert future["time"]["invested_seconds"] == pytest.approx(43200, abs=0.0001)
    beyond = summary(f, start="2026-10-03T00:00:00Z", end="2026-10-04T00:00:00Z")
    assert beyond["time"]["invested_seconds"] == 0
    assert beyond["time"]["running_item_count"] == 0


def test_completed_archived_reference_and_deleted_sources(workload):
    f = workload
    with DbSession.use(readonly=False) as db:
        stage = WorkflowStageDefinition(key="done", name="Done", counts_as_completed=True)
        db.insert(stage)
        ref = WorkflowStageDefinition(key="reference", name="Reference")
        db.insert(ref)
        done = ProjectColumn(project_id=f.project.id, name="Done", workflow_stage="done")
        db.insert(done)
        archive = ProjectColumn(project_id=f.project.id, name="Archive", is_archive=True)
        db.insert(archive)
        reference = ProjectColumn(project_id=f.project.id, name="Reference", workflow_stage="reference")
        db.insert(reference)
    for props in (
        {},
        {"archived_at": SafeDateTime.now()},
        {"project_column_id": done.id},
        {"project_column_id": archive.id},
        {"project_column_id": reference.id},
    ):
        c = card(f, **props)
        item(c, [("2026-10-01T01:00:00Z", "started"), ("2026-10-01T02:00:00Z", "stopped")], checked=True)
    for props in (
        {"source_type": "app", "source_uid": "owned"},
        {"source_type": "project_card", "source_uid": "linked"},
        {"deleted_at": SafeDateTime.now()},
    ):
        item(card(f, **props), [("2026-10-01T01:00:00Z", "started")], current="started")
    result = summary(f)
    assert result["workload"] == {"open_count": 1, "incomplete_count": 0}
    assert result["time"]["invested_seconds"] == pytest.approx(18000, abs=0.001)
    assert result["time"]["item_count"] == 5


def test_personal_assigned_created_old_mentioned_no_deadline_or_page_limit(workload):
    f = workload
    selected = [card(f, created_by_user_id=f.actor.id) for _ in range(110)]
    assigned, mentioned, wrong = card(f), card(f), card(f)
    with DbSession.use(readonly=False) as db:
        db.insert(CardAssignedUser(card_id=assigned.id, user_id=f.actor.id, project_assigned_id=f.membership.id))
        db.insert(
            UserNotification(
                notifier_type="user",
                notifier_id=f.owner.id,
                receiver_id=f.actor.id,
                notification_type=NotificationType.MentionedInComment,
                created_at=dt("2024-01-01T00:00:00Z"),
                record_list=[("project", int(f.project.id)), ("card", int(mentioned.id))],
            )
        )
        db.insert(
            UserNotification(
                notifier_type="user",
                notifier_id=f.owner.id,
                receiver_id=f.actor.id,
                notification_type=NotificationType.AssignedToCard,
                record_list=[("card", int(wrong.id))],
            )
        )
    for c in [*selected, assigned, mentioned, wrong]:
        item(c, [("2026-10-01T01:00:00Z", "started"), ("2026-10-01T01:00:01Z", "stopped")])
    result = summary(f, scope="personal", project_uid=None)
    assert result["workload"]["open_count"] == 112
    assert result["time"]["invested_seconds"] == pytest.approx(112, abs=0.01)
    assert summary(f)["workload"]["open_count"] == 113


def test_visibility_active_membership_and_grant_rechecked(workload):
    f = workload
    for visibility, owner in [("SHARED", None), ("INTERNAL", None), ("PRIVATE", f.owner.id), ("PRIVATE", f.actor.id)]:
        item(card(f, visibility=visibility, owner_user_id=owner))
    assert summary(f)["workload"]["open_count"] == 1
    assert summary(f, channel=CollaborationChannel.HumanUI)["workload"]["open_count"] == 2
    assert summary(f, channel=CollaborationChannel.Mcp)["workload"]["open_count"] == 2
    contexts = f.service.resolve_work_visibility_contexts(f.actor, CollaborationChannel.HumanUI)
    with DbSession.use(readonly=False) as db:
        f.role.actions = ["update"]
        db.update(f.role)
    with pytest.raises(PermissionError):
        summary(f)
    # Even stale supplied contexts cannot evade an immediately revoked SQL grant.
    totals = f.service.repo.card.get_schedule_workload(
        f.actor,
        dt("2026-10-01T00:00:00Z"),
        dt("2026-10-02T00:00:00Z"),
        SafeDateTime.now(),
        contexts=contexts,
        personal=False,
    )
    assert totals["open_count"] == totals["item_count"] == 0
    assert summary(f, scope="personal", project_uid=None)["time"]["invested_seconds"] == 0
    with DbSession.use(readonly=False) as db:
        f.role.actions = ["read"]
        db.update(f.role)
        db.delete(f.membership)
    assert (
        f.service.repo.card.get_schedule_workload(
            f.actor,
            dt("2026-10-01T00:00:00Z"),
            dt("2026-10-02T00:00:00Z"),
            SafeDateTime.now(),
            contexts=contexts,
            personal=False,
        )["open_count"]
        == 0
    )


@pytest.mark.parametrize(
    "current,accumulated,transitions",
    [
        ("started", 0, []),
        ("stopped", 60, []),
        ("paused", 0, []),
        ("paused", 0, [("2026-10-01T00:00:00Z", "paused")]),
        ("started", 0, [("2026-10-01T00:00:00Z", "started"), ("2026-10-01T01:00:00Z", "started")]),
        ("paused", 0, [("2026-10-01T00:00:00Z", "started")]),
    ],
)
def test_missing_and_invalid_evidence_explicit(workload, current, accumulated, transitions):
    f = workload
    item(card(f), transitions, current=current, accumulated=accumulated)
    time = summary(f)["time"]
    assert time["invested_seconds"] is None
    assert time["running_item_count"] is None
    assert time["unknown_item_count"] == 1 and time["evidence"] == "incomplete"


def test_new_stopped_zero_and_empty_are_known(workload):
    f = workload
    assert summary(f)["time"]["invested_seconds"] == 0
    item(card(f))
    time = summary(f)["time"]
    assert time["item_count"] == 1 and time["unknown_item_count"] == 0
    assert time["invested_seconds"] == 0 and time["evidence"] == "complete"


def test_dst_ties_offset_and_half_open_edges(workload):
    f = workload
    c = card(f)
    # DST repeated hour: elapsed real time is two hours. Tie transitions are ordered by ID.
    item(
        c,
        [
            ("2026-10-01T00:00:00Z", "started"),
            ("2026-10-01T00:00:00Z", "paused"),
            ("2026-10-01T00:00:00Z", "started"),
            ("2026-10-01T01:00:00Z", "stopped"),
        ],
    )
    assert summary(f, end="2026-10-01T01:00:00Z")["time"]["invested_seconds"] == pytest.approx(3600, abs=0.0001)
    assert summary(f, start="2026-10-01T01:00:00Z")["time"]["invested_seconds"] == 0
    from langboard_shared.infrastructure.repositories.factory.ScheduleWorkloadQuery import schedule_workload

    # Explicit as_of under a future test clock keeps DST arithmetic independent of current date.
    dst = card(f)
    item(dst, [("2026-11-01T00:30:00-04:00", "started"), ("2026-11-01T01:30:00-05:00", "stopped")])
    totals = schedule_workload(
        f.actor,
        dt("2026-11-01T00:30:00-04:00"),
        dt("2026-11-01T01:30:00-05:00"),
        dt("2026-11-02T00:00:00Z"),
        contexts=f.service.resolve_work_visibility_contexts(f.actor, CollaborationChannel.Api),
        personal=False,
    )
    assert totals["invested_seconds"] == pytest.approx(7200, abs=0.0001)


@pytest.mark.parametrize(
    "start,end,scope,uid",
    [
        ("2026-10-01", "2026-10-02T00:00:00Z", "personal", None),
        ("2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", "personal", None),
        ("2026-01-01T00:00:00Z", "2026-10-01T00:00:00Z", "personal", None),
        ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "board", None),
        ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "personal", "wrong"),
    ],
)
def test_range_and_scope_validation(workload, start, end, scope, uid):
    with pytest.raises(ValueError):
        workload.service.get_schedule_workload(workload.actor, start, end, scope, uid)


def test_one_summary_row_no_body_or_history_transfer(workload):
    f = workload
    c = card(f, created_by_user_id=f.actor.id)
    transitions = []
    for minute in range(60):
        transitions.extend(
            [(f"2026-10-01T01:{minute:02}:00Z", "started"), (f"2026-10-01T01:{minute:02}:30Z", "paused")]
        )
    item(c, transitions, current="paused")
    statements = []

    def capture(_conn, cursor, sql, params, context, many):
        statements.append((sql, cursor.description))

    event.listen(f.engine, "after_cursor_execute", capture)
    try:
        result = summary(f, scope="personal", project_uid=None)
    finally:
        event.remove(f.engine, "after_cursor_execute", capture)
    assert result["time"]["invested_seconds"] == pytest.approx(1800, abs=0.01)
    aggregates = [(sql, description) for sql, description in statements if "workload_time_totals" in sql]
    assert len(aggregates) == 1
    sql, description = aggregates[0]
    assert len(description) == 6
    assert "card.description" not in sql and "card.title" not in sql and "card_comment" not in sql
    assert "SELECT user_notification.record_list" not in sql
    assert [column[0] for column in description] == [
        "open_count",
        "incomplete_count",
        "item_count",
        "unknown_item_count",
        "known_invested_seconds",
        "running_item_count",
    ]


def test_http_auth_server_clock_and_errors(workload, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.dashboard import DashboardApi  # noqa: F401 - register native routes
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.services import DomainService
    from langboard_shared.Env import Env

    f = workload
    monkeypatch.setattr(SafeDateTime, "now", f.real_now)
    monkeypatch.setattr(DomainService, "card", property(lambda _: f.service))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(f.actor.id)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    params = dict(
        start="2026-10-01T00:00:00Z", end="2026-10-02T00:00:00Z", scope="board", project_uid=f.project.get_uid()
    )
    with TestClient(app) as client:
        assert client.get("/dashboard/schedule/workload", params=params).status_code == 401
        response = client.get("/dashboard/schedule/workload", params=params, headers=headers)
        assert response.status_code == 200, response.text
        assert abs((dt(response.json()["as_of"]) - f.real_now()).total_seconds()) < 10
        assert response.headers["cache-control"] == "private, no-store"
        assert (
            client.get("/dashboard/schedule/workload", params={**params, "start": "bad"}, headers=headers).status_code
            == 400
        )
        with DbSession.use(readonly=False) as db:
            f.role.actions = ["update"]
            db.update(f.role)
        assert client.get("/dashboard/schedule/workload", params=params, headers=headers).status_code == 404


def test_missing_internal_cycle_detected_without_adding_accumulation(workload):
    f = workload
    item(card(f), [("2026-10-01T01:00:00Z", "started"), ("2026-10-01T02:00:00Z", "stopped")], accumulated=7200)
    time = summary(f)["time"]
    assert time["invested_seconds"] is None and time["unknown_item_count"] == 1
    assert time["known_invested_seconds"] == 0


def test_microsecond_clipping_and_per_interval_native_truncation(workload):
    f = workload
    item(
        card(f),
        [
            ("2026-10-01T00:00:00.000001Z", "started"),
            ("2026-10-01T00:00:00.999999Z", "paused"),
            ("2026-10-01T00:00:01.000001Z", "started"),
            ("2026-10-01T00:00:01.999999Z", "stopped"),
        ],
    )
    time = summary(f, start="2026-10-01T00:00:00.500000Z", end="2026-10-01T00:00:01.500000Z")["time"]
    assert time["invested_seconds"] == pytest.approx(0.999998, abs=0.000001)


def test_app_ownership_and_deleted_native_item_scope(workload):
    f = workload
    active = card(f)
    owned = card(f)
    released = card(f)
    item(active)
    item(owned, [("2026-10-01T01:00:00Z", "started")], current="started")
    item(released)
    deleted_item = item(active, [("2026-10-01T01:00:00Z", "started")], current="started")
    deleted_list_item = item(active, [("2026-10-01T01:00:00Z", "started")], current="started")
    with DbSession.use(readonly=False) as db:
        db.insert(CardAppOwnership(card_id=owned.id, app_key="github"))
        db.insert(CardAppOwnership(card_id=released.id, app_key=None))
        deleted_item.deleted_at = SafeDateTime.now()
        db.update(deleted_item)
        from langboard_shared.core.db import SqlBuilder

        checklist = db.exec(
            SqlBuilder.select.table(Checklist).where(Checklist.id == deleted_list_item.checklist_id)
        ).first()
        checklist.deleted_at = SafeDateTime.now()
        db.update(checklist)
    result = summary(f)
    assert result["workload"]["open_count"] == 2
    assert result["time"]["item_count"] == 2 and result["time"]["invested_seconds"] == 0


def test_current_actor_deactivation_and_cross_board_personal_scope(workload):
    f = workload
    item(card(f, created_by_user_id=f.actor.id))
    with DbSession.use(readonly=False) as db:
        other = Project(title="Other", owner_id=f.actor.id)
        db.insert(other)
        column = ProjectColumn(project_id=other.id, name="Doing")
        db.insert(column)
    item(card(f, project_id=other.id, project_column_id=column.id, created_by_user_id=f.actor.id))
    assert summary(f, scope="personal", project_uid=None)["workload"]["open_count"] == 2
    assert summary(f)["workload"]["open_count"] == 1
    contexts = f.service.resolve_work_visibility_contexts(f.actor, CollaborationChannel.Api)
    with DbSession.use(readonly=False) as db:
        f.actor.activated_at = None
        db.update(f.actor)
    totals = f.service.repo.card.get_schedule_workload(
        f.actor,
        dt("2026-10-01T00:00:00Z"),
        dt("2026-10-02T00:00:00Z"),
        SafeDateTime.now(),
        contexts=contexts,
        personal=False,
    )
    assert totals["open_count"] == totals["item_count"] == 0
