"""Native SQL resource relations never confer mail-admin board authority."""

import json
from types import SimpleNamespace
import pytest
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Card,
    EmployeeMembershipPolicy,
    NotificationEmailDelivery,
    Project,
    ProjectActivity,
    ProjectActivityEmailDelivery,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.MailDeliveryAttempt import MailDeliveryAttempt
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.MailDeliveryResourceLinks import MailDeliveryResourceLinks as Links
from langboard_shared.Env import Env
from sqlalchemy import event, text
from sqlalchemy.orm import Session


@pytest.fixture
def resources(board, monkeypatch):
    _, user, project, *_ = board
    engine = DbEngine.get_main_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            db.execute(text("CREATE TABLE bot (id BIGINT PRIMARY KEY)"))
    for model in (
        Card,
        EmployeeMembershipPolicy,
        NotificationEmailDelivery,
        ProjectActivity,
        ProjectActivityEmailDelivery,
        MailDeliveryAttempt,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    service = SimpleNamespace(card=CardService(lambda _: None, lambda _: None, SimpleNamespace()))
    with Session(engine) as db:
        db.add(
            Card(
                id=70,
                project_id=project.id,
                project_column_id=40,
                title="secret-title",
                visibility="INTERNAL",
                owner_user_id=None,
            )
        )
        db.add(
            NotificationEmailDelivery(
                id=80,
                notification_id=81,
                receiver_id=user.id,
                notification_type=NotificationType.MentionedInCard,
                recipient_email="secret@example.invalid",
                preferred_lang="en-US",
                template_name="mentioned_in_card",
                formats={"secret": "secret-body"},
                scope_models=[[Project.__tablename__, int(project.id)], [Card.__tablename__, 70]],
            )
        )
        db.add(
            ProjectActivity(
                id=90,
                project_id=project.id,
                card_id=70,
                activity_type="card_created",
                activity_history={},
                user_id=user.id,
            )
        )
        db.add(
            ProjectActivityEmailDelivery(
                id=91,
                project_id=project.id,
                activity_table=ProjectActivity.__tablename__,
                activity_id=90,
                recipient_email="secret@example.invalid",
            )
        )
        db.add(
            MailDeliveryAttempt(
                id=92,
                started_at=SafeDateTime.now(),
                outcome="unknown",
                recipient_masked="***",
                trigger="direct",
                source="notification",
                source_uid=SnowflakeID(80).to_short_code(),
                linked_ids={"card_id": 70},
            )
        )
        db.commit()
    return engine, user, project, service


def resolve(fixture, source="notification", identifier=80, actor=None):
    _, user, _, service = fixture
    return Links.resolve(
        source, SnowflakeID(identifier).to_short_code(), actor or user, service, CollaborationChannel.HumanUI
    )


def test_native_sources_primary_metadata_only_and_no_page_change(resources, monkeypatch):
    engine, user, project, service = resources
    from langboard_shared.domain.services.MailDeliveryHistory import MailDeliveryHistory

    expected = [{"kind": "card", "project_uid": project.get_uid(), "card_uid": SnowflakeID(70).to_short_code()}]
    statements = []

    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        for source, identifier in (("notification", 80), ("project_activity", 91), ("smtp_attempt", 92)):
            assert resolve(resources, source, identifier) == expected
        assert "secret" not in json.dumps(expected)
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert not any(
            "card.description" in statement or "card.ai_description" in statement for statement in statements
        )
        assert not any("formats" in statement or "linked_ids" in statement for statement in statements)
        monkeypatch.setattr(service.card, "resolve_readable_card", lambda *a, **kw: pytest.fail("Page queried ACL"))
        assert all("resources" not in item for item in MailDeliveryHistory.page()["items"])
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_admin_without_membership_private_guest_revoked_deleted(resources):
    engine, user, project, service = resources
    with engine.begin() as db:
        db.execute(User.__table__.update().where(User.id == user.id).values(is_admin=True))
        db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.user_id == user.id))
    assert resolve(resources) == []
    with engine.begin() as db:
        db.execute(
            ProjectAssignedUser.__table__.insert().values(id=20, project_id=project.id, user_id=user.id, starred=False)
        )
        db.execute(Card.__table__.update().values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2))
    assert resolve(resources) == []
    with engine.begin() as db:
        db.execute(Card.__table__.update().values(owner_user_id=user.id, created_by_user_id=user.id))
    assert resolve(resources)
    with engine.begin() as db:
        db.execute(User.__table__.update().where(User.id == user.id).values(is_admin=False))
        db.execute(ProjectRole.__table__.update().where(ProjectRole.user_id == user.id).values(actions=[]))
    assert resolve(resources) == []
    with engine.begin() as db:
        db.execute(ProjectRole.__table__.update().where(ProjectRole.user_id == user.id).values(actions=["read"]))
        db.execute(Card.__table__.update().values(deleted_at=SafeDateTime.now()))
    assert resolve(resources) == []
    with engine.begin() as db:
        db.execute(Card.__table__.update().values(deleted_at=None))
        db.execute(Project.__table__.update().where(Project.id == project.id).values(deleted_at=SafeDateTime.now()))
    assert resolve(resources) == []


