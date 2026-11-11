"""Persisted card activity choices preserve current authority and delivery boundaries."""

from importlib import import_module
from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardComment,
    User,
    UserCardNotificationPreference,
    UserNotification,
    UserNotificationUnsubscription,
)
from langboard_shared.domain.services.factory.NotificationService import NotificationService
from langboard_shared.domain.services.factory.UserNotificationSettingService import UserNotificationSettingService
from langboard_shared.infrastructure.repositories.factory.UserNotificationRepository import UserNotificationRepository
from langboard_shared.infrastructure.repositories.factory.UserNotificationSettingRepository import (
    UserNotificationSettingRepository,
)
from src.api.tests.native_fixtures import TEST_PASSWORD_HASH


pytestmark = pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)


@pytest.fixture
def activity(current_card, monkeypatch):
    owner, project, card, cards = current_card
    for model in (UserCardNotificationPreference, CardComment, UserNotification, UserNotificationUnsubscription):
        model.__table__.create(DbEngine.get_main_engine())
    settings = UserNotificationSettingService(
        lambda _: cards,
        lambda _: None,
        SimpleNamespace(user_notification_setting=UserNotificationSettingRepository(None, None)),
    )
    notifications = NotificationService(
        lambda cls: settings if cls is UserNotificationSettingService else cards,
        lambda _: None,
        SimpleNamespace(user_notification=UserNotificationRepository(None, None)),
    )
    module = import_module("langboard_shared.domain.services.factory.NotificationService")
    monkeypatch.setattr(module, "publish_pending_work_events", lambda: None)
    monkeypatch.setattr(notifications, "_publish_web_notification", lambda *args: None)
    with DbSession.atomic() as db:
        actor = User(
            firstname="Other",
            lastname="Actor",
            email="other@example.invalid",
            password=TEST_PASSWORD_HASH,
            activated_at=SafeDateTime.now(),
        )
        db.insert(actor)
        card = Card(project_id=project.id, title="Activity fixture", visibility="SHARED", created_by_user_id=owner.id)
        db.insert(card)
        comment = CardComment(card_id=card.id, user_id=actor.id, content=EditorContentModel(content="ordinary comment"))
        db.insert(comment)
    return owner, actor, project, card, comment, settings, notifications


@pytest.mark.parametrize("mode,comments,moves", [("ignore", 0, 0), ("comments", 1, 0), ("comments_and_moves", 1, 1)])
def test_event_delivery_matrix_persists_choice(activity, mode, comments, moves):
    owner, actor, project, card, comment, settings, notifications = activity
    assert settings.card_activity_mode(owner, project, card) == "ignore"
    with DbSession.use(readonly=False) as db:
        before = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().updated_at
    assert settings.card_activity_mode(owner, project, card, mode) == mode
    assert settings.card_activity_mode(owner, project, card) == mode
    assert notifications.notify_card_activity(actor, project, card, comment=comment) == comments
    column = SimpleNamespace(name="Next")
    assert (
        notifications.notify_card_activity(
            actor, project, card, old_column=SimpleNamespace(name="Before"), new_column=column
        )
        == moves
    )
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserNotification)).all()
        assert len(rows) == comments + moves
        assert all(row.receiver_id == owner.id and row.web_visible for row in rows)
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().updated_at == before


def test_preference_change_is_idempotent_and_does_not_touch_channel_unsubscriptions(activity):
    owner, _, project, card, _, settings, _ = activity
    for mode in ("comments", "comments", "comments_and_moves", "ignore"):
        settings.card_activity_mode(owner, project, card, mode)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
        assert len(rows) == 1 and rows[0].mode == "ignore"
        assert not db.exec(SqlBuilder.select.table(UserNotificationUnsubscription)).all()


@pytest.mark.parametrize("operation", ["read", "write"])
def test_private_nonowner_preference_is_denied_without_persistence(activity, operation):
    owner, actor, project, card, _, settings, _ = activity
    with DbSession.atomic() as db:
        card.visibility = "PRIVATE"
        card.owner_user_id = owner.id
        db.update(card)
    # The project owner is not replaced; an unrelated user cannot infer or subscribe to the card.
    with pytest.raises(PermissionError, match="Card unavailable"):
        settings.card_activity_mode(
            actor, project, card, "comments" if operation == "write" else None, channel=CollaborationChannel.HumanUI
        )
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
    assert (
        settings.card_activity_mode(owner, project, card, "comments", channel=CollaborationChannel.HumanUI)
        == "comments"
    )


