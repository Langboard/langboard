"""Native Schedule range, authorization and calendar contract."""

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAssignedUser,
    EmployeeMembershipPolicy,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    UserNotification,
)
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from sqlalchemy import create_engine, text


@pytest.fixture
def schedule(monkeypatch):
    engine = create_engine("sqlite://")
    for model in (
        EmployeeMembershipPolicy,
        User,
        Project,
        ProjectColumn,
        ProjectAssignedUser,
        ProjectRole,
        Card,
        CardAssignedUser,
        UserNotification,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Actor",
            lastname="Fixture",
            email="schedule@example.invalid",
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
        cards = []
        for index, visibility in enumerate(["SHARED"] * 6 + ["PRIVATE", "INTERNAL"]):
            card = Card(
                project_id=project.id,
                project_column_id=column.id,
                title=f"Card {index}",
                visibility=visibility,
                created_by_user_id=owner.id if visibility == "PRIVATE" else None,
                owner_user_id=owner.id if visibility == "PRIVATE" else None,
                deadline_at=SafeDateTime.fromisoformat("2026-10-01T00:00:00+00:00"),
            )
            db.insert(card)
            cards.append(card)
        db.insert(CardAssignedUser(card_id=cards[0].id, user_id=actor.id))
        cards[1].created_by_user_id = actor.id
        db.update(cards[1])
        db.insert(
            UserNotification(
                notifier_type="user",
                notifier_id=owner.id,
                receiver_id=actor.id,
                notification_type=NotificationType.MentionedInCard,
                record_list=[("card", int(cards[2].id))],
            )
        )
    service = CardService(lambda _: None, lambda _: None, SimpleNamespace(card=CardRepository(None, None)))
    monkeypatch.setattr(service, "_resolve_internal_access", lambda _: False)
    monkeypatch.setattr(service, "get_work_states", lambda cards, **_: {card.id: {"fixture": True} for card in cards})
    yield service, actor, project, cards, role
    engine.dispose()


def query(schedule, **kwargs):
    service, actor, project, *_ = schedule
    return service.list_schedule(
        actor,
        start="2026-10-01T09:00:00+09:00",
        end="2026-10-02T09:00:00+09:00",
        scope="board",
        project_uid=project.get_uid(),
        **kwargs,
    )


def test_schedule_same_deadline_pagination_and_personal_scope(schedule):
    seen = []
    cursor = None
    while True:
        page = query(schedule, limit=2, cursor=cursor)
        seen.extend(item["card_uid"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [card.get_uid() for card in schedule[3][:6]]
    service, actor, *_ = schedule
    personal = service.list_schedule(actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    assert [item["card_uid"] for item in personal["items"]] == seen[:3]
    assert all(
        set(item)
        == {
            "card_uid",
            "title",
            "project_uid",
            "project_title",
            "column_uid",
            "column_name",
            "deadline_at",
            "work_state",
        }
        for item in personal["items"]
    )


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-01", "2026-10-02T00:00:00Z"),
        ("2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
        ("2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-10-01T00:00:00Z"),
        ("bad", "2026-10-01T00:00:00Z"),
    ],
)
def test_schedule_rejects_invalid_ranges(schedule, start, end):
    with pytest.raises(ValueError):
        schedule[0].list_schedule(schedule[1], start, end)


def test_schedule_half_open_boundaries_and_scope_bound_cursor(schedule):
    service, actor, project, cards, _ = schedule
    with DbSession.use(readonly=False) as db:
        cards[0].deadline_at = SafeDateTime.fromisoformat("2026-09-30T23:59:59.999999Z")
        db.update(cards[0])
        cards[1].deadline_at = SafeDateTime.fromisoformat("2026-10-02T00:00:00Z")
        db.update(cards[1])
        cards[2].deadline_at = SafeDateTime.fromisoformat("2026-10-01T23:59:59.999999Z")
        db.update(cards[2])
    page = query(schedule, limit=1)
    assert page["items"][0]["card_uid"] == cards[3].get_uid()
    for scope, uid, start, end in [
        ("personal", None, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"),
        ("board", project.get_uid(), "2026-10-01T00:00:01Z", "2026-10-02T00:00:00Z"),
    ]:
        with pytest.raises(ValueError):
            service.list_schedule(actor, start, end, scope, uid, cursor=page["next_cursor"])
    for cursor in ("bad", "a" * 513, "W10", "e30"):
        with pytest.raises(ValueError):
            query(schedule, cursor=cursor)
    ids = [item["card_uid"] for item in query(schedule, limit=100)["items"]]
    assert ids == [card.get_uid() for card in cards[3:6]] + [cards[2].get_uid()]
    assert query(schedule)["start"] == "2026-10-01T00:00:00+00:00"


def test_schedule_revocation_cross_board_and_private_channel(schedule):
    service, actor, project, cards, role = schedule
    with DbSession.use(readonly=False) as db:
        cards[6].owner_user_id = actor.id
        cards[6].created_by_user_id = actor.id
        db.update(cards[6])
        other = Project(title="Denied", owner_id=cards[6].project_id)
        db.insert(other)
    assert cards[6].get_uid() not in [item["card_uid"] for item in query(schedule)["items"]]
    human = query(schedule, channel=CollaborationChannel.HumanUI)
    assert cards[6].get_uid() in [item["card_uid"] for item in human["items"]]
    with pytest.raises(PermissionError):
        service.list_schedule(actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "board", other.get_uid())
    cursor = query(schedule, limit=1)["next_cursor"]
    with DbSession.use(readonly=False) as db:
        role.actions = ["update"]
        db.update(role)
    with pytest.raises(PermissionError):
        query(schedule, cursor=cursor)
    assert service.list_schedule(actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")["items"] == []
    with DbSession.use(readonly=False) as db:
        actor.activated_at = None
        db.update(actor)
    assert service.list_schedule(actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")["items"] == []


def test_schedule_full_export_selection_and_explicit_cap(schedule, monkeypatch):
    from langboard_shared.domain.services.CardScheduleCalendar import calendar_bytes

    result = query(schedule, export=True)
    raw = calendar_bytes(result["items"]).decode()
    assert raw.count("BEGIN:VEVENT") == 6
    assert schedule[3][6].get_uid() not in raw
    with pytest.raises(ValueError):
        query(schedule, export=True, cursor=query(schedule, limit=1)["next_cursor"])
    monkeypatch.setattr(schedule[0].repo.card, "get_schedule_page", lambda *_, **__: [None] * 10001)
    with pytest.raises(ValueError, match="10000"):
        query(schedule, export=True)


def test_ical_exact_escaping_folding_and_utc():
    from langboard_shared.domain.services.CardScheduleCalendar import calendar_bytes

    item = {"card_uid": "abc", "title": "Title\\,;\r\nBEGIN:VEVENT\x00", "deadline_at": "2026-10-01T09:00:00+09:00"}
    raw = calendar_bytes([item], now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert raw == (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Langboard//Schedule//EN\r\nCALSCALE:GREGORIAN\r\n"
        b"BEGIN:VEVENT\r\nUID:card-abc@langboard\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261001T000000Z\r\n"
        b"SUMMARY:Title\\\\\\,\\;\\nBEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    item["title"] = "한글" * 100 + "\r\nEND:VCALENDAR"
    folded = calendar_bytes([item])
    assert all(len(line) <= 75 for line in folded.split(b"\r\n"))
    assert b"\r\n " in folded
    assert folded.replace(b"\r\n ", b"").decode().count("\r\nEND:VCALENDAR") == 1


def test_schedule_http_auth_range_and_export(schedule, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.dashboard import DashboardApi  # noqa: F401 - register native routes
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    from sqlalchemy.pool import StaticPool

    # Fixture is thread-shared for native middleware and service reads.
    service, actor, project, *_ = schedule
    engine = DbEngine.get_main_engine()
    User.metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(
            text(
                "CREATE TABLE card_execution_generation (card_id BIGINT PRIMARY KEY, execution_generation BIGINT NOT NULL)"
            )
        )
    clone = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as source, clone.connect() as destination:
        source.connection.driver_connection.backup(destination.connection.driver_connection)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: clone)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: clone)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    params = {
        "scope": "board",
        "project_uid": project.get_uid(),
        "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-02T00:00:00Z",
    }
    with TestClient(app) as client:
        for path in ("/dashboard/schedule", "/dashboard/schedule/ical"):
            assert client.get(path, params=params).status_code == 401
            response = client.get(path, params=params, headers=headers)
            assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-type"].startswith("text/calendar")
        assert response.content.count(b"BEGIN:VEVENT") == 6
        for overrides in (
            {"limit": 101},
            {"start": "2026-10-01"},
            {"end": "2027-10-01T00:00:00Z"},
            {"scope": "invalid"},
        ):
            assert client.get("/dashboard/schedule", params={**params, **overrides}, headers=headers).status_code in (
                400,
                422,
            )
        assert (
            client.get("/dashboard/schedule", params={**params, "project_uid": "denied"}, headers=headers).status_code
            == 404
        )
        with DbSession.use(readonly=False) as db:
            role = schedule[4]
            role.actions = ["update"]
            db.update(role)
        for path in ("/dashboard/schedule", "/dashboard/schedule/ical"):
            assert client.get(path, params=params, headers=headers).status_code == 404
    clone.dispose()


def test_personal_cross_board_contexts_and_old_mentions(schedule, monkeypatch):
    service, actor, project, cards, _ = schedule
    with DbSession.use(readonly=False) as db:
        other = Project(owner_id=actor.id, title="Other readable board")
        db.insert(other)
        column = ProjectColumn(project_id=other.id, name="Archive", is_archive=True)
        db.insert(column)
        card = Card(
            project_id=other.id,
            project_column_id=column.id,
            title="Archived deadline",
            visibility="SHARED",
            created_by_user_id=actor.id,
            archived_at=SafeDateTime.now(),
            deadline_at=SafeDateTime.fromisoformat("2026-10-01T00:00:00Z"),
        )
        db.insert(card)
        # The mentioned card precedes more than the queue's 500 recent notices.
        for _ in range(501):
            db.insert(
                UserNotification(
                    notifier_type="user",
                    notifier_id=actor.id,
                    receiver_id=actor.id,
                    notification_type=NotificationType.MentionedInCard,
                    record_list=[("card", int(cards[0].id))],
                )
            )
    calls = []

    def states(batch, *, context):
        if batch:
            assert len({int(item.project_id) for item in batch}) == 1
            calls.append((int(batch[0].project_id), context.actor_user_id))
        return {item.id: {"fixture": True} for item in batch}

    monkeypatch.setattr(service, "get_work_states", states)
    result = service.list_schedule(actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    assert {item["card_uid"] for item in result["items"]} == {item.get_uid() for item in [*cards[:3], card]}
    assert set(calls) == {(int(project.id), int(actor.id)), (int(other.id), int(actor.id))}


def test_repository_does_not_load_bodies_or_project_activity(schedule):
    from sqlalchemy import event

    statements = []
    engine = DbEngine.get_main_engine()

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        query(schedule)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    card_queries = [sql for sql in statements if "JOIN project_column" in sql]
    assert len(card_queries) == 1
    assert "description" not in card_queries[0]
    assert "project_activity" not in " ".join(statements)


def test_personal_mentions_stay_in_database_and_preserve_filters(schedule):
    """Old mentions survive; wrong receivers/types/visibility cannot select cards."""
    from sqlalchemy import event

    service, actor, project, cards, _ = schedule
    with DbSession.use(readonly=False) as db:
        # Put three previously unselected readable cards behind nonqualifying notices.
        for card, receiver, note_type, visible in (
            (cards[3], cards[6].owner_user_id, NotificationType.MentionedInCard, True),
            (cards[4], actor.id, NotificationType.AssignedToCard, True),
            (cards[5], actor.id, NotificationType.MentionedInComment, False),
        ):
            db.insert(
                UserNotification(
                    notifier_type="user",
                    notifier_id=actor.id,
                    receiver_id=receiver,
                    notification_type=note_type,
                    web_visible=visible,
                    record_list=[("card", int(card.id))],
                )
            )
        # Multiple references and repeated mentions must neither omit nor duplicate a card.
        for _ in range(2):
            db.insert(
                UserNotification(
                    notifier_type="user",
                    notifier_id=actor.id,
                    receiver_id=actor.id,
                    notification_type=NotificationType.MentionedInComment,
                    created_at=SafeDateTime.fromisoformat("2025-01-01T00:00:00Z"),
                    record_list=[("project", int(project.id)), ("card", int(cards[2].id))],
                )
            )
    statements = []
    engine = DbEngine.get_main_engine()

    def capture(_conn, cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine, "after_cursor_execute", capture)
    try:
        page = service.list_schedule(actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", limit=2)
        following = service.list_schedule(
            actor, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", cursor=page["next_cursor"], limit=2
        )
    finally:
        event.remove(engine, "after_cursor_execute", capture)
    assert [item["card_uid"] for item in [*page["items"], *following["items"]]] == [
        card.get_uid() for card in cards[:3]
    ]
    assert following["next_cursor"] is None
    assert not any(sql.lstrip().startswith("SELECT user_notification.record_list") for sql in statements)
    assert sum("JOIN project_column" in sql and "user_notification" in sql for sql in statements) == 2