def test_unknown_direct_wrong_sources_and_native_board(resources):
    engine, user, project, _ = resources
    with engine.begin() as db:
        db.execute(MailDeliveryAttempt.__table__.update().values(source="project_activity"))
    assert resolve(resources, "smtp_attempt", 92) == []  # UID exists in notification only.
    with engine.begin() as db:
        db.execute(ProjectActivityEmailDelivery.__table__.update().values(activity_table="user_activity"))
    assert resolve(resources, "project_activity", 91) == []
    with engine.begin() as db:
        db.execute(
            ProjectActivityEmailDelivery.__table__.update().values(
                activity_table=ProjectActivity.__tablename__, project_id=11
            )
        )
    assert resolve(resources, "project_activity", 91) == []
    with engine.begin() as db:
        db.execute(
            NotificationEmailDelivery.__table__.update().values(
                scope_models=[[Project.__tablename__, 11], [Card.__tablename__, 70]]
            )
        )
    assert resolve(resources) == []
    with engine.begin() as db:
        db.execute(
            NotificationEmailDelivery.__table__.update().values(scope_models=[[Project.__tablename__, int(project.id)]])
        )
    assert resolve(resources) == [{"kind": "board", "project_uid": project.get_uid()}]
    for scopes in (None, [], [[Card.__tablename__, True]], [[Card.__tablename__, "70"]]):
        with engine.begin() as db:
            db.execute(NotificationEmailDelivery.__table__.update().values(scope_models=scopes))
        assert resolve(resources) == []
    for source in (None, "unknown"):
        with engine.begin() as db:
            db.execute(MailDeliveryAttempt.__table__.update().values(source=source))
        assert resolve(resources, "smtp_attempt", 92) == []


def test_guest_internal_shared_and_current_account(resources, monkeypatch):
    engine, user, project, service = resources
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    monkeypatch.setattr(service.card, "_get_service", lambda _: SimpleNamespace(is_employee=lambda _: False))
    assert resolve(resources) == []
    with engine.begin() as db:
        db.execute(Card.__table__.update().values(visibility="SHARED"))
    assert resolve(resources)
    with engine.begin() as db:
        db.execute(User.__table__.update().where(User.id == user.id).values(deleted_at=SafeDateTime.now()))
    assert resolve(resources) == []


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_http_native_detail_resources_admin_fence_and_no_leak(resources, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.settings import MailDeliveryHistoryApi
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.helpers import MiddlewareHelper

    engine, user, project, service = resources
    actor = user
    actor.is_admin = True
    with engine.begin() as db:
        db.execute(User.__table__.update().where(User.id == user.id).values(is_admin=True))

    def validate(scope, **kwargs):
        scope["auth"] = actor
        scope["collaboration_channel"] = CollaborationChannel.HumanUI
        return actor

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    module = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(module, "DomainService", lambda: SimpleNamespace(close=lambda: None))
    dependency = MailDeliveryHistoryApi.get_mail_delivery.__defaults__[1].dependency
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    app.dependency_overrides[dependency] = lambda: service
    path = "/settings/mail/deliveries/notification/" + SnowflakeID(80).to_short_code()
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["resources"] == resolve(resources)
        assert "secret" not in response.text
        with engine.begin() as db:
            db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.user_id == user.id))
        response = client.get(path)
        assert response.status_code == 200 and response.json()["resources"] == []
        assert project.get_uid() not in json.dumps(response.json()["resources"])
        actor.is_admin = False
        assert client.get(path).status_code == 403
        actor = 401
        assert client.get(path).status_code == 401


def test_attempt_requires_live_native_relation(resources):
    engine, _, project, _ = resources
    for source_uid in ("bad", "zzzzzzzzzzz", "00000000000"):
        with engine.begin() as db:
            db.execute(MailDeliveryAttempt.__table__.update().values(source_uid=source_uid))
        assert resolve(resources, "smtp_attempt", 92) == []
    with engine.begin() as db:
        db.execute(MailDeliveryAttempt.__table__.update().values(source_uid=SnowflakeID(80).to_short_code()))
        db.execute(NotificationEmailDelivery.__table__.delete())
    assert resolve(resources, "smtp_attempt", 92) == []
    with engine.begin() as db:
        db.execute(ProjectActivity.__table__.update().values(card_id=None))
    assert resolve(resources, "project_activity", 91) == [{"kind": "board", "project_uid": project.get_uid()}]