@pytest.mark.parametrize("revoked", ["private", "deleted", "inactive"])
def test_delivery_rechecks_current_card_and_user_authority(activity, revoked):
    owner, actor, project, card, comment, settings, notifications = activity
    settings.card_activity_mode(owner, project, card, "comments_and_moves")
    with DbSession.atomic() as db:
        if revoked == "private":
            card.visibility = "PRIVATE"
            card.owner_user_id = actor.id
            card.created_by_user_id = actor.id
            db.update(card)
        elif revoked == "deleted":
            card.deleted_at = SafeDateTime.now()
            db.update(card)
        else:
            owner.activated_at = None
            db.update(owner)
    assert notifications.notify_card_activity(actor, project, card, comment=comment) == 0
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(UserNotification)).all()


def test_self_action_and_reorder_do_not_notify(activity):
    owner, _, project, card, comment, settings, notifications = activity
    settings.card_activity_mode(owner, project, card, "comments_and_moves")
    assert notifications.notify_card_activity(owner, project, card, comment=comment) == 0
    assert notifications.notify_card_activity(owner, project, card) == 0


def test_failed_transaction_rolls_back_notification_and_preference(activity):
    owner, actor, project, card, comment, settings, notifications = activity
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            settings.card_activity_mode(owner, project, card, "comments")
            notifications.notify_card_activity(actor, project, card, comment=comment)
            raise RuntimeError("rollback")
    assert settings.card_activity_mode(owner, project, card) == "ignore"
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(UserNotification)).all()


@pytest.mark.parametrize("change", ["ignore", "private", "inactive"])
def test_pending_delivery_rechecks_preference_and_current_acl(activity, monkeypatch, change):
    from langboard_shared.publishers.UserPublisher import UserPublisher

    owner, actor, project, card, comment, settings, notifications = activity
    settings.card_activity_mode(owner, project, card, "comments")
    assert notifications.notify_card_activity(actor, project, card, comment=comment) == 1
    with DbSession.use(readonly=False) as db:
        note = db.exec(SqlBuilder.select.table(UserNotification)).first()
    if change == "ignore":
        settings.card_activity_mode(owner, project, card, "ignore")
    else:
        with DbSession.atomic() as db:
            if change == "private":
                card.visibility = "PRIVATE"
                card.owner_user_id = actor.id
                card.created_by_user_id = actor.id
                db.update(card)
            else:
                owner.activated_at = None
                db.update(owner)
    sent = []
    monkeypatch.setattr(UserPublisher, "notified", lambda *args: sent.append(args))
    assert NotificationService._publish_web_notification(notifications, note, owner, {}) is True
    assert sent == []


def test_activity_http_requires_auth_and_round_trips_saved_mode(activity, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.notification import NotificationSettingApi
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    owner, _, project, card, _, settings, _ = activity
    service = SimpleNamespace(user_notification_setting=settings, close=lambda: None)
    monkeypatch.setattr(import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) in (
            NotificationSettingApi.get_card_activity_notification_mode,
            NotificationSettingApi.set_card_activity_notification_mode,
        ):
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(owner.id)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    url = f"/notification/settings/project/{project.get_uid()}/card/{card.get_uid()}/activity"
    with TestClient(app) as client:
        assert client.get(url).status_code in (401, 422)
        assert client.get(url, headers=headers).json() == {"mode": "ignore"}
        for mode in ("comments", "comments_and_moves", "ignore"):
            result = client.put(url, json={"mode": mode}, headers=headers)
            assert result.status_code == 200, result.text
            assert result.json() == {"mode": mode}
            assert client.get(url, headers=headers).json() == {"mode": mode}
        assert client.put(url, json={"mode": "invalid"}, headers=headers).status_code == 400
        assert client.get(url, headers=headers).json() == {"mode": "ignore"}

        # Current identity/card visibility are enforced on both registered HTTP operations.
        other = activity[1]
        other_access, other_refresh = AuthSecurity.authenticate(other.id)
        other_headers = {
            "Authorization": f"Bearer {other_access}",
            "Cookie": f"{Env.REFRESH_TOKEN_NAME}={other_refresh}",
        }
        with DbSession.atomic() as db:
            card.visibility = "PRIVATE"
            card.owner_user_id = owner.id
            db.update(card)
        assert client.get(url, headers=other_headers).status_code == 403
        assert client.put(url, json={"mode": "comments"}, headers=other_headers).status_code == 403
        with DbSession.use(readonly=False) as db:
            preferences = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
            assert len(preferences) == 1 and preferences[0].user_id == owner.id and preferences[0].mode == "ignore"
