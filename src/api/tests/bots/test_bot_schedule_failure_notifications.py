"""Native scheduler failures persist one authorized notification per occurrence."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock
import httpx
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    User,
    UserNotification,
    UserNotificationUnsubscription,
)
from langboard_shared.domain.models.CardBotLog import CardBotLog
from langboard_shared.domain.models.CardBotSchedule import CardBotSchedule
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.models.UserNotificationUnsubscription import NotificationChannel, NotificationScope
from langboard_shared.domain.services.factory.NotificationService import NotificationService
from langboard_shared.tasks.bots.utils.BotScheduleFailureNotification import (
    notify_schedule_failure,
    recover_schedule_failure_notifications,
)
from langboard_shared.tasks.bots.utils.BotScheduleRunStore import get_schedule_run, reserve_schedule_run
from sqlalchemy import delete, insert, update
from sqlalchemy.schema import CreateTable
from test_bot_schedule_recovery import runs, scheduler, transport_module
from test_bot_schedule_recovery import scheduled as schedule_storage  # noqa: F401


NATIVE_WEB_FANOUT = NotificationService._publish_web_notification
NATIVE_CONVERT = NotificationService.convert_to_api_response


@pytest.fixture
def notices(schedule_storage, monkeypatch):  # noqa: F811 - imported parametrized fixture
    from langboard_shared.Env import Env

    engine = DbEngine.get_main_engine()
    for model in (ProjectAssignedUser, ProjectRole, UserNotification, UserNotificationUnsubscription, Card, CardBotLog):
        with engine.begin() as connection:
            connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
            for index in model.__table__.indexes:
                index.create(connection)
    with DbSession.atomic() as db:
        owner = User(
            id=1,
            firstname="Owner",
            lastname="Test",
            email="owner@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.exec(insert(User).values(**{c.name: getattr(owner, c.name) for c in User.__table__.columns}))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    module = importlib.import_module("langboard_shared.domain.services.factory.NotificationService")
    monkeypatch.setattr(module, "publish_pending_work_events", lambda: None)
    # Keep real authorization, subscriptions, row insertion and after-commit queueing.
    fanout = Mock(return_value=True)
    monkeypatch.setattr(NotificationService, "convert_to_api_response", lambda *_args: {})
    monkeypatch.setattr(NotificationService, "_publish_web_notification", fanout)
    return schedule_storage, fanout


def notifications():
    with DbSession.atomic() as db:
        return db.exec(SqlBuilder.select.table(UserNotification)).all()


def transport(monkeypatch, status):
    calls = []

    async def handler(request):
        calls.append(request)
        if status == "timeout":
            raise httpx.ReadTimeout("secret provider payload", request=request)
        return httpx.Response(status, text="secret provider payload")

    client = httpx.AsyncClient
    monkeypatch.setattr(
        transport_module, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw)
    )
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error", [(400, "permanent_failure"), (503, "retry_limit_exceeded"), ("timeout", "admission_uncertain")]
)
async def test_terminal_notification_once_and_no_admission_replay(notices, monkeypatch, status, error):
    scheduled, fanout = notices
    reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now(), max_attempts=1)
    calls = transport(monkeypatch, status)
    await scheduler._run_scheduler(*scheduled)
    await scheduler._run_scheduler(*scheduled)
    await scheduler.recover_due_onetime_schedules()
    rows = notifications()
    assert len(rows) == len(calls) == fanout.call_count == 1
    row = rows[0]
    assert row.notification_type == NotificationType.BotScheduleFailed
    assert row.receiver_id == 1 and row.message_vars["error_code"] == error
    assert row.message_vars["occurrence_key"] == runs()[0].occurrence_key
    assert "secret" not in str(row.message_vars) and row.record_list == [["project", 3]]
    # A user's deletion cannot erase the occurrence's delivered receipt.
    with DbSession.atomic() as db:
        db.exec(delete(UserNotification))
    await scheduler._run_scheduler(*scheduled)
    assert notifications() == [] and len(calls) == 1


@pytest.mark.asyncio
async def test_notification_insert_failure_rolls_back_and_recovers_without_work(notices, monkeypatch):
    from langboard_shared.infrastructure.repositories.factory.UserNotificationRepository import (
        UserNotificationRepository,
    )

    scheduled, fanout = notices
    calls = transport(monkeypatch, 400)
    original = UserNotificationRepository.insert

    def fail(self, notification):
        original(self, notification)
        raise RuntimeError("storage failure after insert")

    monkeypatch.setattr(UserNotificationRepository, "insert", fail)
    await scheduler._run_scheduler(*scheduled)
    assert runs()[0].state == "failed" and notifications() == [] and fanout.call_count == 0
    monkeypatch.setattr(UserNotificationRepository, "insert", original)
    run = runs()[0]
    with DbSession.atomic() as db:
        run.next_attempt_at = SafeDateTime.now() - timedelta(seconds=1)
        db.update(run)
    await scheduler.recover_due_onetime_schedules()
    assert len(notifications()) == fanout.call_count == len(calls) == 1
    assert len(runs()) == 1 and runs()[0].attempt == 1


@pytest.mark.parametrize("denial", ["deleted", "inactive", "missing", "revoked", "private", "column_deleted"])
def test_denied_recipient_never_receives_source(notices, denial):
    scheduled, fanout = notices
    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        run.state = "failed"
        run.error_code = "permanent_failure"
        db.update(run)
        owner = db.exec(SqlBuilder.select.table(User).where(User.id == 1)).first()
        if denial in {"deleted", "inactive"}:
            if denial == "deleted":
                owner.deleted_at = SafeDateTime.now()
            else:
                owner.activated_at = None
            db.update(owner)
        elif denial == "missing":
            db.exec(delete(User).where(User.id == 1))
        elif denial == "revoked":
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 3)).first()
            project.owner_id = 99
            db.update(project)
        elif denial == "column_deleted":
            from langboard_shared.domain.models import ProjectColumn

            column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == 4)).first()
            column.deleted_at = SafeDateTime.now()
            db.update(column)
        else:
            card = Card(
                id=7,
                title="PRIVATE SECRET",
                project_id=3,
                project_column_id=4,
                created_by_user_id=1,
                owner_user_id=1,
                visibility="PRIVATE",
            )
            db.exec(insert(Card).values(**{c.name: getattr(card, c.name) for c in Card.__table__.columns}))
            association = CardBotSchedule(id=8, bot_schedule_id=scheduled[1].id, card_id=7)
            db.exec(
                insert(CardBotSchedule).values(
                    **{c.name: getattr(association, c.name) for c in CardBotSchedule.__table__.columns}
                )
            )
            from langboard_shared.tasks.bots.utils.BotScheduleRunStore import occurrence_key

            run.scope_table = association.__tablename__
            run.scope_id = association.id
            run.occurrence_key = occurrence_key(scheduled[1], association)
            db.update(run)
    assert notify_schedule_failure(run.id) is False
    assert notifications() == [] and fanout.call_count == 0


def test_parallel_recovery_has_one_durable_notification(notices):
    scheduled, fanout = notices
    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        run.state = "uncertain"
        run.error_code = "dispatch_lease_expired"
        db.update(run)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: notify_schedule_failure(run.id), range(8)))
    assert sum(results) == len(notifications()) == fanout.call_count == 1
    assert get_schedule_run(run.id).state == "uncertain" and get_schedule_run(run.id).attempt == 0


def test_unsubscription_retains_hidden_receipt_without_web_fanout(notices):
    scheduled, fanout = notices
    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        run.state = "failed"
        run.error_code = "retry_window_expired"
        db.update(run)
        db.insert(
            UserNotificationUnsubscription(
                user_id=1,
                channel=NotificationChannel.Web,
                notification_type=NotificationType.BotScheduleFailed,
                scope_type=NotificationScope.Specific,
                specific_table="project_column",
                specific_id=4,
            )
        )
    assert notify_schedule_failure(run.id)
    assert len(notifications()) == 1 and notifications()[0].web_visible is False and fanout.call_count == 0


def test_recovery_is_bounded_and_creates_no_first_occurrence(notices, monkeypatch):
    module = importlib.import_module("langboard_shared.tasks.bots.utils.BotScheduleFailureNotification")
    monkeypatch.setattr(module, "NOTIFICATION_RECOVERY_BATCH", 1)
    recover_schedule_failure_notifications()
    assert runs() == []
    scheduled, _ = notices
    first = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        first.state = "failed"
        db.update(first)
        from langboard_shared.domain.models.BotScheduleRun import BotScheduleRun

        db.insert(
            BotScheduleRun(
                schedule_id=99,
                scope_table="unknown",
                scope_id=99,
                occurrence_key="missing",
                state="failed",
                next_attempt_at=SafeDateTime.now() - timedelta(days=1),
            )
        )
    recover_schedule_failure_notifications()
    assert notifications() == []
    recover_schedule_failure_notifications()
    assert len(notifications()) == 1 and len(runs()) == 2


@pytest.mark.parametrize(
    "revocation", ["none", "inactive", "private", "unsubscribed", "owner_changed", "column_deleted"]
)
def test_pending_web_fanout_rechecks_current_authority(notices, monkeypatch, revocation):
    from langboard_shared.domain.services import DomainService
    from langboard_shared.publishers.UserPublisher import UserPublisher

    scheduled, _ = notices
    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        run.state = "failed"
        run.error_code = "permanent_failure"
        db.update(run)
    assert notify_schedule_failure(run.id)
    note = notifications()[0]
    with DbSession.atomic() as db:
        if revocation == "inactive":
            owner = db.exec(SqlBuilder.select.table(User).where(User.id == 1)).first()
            owner.activated_at = None
            db.update(owner)
        elif revocation == "owner_changed":
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 3)).first()
            project.owner_id = 99
            db.update(project)
        elif revocation == "private":
            card = Card(
                project_id=3,
                project_column_id=4,
                title="Private source",
                visibility="PRIVATE",
                owner_user_id=1,
                created_by_user_id=1,
            )
            db.insert(card)
            note.record_list = [["project", 3], ["card", int(card.id)]]
            db.update(note)
        elif revocation == "column_deleted":
            from langboard_shared.domain.models import ProjectColumn

            column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == 4)).first()
            column.deleted_at = SafeDateTime.now()
            db.update(column)
        elif revocation == "unsubscribed":
            db.insert(
                UserNotificationUnsubscription(
                    user_id=1,
                    channel=NotificationChannel.Web,
                    notification_type=NotificationType.BotScheduleFailed,
                    scope_type=NotificationScope.Specific,
                    specific_table="project_column",
                    specific_id=4,
                )
            )
    with DbSession.atomic() as db:
        db.exec(
            update(UserNotification)
            .where(UserNotification.id == note.id)
            .values(updated_at=SafeDateTime.now() - timedelta(minutes=1))
        )
    published = Mock()
    monkeypatch.setattr(NotificationService, "_publish_web_notification", NATIVE_WEB_FANOUT)
    monkeypatch.setattr(NotificationService, "convert_to_api_response", NATIVE_CONVERT)
    monkeypatch.setattr(UserPublisher, "notified", published)
    with DomainService.use() as service:
        assert service.notification.recover_pending_web_fanout() == 1
    assert published.call_count == int(revocation == "none")
    assert len(notifications()) == 1 and notifications()[0].web_fanout_pending is False


def test_whisper_references_and_unknown_error_content_are_not_emitted(notices):
    from langboard_shared.domain.models.WhisperThread import WhisperThread
    from langboard_shared.domain.services import DomainService

    scheduled, fanout = notices
    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        run.state = "uncertain"
        run.error_code = "SECRET_TOKEN"
        db.update(run)
    assert notify_schedule_failure(run.id)
    assert notifications()[0].message_vars["error_code"] == "unknown_failure"
    with DomainService.use() as service:
        with DbSession.atomic() as db:
            owner = db.exec(SqlBuilder.select.table(User).where(User.id == 1)).first()
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 3)).first()
        assert (
            service.notification._resolve_notification_recipient(
                owner,
                NotificationType.BotScheduleFailed,
                [project, WhisperThread(project_id=3, card_id=7, creator_id=1)],
            )
            is None
        )
    assert len(notifications()) == fanout.call_count == 1


def test_postcommit_web_failure_keeps_one_durable_row_for_native_recovery(notices, monkeypatch):
    from langboard_shared.domain.services import DomainService
    from langboard_shared.publishers.UserPublisher import UserPublisher

    scheduled, _ = notices
    run = reserve_schedule_run(scheduled[1], scheduled[2], now=SafeDateTime.now())
    with DbSession.atomic() as db:
        run.state = "failed"
        run.error_code = "permanent_failure"
        db.update(run)
    monkeypatch.setattr(NotificationService, "_publish_web_notification", NATIVE_WEB_FANOUT)
    monkeypatch.setattr(UserPublisher, "notified", Mock(side_effect=RuntimeError("socket unavailable")))
    assert notify_schedule_failure(run.id)
    note = notifications()[0]
    assert note.web_fanout_pending is True
    assert notify_schedule_failure(run.id) is False
    with DbSession.atomic() as db:
        db.exec(
            update(UserNotification)
            .where(UserNotification.id == note.id)
            .values(updated_at=SafeDateTime.now() - timedelta(minutes=1))
        )
    published = Mock()
    monkeypatch.setattr(UserPublisher, "notified", published)
    with DomainService.use() as service:
        assert service.notification.recover_pending_web_fanout() == 1
    assert len(notifications()) == published.call_count == 1
    assert notifications()[0].web_fanout_pending is False
