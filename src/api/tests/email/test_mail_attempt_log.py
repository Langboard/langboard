"""Durable native SMTP evidence, actual SQL migration and safe failure boundaries."""

import importlib.util
import json
from email.message import EmailMessage
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import MailDeliveryAttempt, NotificationEmailDelivery, ProjectActivityEmailDelivery
from langboard_shared.domain.models.NotificationEmailDelivery import NotificationEmailDeliveryStatus
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.factory.EmailService import EmailService
from langboard_shared.domain.services.MailDeliveryAttemptLog import MailDeliveryAttemptLog
from langboard_shared.Env import Env
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def smtp_configuration(monkeypatch):
    # These boundary tests own their prerequisites; never depend on shell secrets.
    for name, value in {"MAIL_SERVER": "127.0.0.1", "MAIL_PORT": 25, "MAIL_FROM": "sender@example.test"}.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, value=value: value))


@pytest.fixture
def mail_audit_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    migrate(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    yield engine
    engine.dispose()


def migrate(engine):
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261025040000-bdef890a1234.py"
    spec = importlib.util.spec_from_file_location("mail_attempt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "acde789f0123"
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    return migration


def attempts(engine):
    with Session(engine) as session:
        return session.scalars(select(MailDeliveryAttempt).order_by(MailDeliveryAttempt.column("id"))).all()


def message():
    result = EmailMessage()
    result["To"] = "secret-user+secret-password@example.test"
    result["Subject"] = "secret-password sensitive-server-content"
    result["X-Langboard-Trigger"] = "secret-password"
    result.set_content("credential-bearing-body")
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,outcome,code",
    [
        ("accepted", "accepted", None),
        ("refused", "rejected", "smtp_rejected"),
        ("unknown", "unknown", "timeout"),
    ],
)
async def test_actual_sql_boundaries(mail_audit_db, monkeypatch, mode, outcome, code):
    calls = []

    async def send(msg, **kwargs):
        rows = attempts(mail_audit_db)
        assert len(rows) == 1
        assert rows[0].outcome == "unknown" and rows[0].completed_at is None
        calls.append(msg)
        if mode == "unknown":
            raise TimeoutError("secret-password sensitive-server-content")
        return ({"sensitive-recipient": "sensitive-server-content"} if mode == "refused" else {}, "secret-password")

    monkeypatch.setattr("aiosmtplib.send", send)
    msg = message()
    if mode == "unknown":
        with pytest.raises(TimeoutError):
            await EmailService._send_native(msg)
    else:
        await EmailService._send_native(msg)
    row = attempts(mail_audit_db)[0]
    assert row.outcome == outcome and row.safe_error_code == code
    assert row.started_at <= row.completed_at
    assert row.recipient_masked == "***@***" and row.subject is None
    assert row.trigger == "direct"
    assert len(calls) == 1
    stored = json.dumps({key: str(getattr(row, key)) for key in row.model_fields})
    for secret in ("secret-user", "secret-password", "sensitive", "credential-bearing-body"):
        assert secret not in stored


@pytest.mark.asyncio
async def test_pre_send_storage_failure_prevents_smtp(mail_audit_db, monkeypatch):
    MailDeliveryAttempt.__table__.drop(mail_audit_db)

    async def forbidden(*args, **kwargs):
        pytest.fail("SMTP reached without committed start")

    monkeypatch.setattr("aiosmtplib.send", forbidden)
    result = await EmailService.send_test_message("recipient@example.test")
    assert result["smtp_send_acceptance"] == "failed"
    assert result["code"] == "audit_storage_unavailable"


@pytest.mark.asyncio
async def test_post_acceptance_storage_failure_preserves_success(mail_audit_db, monkeypatch):
    calls = []

    async def send(msg, **kwargs):
        calls.append(msg)
        return {}, "accepted"

    with mail_audit_db.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_mail_finish BEFORE UPDATE ON mail_delivery_attempt "
            "BEGIN SELECT RAISE(FAIL, 'secret-password'); END"
        )

    monkeypatch.setattr("aiosmtplib.send", send)
    result = await EmailService.send_test_message("recipient@example.test")
    assert result["smtp_send_acceptance"] == "accepted" and len(calls) == 1
    row = attempts(mail_audit_db)[0]
    assert row.outcome == "unknown" and row.completed_at is None


@pytest.mark.parametrize(
    "strict,environment,expected",
    [
        (True, "development", False),
        (False, "development", True),
        (False, "production", False),
    ],
)
def test_pre_send_storage_failure_legacy_boolean(mail_audit_db, monkeypatch, strict, environment, expected):
    MailDeliveryAttempt.__table__.drop(mail_audit_db)
    monkeypatch.setattr(type(Env), "ENVIRONMENT", property(lambda _: environment))
    service = EmailService(lambda *_: None, lambda *_: None, None)
    assert service.send_message(message(), strict=strict) is expected


