"""Persisted card activity choices preserve current authority and delivery boundaries."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
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
    for model in (CardComment, UserNotification, UserNotificationUnsubscription):
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
        assert client.get(url, headers=headers).json() == {"mode": "ignore", "reasons": []}
        settings.record_card_participation(owner, project, card, "creator")
        assert client.get(url, headers=headers).json() == {"mode": "comments_and_moves", "reasons": ["creator"]}
        for mode in ("comments", "comments_and_moves", "ignore"):
            result = client.put(url, json={"mode": mode}, headers=headers)
            assert result.status_code == 200, result.text
            assert result.json() == {"mode": mode, "reasons": ["creator", "direct"]}
            assert client.get(url, headers=headers).json() == {"mode": mode, "reasons": ["creator", "direct"]}
        assert client.put(url, json={"mode": "invalid"}, headers=headers).status_code == 400
        assert client.get(url, headers=headers).json() == {"mode": "ignore", "reasons": ["creator", "direct"]}

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


def test_participation_preserves_modes_and_records_distinct_reasons(activity):
    owner, _, project, card, _, settings, _ = activity
    with DbSession.use(readonly=False) as db:
        before = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().updated_at
    settings.record_card_participation(owner, project, card, "creator")
    settings.record_card_participation(owner, project, card, "assignee")
    settings.record_card_participation(owner, project, card, "creator")
    assert settings.card_activity_mode(owner, project, card) == "comments_and_moves"
    settings.card_activity_mode(owner, project, card, "ignore")
    settings.record_card_participation(owner, project, card, "commenter")
    settings.record_card_participation(owner, project, card, "mentioned")
    settings.card_activity_mode(owner, project, card, "ignore")
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
        assert len(rows) == 1
        assert rows[0].mode == "ignore"
        assert rows[0].reasons == ["creator", "assignee", "direct", "commenter", "mentioned"]
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().updated_at == before
        assert not db.exec(SqlBuilder.select.table(UserNotificationUnsubscription)).all()
        assert not db.exec(SqlBuilder.select.table(UserNotification)).all()


def test_participation_does_not_update_existing_reason(activity):
    from sqlalchemy import event

    owner, _, project, card, _, settings, _ = activity
    settings.card_activity_mode(owner, project, card, "ignore")
    settings.record_card_participation(owner, project, card, "creator")
    statements = []
    engine = DbEngine.get_main_engine()

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().lower().startswith(("update ", "insert ", "delete ")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        settings.record_card_participation(owner, project, card, "creator")
        settings.card_activity_mode(owner, project, card, "ignore")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements == []


def test_participation_rolls_back_creation_and_reason_merge(activity):
    owner, _, project, card, _, settings, _ = activity
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            settings.record_card_participation(owner, project, card, "creator")
            raise RuntimeError("rollback")
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
    settings.card_activity_mode(owner, project, card, "ignore")
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            settings.record_card_participation(owner, project, card, "assignee")
            raise RuntimeError("rollback")
    with DbSession.use(readonly=False) as db:
        preference = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).first()
        assert preference.mode == "ignore" and preference.reasons == ["direct"]


@pytest.mark.parametrize("revoked", ["private", "deleted", "inactive"])
def test_participation_rechecks_current_authority(activity, revoked):
    owner, actor, project, card, _, settings, _ = activity
    settings.record_card_participation(owner, project, card, "creator")
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
    with pytest.raises(PermissionError, match="Card unavailable"):
        settings.record_card_participation(owner, project, card, "mentioned", channel=CollaborationChannel.HumanUI)
    with DbSession.use(readonly=False) as db:
        preference = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).first()
        assert preference.reasons == ["creator"]


def test_participation_rejects_unknown_reason(activity):
    owner, _, project, card, _, settings, _ = activity
    with pytest.raises(ValueError, match="Invalid card participation reason"):
        settings.record_card_participation(owner, project, card, "invalid")
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()


def test_concurrent_private_creates_record_creator_participation(activity):
    owner, _, project, _, _, settings, _ = activity
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL foreign-key row locks")

    inserted = Barrier(2)

    def create_private(index):
        with DbSession.atomic() as db:
            card = Card(
                project_id=project.id,
                title=f"Concurrent private card {index}",
                visibility="PRIVATE",
                owner_user_id=owner.id,
                created_by_user_id=owner.id,
            )
            db.insert(card)
            # Both transactions must hold the owner FK lock before they record participation.
            inserted.wait(timeout=5)
            settings.record_card_participations(
                project, card, [(owner, "creator")], channel=CollaborationChannel.HumanUI
            )
            return card.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create_private, index) for index in range(2)]
        card_ids = {future.result(timeout=15) for future in futures}

    assert len(card_ids) == 2
    with DbSession.use(readonly=False) as db:
        preferences = db.exec(
            SqlBuilder.select.table(UserCardNotificationPreference).where(
                UserCardNotificationPreference.card_id.in_(card_ids),
            )
        ).all()
    assert {preference.card_id for preference in preferences} == card_ids
    assert all(preference.user_id == owner.id and preference.reasons == ["creator"] for preference in preferences)


def test_batch_participation_deduplicates_and_preserves_modes(activity, monkeypatch):
    from sqlalchemy import event

    owner, actor, project, card, _, settings, _ = activity
    settings.card_activity_mode(owner, project, card, "ignore")
    cards = settings._get_service(None)
    monkeypatch.setattr(cards, "resolve_readable_card", lambda *args: (project, card, None))
    statements = []
    engine = DbEngine.get_main_engine()

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "order by" in statement.lower() and "user" in statement.lower():
            statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        settings.record_card_participations(project, card, [
            (actor, "mentioned"), (owner, "creator"), (actor, "mentioned"),
            (owner, "assignee"), (owner, "creator"), (actor, "commenter"),
        ])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1 and "order by" in statements[0] and " asc" in statements[0]
    if engine.dialect.name == "postgresql":
        assert "for no key update" in statements[0]
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
        preferences = {row.user_id: row for row in rows}
        assert preferences[owner.id].mode == "ignore"
        assert preferences[owner.id].reasons == ["direct", "creator", "assignee"]
        assert preferences[actor.id].mode == "comments_and_moves"
        assert preferences[actor.id].reasons == ["mentioned", "commenter"]


@pytest.mark.parametrize("unavailable", ["unreadable", "inactive"])
def test_batch_participation_skips_unavailable_users(activity, unavailable):
    owner, actor, project, card, _, settings, _ = activity
    with DbSession.atomic() as db:
        if unavailable == "unreadable":
            card.visibility = "PRIVATE"
            card.owner_user_id = owner.id
            db.update(card)
        else:
            actor.activated_at = None
            db.update(actor)
    settings.record_card_participations(
        project, card, [(actor, "mentioned"), (owner, "commenter")], channel=CollaborationChannel.HumanUI
    )
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
        assert len(rows) == 1 and rows[0].user_id == owner.id and rows[0].reasons == ["commenter"]


def test_batch_participation_rolls_back(activity, monkeypatch):
    owner, actor, project, card, _, settings, _ = activity
    cards = settings._get_service(None)
    monkeypatch.setattr(cards, "resolve_readable_card", lambda *args: (project, card, None))
    settings.card_activity_mode(owner, project, card, "ignore")
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            settings.record_card_participations(project, card, [(actor, "mentioned"), (owner, "creator")])
            raise RuntimeError("rollback")
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).all()
        assert len(rows) == 1 and rows[0].user_id == owner.id
        assert rows[0].mode == "ignore" and rows[0].reasons == ["direct"]


@pytest.mark.parametrize("fail", [False, True])
def test_native_comment_and_participation_commit_or_rollback_together(activity, monkeypatch, fail):
    from langboard_shared.domain.models import CardChangeReceipt
    from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.infrastructure.repositories.factory.CardCommentRepository import CardCommentRepository
    from langboard_shared.publishers import CardPublisher
    from sqlalchemy import text

    owner, _, project, card, _, settings, _ = activity
    engine = DbEngine.get_main_engine()
    CardChangeReceipt.__table__.create(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE SEQUENCE content_change_seq"))
    cards = settings._get_service(CardService)
    monkeypatch.setattr(CardPublisher, "metadata_changed", lambda *args: None)
    service = CardCommentService(
        lambda cls: settings if cls is UserNotificationSettingService else cards,
        lambda _: None,
        SimpleNamespace(card_comment=CardCommentRepository(None, None)),
    )
    settings.card_activity_mode(owner, project, card, "ignore")
    original = settings.record_card_participations
    if fail:
        def record_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("participation rollback")
        monkeypatch.setattr(settings, "record_card_participations", record_then_fail)
    content = EditorContentModel(content=f"native comment [**@owner**]({owner.get_uid()})")
    if fail:
        with pytest.raises(RuntimeError, match="participation rollback"):
            service.create(owner, project, card, content, dispatch_effects=False)
    else:
        created = service.create(owner, project, card, content, dispatch_effects=False)
        assert isinstance(created, CardComment)
    with DbSession.use(readonly=False) as db:
        comments = db.exec(SqlBuilder.select.table(CardComment).where(CardComment.user_id == owner.id)).all()
        receipts = db.exec(SqlBuilder.select.table(CardChangeReceipt)).all()
        preference = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).first()
        assert len(comments) == len(receipts) == (0 if fail else 1)
        assert preference.mode == "ignore"
        assert preference.reasons == (["direct"] if fail else ["direct", "commenter", "mentioned"])


def test_participation_migration_preserves_existing_ignore(activity):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    owner, _, project, card, _, settings, _ = activity
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261117162605-6ee7b62c61e6.py"
    spec = spec_from_file_location("participation_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = DbEngine.get_main_engine()
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            # Reflection follows existing foreign keys while rebuilding the table.
            for table in ("organization", "bot", "project_column"):
                connection.execute(text(f'CREATE TABLE IF NOT EXISTS "{table}" (id BIGINT PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        connection.execute(text(
            "INSERT INTO user_card_notification_preference "
            "(id,user_id,card_id,mode,created_at,updated_at) "
            "VALUES (:id,:user_id,:card_id,'ignore',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ), {"id": 1, "user_id": owner.id, "card_id": card.id})
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        row = connection.execute(text("SELECT mode,CAST(reasons AS TEXT) FROM user_card_notification_preference")).one()
        assert row[0] == "ignore" and row[1] == "[]"
    settings.record_card_participation(owner, project, card, "commenter")
    with DbSession.use(readonly=False) as db:
        preference = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).first()
        assert preference.mode == "ignore" and preference.reasons == ["commenter"]



def test_self_mention_enrolls_without_notifying_self(activity, monkeypatch):
    owner, _, project, card, comment, settings, notifications = activity
    monkeypatch.setattr(notifications, "_NotificationService__get_column_by_card", lambda _: SimpleNamespace(id=0))
    from langboard_shared.domain.models import Bot
    from langboard_shared.helpers import InfraHelper
    original = InfraHelper.get_by_id_like
    monkeypatch.setattr(InfraHelper, "get_by_id_like", lambda model, value, **kwargs: None if model is Bot else original(model, value, **kwargs))
    comment.content = EditorContentModel(content=f"[**@owner**]({owner.get_uid()})")
    notifications.notify_mentioned_in_comment(owner, project, card, comment)
    with DbSession.use(readonly=False) as db:
        preference = db.exec(SqlBuilder.select.table(UserCardNotificationPreference)).first()
        assert preference.user_id == owner.id and preference.reasons == ["mentioned"]
        assert preference.mode == "comments_and_moves"
        assert not db.exec(SqlBuilder.select.table(UserNotification)).all()


@pytest.mark.parametrize("mention_enabled", [True, False])
def test_comment_mention_and_activity_deliver_once_per_recipient(activity, monkeypatch, mention_enabled):
    from langboard_shared.domain.models import ProjectColumn
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.models.UserNotificationUnsubscription import NotificationChannel

    owner, actor, project, card, comment, settings, notifications = activity
    monkeypatch.setattr(notifications, "_NotificationService__get_column_by_card", lambda _: ProjectColumn(
        id=0, project_id=project.id, name="Fixture"
    ))
    # Exercise native web fanout while keeping this fixture independent of SMTP.
    original = settings.has_unsubscription
    monkeypatch.setattr(settings, "has_unsubscription", lambda user, kind, scopes, channel:
        channel == NotificationChannel.Email or (kind == NotificationType.MentionedInComment and not mention_enabled)
        or original(user, kind, scopes, channel))
    comment.content = EditorContentModel(content=f"[**@owner**]({owner.get_uid()})")
    settings.record_card_participations(project, card, [(owner, "creator"), (owner, "assignee")])
    mentioned = notifications.notify_mentioned_in_comment(actor, project, card, comment)
    assert mentioned == ({owner.id} if mention_enabled else set())
    assert notifications.notify_card_activity(actor, project, card, comment=comment, exclude_user_ids=mentioned) == (0 if mention_enabled else 1)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(UserNotification)).all()
        visible = [row for row in rows if row.web_visible]
        assert len(visible) == 1 and visible[0].receiver_id == owner.id
        assert visible[0].notification_type == (NotificationType.MentionedInComment if mention_enabled else NotificationType.CardCommentAdded)

def test_notification_scheduler_restores_enabled_rules_and_removes_stale_entries(activity):
    from crontab import CronTab
    from langboard_shared.domain.services.factory.AppSettingService import AppSettingService

    interval = "*/5 * * * *"
    rules = [SimpleNamespace(interval_str=interval), SimpleNamespace(interval_str=interval)]
    repository = SimpleNamespace(notification_schedule_rule=SimpleNamespace(get_enabled=lambda: rules))
    settings = AppSettingService(lambda _: None, lambda _: None, repository)
    cron = CronTab(user=False, tab="0 8 * * * /bin/bash /tmp/foreign # other-job\n")
    stale = cron.new(command="/tmp/old", comment="notification-schedule:0 8 * * *", user="/bin/bash")
    stale.setall("0 8 * * *")
    assert settings.reconcile_notification_schedule_cron(cron)
    reminder_comment = "notification-schedule:* * * * *"
    assert {job.comment for job in cron} == {"other-job", reminder_comment, f"notification-schedule:{interval}"}
    assert {job.command for job in cron if job.comment.startswith("notification-schedule:")} == {
        f"/app/scripts/run_notification_cron.sh '{interval}'",
        "/app/scripts/run_notification_cron.sh '* * * * *'",
    }
    assert not settings.reconcile_notification_schedule_cron(cron)
    rules.clear()
    assert settings.reconcile_notification_schedule_cron(cron)
    assert [job.comment for job in cron] == ["other-job", reminder_comment]

def test_due_reminders_use_current_audience_and_persist_once(activity, current_card, monkeypatch):
    from datetime import timedelta
    from langboard_shared.domain.models import (
        CardAssignedUser,
        CardReminderOccurrence,
        CardReminderRule,
        ProjectAssignedUser,
        ProjectColumn,
        ProjectRole,
    )
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
    from langboard_shared.domain.models.UserNotification import NotificationType
    from langboard_shared.domain.services.CardReminderPersistence import card_allows_reminders, create_rule
    from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule
    from langboard_shared.tasks.notifications import CardReminderTask

    owner, participant, project, card, _, _, notifications = activity
    for model in (CardAssignedUser, CardReminderRule, CardReminderOccurrence):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.atomic() as db:
        membership = ProjectAssignedUser(project_id=project.id, user_id=participant.id)
        db.insert(membership)
        db.insert(ProjectRole(project_id=project.id, user_id=participant.id, actions=[ProjectRoleAction.Read.value]))
        db.insert(CardAssignedUser(card_id=card.id, user_id=participant.id, project_assigned_id=membership.id))

    active = ProjectColumn(id=0, project_id=project.id, name="Active")
    monkeypatch.setattr(notifications, "_NotificationService__get_column_by_card", lambda _: active)
    monkeypatch.setattr(CardReminderTask, "card_allows_reminders", lambda db, card: card_allows_reminders(db, card, active))
    service = SimpleNamespace(card=current_card[3], notification=notifications)
    now = SafeDateTime.now()
    schedule = ReminderSchedule("UTC", anchor=now, frequency="daily")
    earlier = now - timedelta(minutes=1)
    private_owner_rule = create_rule(card.id, owner.id, schedule, earlier)
    participant_rule = create_rule(card.id, participant.id, schedule, earlier)
    shared_rule = create_rule(card.id, owner.id, schedule, earlier, is_shared=True)

    with DbSession.atomic() as db:
        card.visibility = "PRIVATE"
        card.owner_user_id = owner.id
        db.update(card)
    assert CardReminderTask.run_due_card_reminders(service, now) == 1
    assert CardReminderTask.run_due_card_reminders(service, now) == 0
    with DbSession.atomic() as db:
        notices = db.exec(SqlBuilder.select.table(UserNotification)).all()
        occurrences = db.exec(SqlBuilder.select.table(CardReminderOccurrence)).all()
        assert len(notices) == len(occurrences) == 1
        assert occurrences[0].rule_id == private_owner_rule.id
        assert notices[0].receiver_id == owner.id and notices[0].web_visible
        assert notices[0].notification_type == NotificationType.CardReminder
        assert notices[0].message_vars["occurrence_uid"] == occurrences[0].get_uid()
        assert notifications.get_dispatch_context(owner, notices[0].id) is None
        for rule_id in (participant_rule.id, shared_rule.id):
            rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
            assert rule.consumed_count == 0
        card.visibility = "SHARED"
        card.owner_user_id = None
        db.update(card)

    assert CardReminderTask.run_due_card_reminders(service, now + timedelta(days=1)) == 3
    with DbSession.atomic() as db:
        notices = db.exec(SqlBuilder.select.table(UserNotification)).all()
        assert sum(n.receiver_id == participant.id for n in notices) == 2
        assert len(notices) == 4


def test_reminder_snooze_is_recipient_scoped_and_preserves_occurrence(activity, current_card, monkeypatch):
    from datetime import timedelta
    from langboard_shared.domain.models import (
        CardReminderOccurrence,
        CardReminderRule,
        Checkitem,
        Checklist,
        ProjectAssignedUser,
        ProjectColumn,
        ProjectInvitation,
        ProjectRole,
        ProjectWiki,
        ProjectWikiAssignedUser,
    )
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
    from langboard_shared.domain.services import CardReminderSnooze
    from langboard_shared.domain.services.CardReminderPersistence import (
        card_allows_reminders,
        create_rule,
        materialize_due_occurrence,
        reschedule_rule,
    )
    from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule

    owner, participant, project, card, _, _, notifications = activity
    for model in (
        CardReminderRule, CardReminderOccurrence, Checklist, Checkitem,
        ProjectInvitation, ProjectWiki, ProjectWikiAssignedUser,
    ):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)

    with DbSession.atomic() as db:
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=participant.id))
        db.insert(ProjectRole(project_id=project.id, user_id=participant.id, actions=[ProjectRoleAction.Read.value]))

    active = ProjectColumn(id=0, project_id=project.id, name="Active")
    monkeypatch.setattr(notifications, "_NotificationService__get_column_by_card", lambda _: active)
    monkeypatch.setattr(CardReminderSnooze, "card_allows_reminders", lambda db, card: card_allows_reminders(db, card, active))
    monkeypatch.setattr(CardReminderSnooze.UserPublisher, "notification_mutated", lambda *_: None)

    now = SafeDateTime.now()
    schedule = ReminderSchedule("UTC", anchor=now, frequency="daily")
    rule = create_rule(card.id, owner.id, schedule, now - timedelta(minutes=1), is_shared=True)
    occurrence = materialize_due_occurrence(rule.id, rule.revision, now)
    assert occurrence is not None
    assert notifications.notify_card_reminder(owner, owner, project, card, occurrence) == 1
    assert notifications.notify_card_reminder(owner, participant, project, card, occurrence) == 1
    with DbSession.use(readonly=False) as db:
        original = db.exec(
            SqlBuilder.select.table(UserNotification).where(UserNotification.receiver_id == owner.id)
        ).first()
        peer = db.exec(
            SqlBuilder.select.table(UserNotification).where(UserNotification.receiver_id == participant.id)
        ).first()
        assert original.reminder_occurrence_id == occurrence.id
        uid = original.id

    channel = CollaborationChannel.HumanUI
    cards = current_card[3]
    repository = notifications.repo.user_notification

    def snooze(actor, minutes, request_uid, expected=None):
        return CardReminderSnooze.snooze_reminder(
            actor, uid, minutes, request_uid, expected,
            contexts=cards.resolve_work_visibility_contexts(actor, channel),
            repository=repository, now=now,
        )

    assert snooze(participant, 10, "another-recipient") is None
    receipt = snooze(owner, 10, "original")
    assert receipt == {"snoozed_until": (now + timedelta(minutes=10)).isoformat()}
    assert snooze(owner, 10, "original") == receipt
    with pytest.raises(CardReminderSnooze.SnoozeConflict):
        snooze(owner, 60, "original")
    with pytest.raises(CardReminderSnooze.SnoozeConflict):
        snooze(owner, 10, "stale")
    assert not notifications.get_inbox_events(owner, {uid}, channel=channel)
    assert notifications.get_inbox_events(participant, {peer.id}, channel=channel)
    assert CardReminderSnooze.wake_due_snoozes(cards, now + timedelta(minutes=9)) == 0
    assert CardReminderSnooze.wake_due_snoozes(cards, now + timedelta(minutes=10)) == 1
    assert CardReminderSnooze.wake_due_snoozes(cards, now + timedelta(minutes=10)) == 0
    assert notifications.get_inbox_events(owner, {uid}, channel=channel)
    assert snooze(owner, 10, "second") == receipt
    assert notifications.read_inbox_events(owner, {uid}, channel=channel) == 1
    assert CardReminderSnooze.wake_due_snoozes(cards, now + timedelta(minutes=10)) == 0
    with DbSession.use(readonly=False) as db:
        rows = {row.id: row for row in db.exec(SqlBuilder.select.table(UserNotification)).all()}
        assert set(rows) == {uid, peer.id}
        assert rows[uid].read_at is not None and rows[uid].reminder_snoozed_until is None
        assert rows[peer.id].read_at is None and rows[peer.id].reminder_snoozed_until is None

    assert snooze(owner, 10, "third") == receipt
    assert reschedule_rule(rule.id, rule.revision, schedule, now, is_runnable=True, enabled=False)
    assert CardReminderSnooze.wake_due_snoozes(cards, now + timedelta(minutes=10)) == 0
    assert notifications.get_inbox_events(owner, {uid}, channel=channel)[0]["read_at"] is not None