def test_trusted_metadata_is_bounded_non_header(mail_audit_db, monkeypatch):
    service = EmailService(lambda *_: None, lambda *_: None, None)
    monkeypatch.setattr(EmailService, "_EmailService__get_template", lambda *args: ("secret-password", "secret-body"))
    monkeypatch.setattr(type(Env), "MAIL_FROM", property(lambda _: "sender@example.test"))
    msg = service.prepare_template_message("en-US", "secret@example.test", "signup", {"token": "secret"})
    EmailService.attach_attempt_metadata(
        msg,
        source="notification",
        source_uid=SnowflakeID(123).to_short_code(),
        linked_ids={"notification_id": 123, "password": "secret-password", "user_id": "secret-password"},
    )
    row = MailDeliveryAttemptLog.start(msg)
    assert row.subject == "Langboard: signup"
    assert row.template_name == row.trigger == "signup"
    assert row.source == "notification" and row.source_uid == SnowflakeID(123).to_short_code()
    assert row.linked_ids == {"notification_id": 123}
    assert "notification_id" not in msg.as_string()
    assert "_langboard_attempt_metadata" not in msg.as_string()


def test_additive_actual_migration_preserves_old_rows(board):
    _, user, project, *_ = board
    engine = DbEngine.get_main_engine()
    models = (NotificationEmailDelivery, ProjectActivityEmailDelivery)
    for model in models:
        model.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            NotificationEmailDelivery.__table__.insert().values(
                id=7,
                notification_id=8,
                receiver_id=user.id,
                notification_type=NotificationType.MentionedInCard,
                recipient_email="old@example.test",
                preferred_lang="en-US",
                template_name="mentioned_in_card",
                formats={"old": "secret-token"},
                status=NotificationEmailDeliveryStatus.Uncertain,
                failure_reason="old-reply",
                review_note="old-review",
            )
        )
        connection.execute(
            ProjectActivityEmailDelivery.__table__.insert().values(
                id=7,
                project_id=project.id,
                activity_table="project_activity",
                activity_id=9,
                recipient_email="old@example.test",
                status=NotificationEmailDeliveryStatus.Sent,
                failure_reason="old-reply",
                review_note="old-review",
            )
        )
        before = [connection.execute(select(model.__table__)).all() for model in models]
    migration = migrate(engine)
    with engine.begin() as connection:
        assert before == [connection.execute(select(model.__table__)).all() for model in models]
        assert connection.execute(text("SELECT count(*) FROM mail_delivery_attempt")).scalar() == 0
    # Native insert/update must work against the migrated schema on both DBs.
    row = MailDeliveryAttemptLog.start(message())
    MailDeliveryAttemptLog.finish(row, "accepted", None)
    assert attempts(engine)[0].outcome == "accepted"
    if engine.dialect.name == "postgresql":
        # An unrelated caller rollback must not erase committed SMTP evidence.
        with pytest.raises(RuntimeError, match="caller rollback"):
            with DbSession.atomic() as outer:
                outer.exec(
                    type(user).__table__.update().where(type(user).column("id") == user.id).values(firstname="Rollback")
                )
                independent = MailDeliveryAttemptLog.start(message())
                MailDeliveryAttemptLog.finish(independent, "accepted", None)
                raise RuntimeError("caller rollback")
        assert any(int(item.id) == int(independent.id) and item.outcome == "accepted" for item in attempts(engine))
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()


def test_post_acceptance_storage_failure_preserves_legacy_true(mail_audit_db, monkeypatch):
    calls = []

    async def send(msg, **kwargs):
        calls.append(msg)
        return {}, "accepted"

    with mail_audit_db.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_mail_finish BEFORE UPDATE ON mail_delivery_attempt "
            "BEGIN SELECT RAISE(FAIL, 'secret-password'); END"
        )

    monkeypatch.setattr("aiosmtplib.send", send)
    service = EmailService(lambda *_: None, lambda *_: None, None)
    assert service.send_message(message(), strict=True) is True
    assert len(calls) == 1
    assert attempts(mail_audit_db)[0].completed_at is None


def test_migration_chain_has_single_additive_head():
    from alembic.script import ScriptDirectory

    directory = Path(__file__).resolve().parents[2] / "langboard/migrations"
    scripts = ScriptDirectory(str(directory))
    assert scripts.get_heads() == ["defa012c3456"]
    assert scripts.get_revision("defa012c3456").down_revision == "cdef901b2345"
    assert scripts.get_revision("cdef901b2345").down_revision == "bdef890a1234"
    assert scripts.get_revision("bdef890a1234").down_revision == "acde789f0123"
