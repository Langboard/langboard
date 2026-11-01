"""Native outbox history: actual SQL pagination and current middleware authority."""

import importlib
import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.settings import MailDeliveryHistoryApi
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Bot,
    Card,
    NotificationEmailDelivery,
    ProjectActivity,
    ProjectActivityEmailDelivery,
    User,
)
from langboard_shared.domain.models.MailDeliveryAttempt import MailDeliveryAttempt
from langboard_shared.domain.models.NotificationEmailDelivery import NotificationEmailDeliveryStatus as Status
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.MailDeliveryHistory import MailDeliveryHistory as History
from langboard_shared.Env import Env
from langboard_shared.helpers import MiddlewareHelper
from sqlalchemy import event


@pytest.fixture
def history_store(board):
    _, user, project, *_ = board
    engine = DbEngine.get_main_engine()
    NotificationEmailDelivery.__table__.create(engine)
    ProjectActivityEmailDelivery.__table__.create(engine)
    MailDeliveryAttempt.__table__.create(engine)
    # Detail resource resolution follows real native activity references.
    for model in (Card, Bot, ProjectActivity):
        model.__table__.create(engine, checkfirst=True)
    at = SafeDateTime(2026, 10, 25, 0, 0)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    with engine.begin() as connection:
        for source, model in History.SOURCES.items():
            if source == "smtp_attempt":
                continue
            for i, state in enumerate(Status):
                values = dict(
                    id=SnowflakeID(i + 10),
                    created_at=at - timedelta(days=i // 3),
                    recipient_email="secret-recipient@secret-domain.test",
                    status=state,
                    claimed_at=None if state == Status.Pending else at,
                    sent_at=at if state in (Status.Sent, Status.ConfirmedSent) else None,
                    failure_reason="535 secret-password token=secret-token",
                    review_note="secret-review",
                )
                if source == "notification":
                    values.update(
                        notification_id=SnowflakeID(i + 100),
                        receiver_id=user.id,
                        notification_type=NotificationType.MentionedInCard,
                        preferred_lang="en-US",
                        template_name="mentioned_in_card",
                        formats={"secret-body": "secret-token"},
                        scope_models=[["Project", int(project.id)]],
                    )
                else:
                    values.update(
                        project_id=project.id, activity_id=SnowflakeID(i + 200), activity_table="secret-arbitrary-table"
                    )
                connection.execute(model.__table__.insert().values(**values))
    statements.clear()
    yield engine, at, statements
    event.remove(engine, "before_cursor_execute", record)


def test_sql_cursor_order_filters_and_no_side_effects(history_store):
    _, _, statements = history_store
    all_items = []
    cursor = None
    while True:
        page = History.page(limit=2, cursor=cursor)
        all_items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(all_items) == 18
    assert len({item["key"] for item in all_items}) == 18
    assert len({item["uid"] for item in all_items}) == 9  # Same native IDs in different sources stay distinct.
    assert all_items[0]["source"] == "project_activity"
    assert all_items[1]["source"] == "project_activity"
    assert all_items[3]["source"] == "notification"
    assert all(statement.lstrip().upper().startswith("SELECT") for statement, _ in statements)
    assert all("LIMIT" in statement.upper() for statement, _ in statements)
    assert any("created_at <" in statement for statement, _ in statements)
    statements.clear()
    page = History.page(status="failed", created_from="2026-10-24T00:00:00Z", created_to="2026-10-25T00:00:00Z")
    assert len(page["items"]) == 2
    assert {item["status"] for item in page["items"]} == {"failed"}
    assert all(
        "status =" in statement and "created_at >=" in statement and "created_at <" in statement
        for statement, _ in statements
        if "GROUP BY" not in statement
    )


def test_safe_projection_and_honest_evidence(history_store):
    page = History.page()
    serialized = json.dumps(page)
    assert "secret" not in serialized
    assert "formats" not in serialized and "failure_reason" not in serialized and "review_note" not in serialized
    assert page["coverage"]["direct_email_paths"] == "recorded_after_migration"
    by_status = {item["status"]: item for item in page["items"]}
    assert by_status["pending"]["attempt_evidence"] == "not_attempted"
    assert by_status["sent"]["attempt_evidence"] == "smtp_accepted"
    assert by_status["confirmed_sent"]["attempt_evidence"] == "operator_confirmed"
    for item in page["items"]:
        assert item["subject"] is item["retry_history"] is item["linked_ids"] is None
        assert item["inbox_delivery"] == "unverified"
        assert History.detail(item["source"], item["uid"]) == item
        assert item["safe_error_code"] == "error_detail_unavailable"


@pytest.mark.parametrize(
    "filters",
    [
        {"limit": 0},
        {"limit": 101},
        {"status": "invented"},
        {"created_from": "2026-10-25"},
        {"created_from": "2026-10-26T00:00:00Z", "created_to": "2026-10-25T00:00:00Z"},
        {"cursor": "not-a-cursor"},
        {"cursor": "x" * 513},
    ],
)
def test_invalid_filters_rejected(history_store, filters):
    with pytest.raises(ValueError):
        History.page(**filters)


def test_cursor_bound_to_filters(history_store):
    cursor = History.page(limit=1)["next_cursor"]
    with pytest.raises(ValueError):
        History.page(cursor=cursor, status="sent")
    with pytest.raises(ValueError):
        History.page(cursor=cursor, created_from="2026-01-01T00:00:00Z")


def test_unknown_detail_is_uniform(history_store):
    for source, uid in [
        ("unknown", SnowflakeID(10).to_short_code()),
        ("notification", "bad"),
        ("notification", SnowflakeID(9999).to_short_code()),
        ("notification", "!!!!!!!!!!!"),
        ("notification", "zzzzzzzzzzz"),
        ("notification", "00000000000"),
        ("notification", "ZZZZZZZZZZZ"),
    ]:
        assert History.detail(source, uid) is None


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_current_native_admin_middleware_and_http_filters(history_store, monkeypatch):
    engine, at, _ = history_store
    with engine.begin() as connection:
        connection.execute(
            MailDeliveryAttempt.__table__.insert().values(
                id=SnowflakeID(1000),
                created_at=at,
                started_at=at,
                outcome="unknown",
                recipient_masked="***",
                trigger="direct",
            )
        )
    actor = User.model_construct(id=1, is_admin=True)

    def validate(scope, **kwargs):
        scope["auth"] = actor
        return actor

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    module = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(module, "DomainService", lambda: SimpleNamespace(close=lambda: None))
    assert any(route.endpoint is MailDeliveryHistoryApi.get_mail_deliveries for route in AppRouter.api.routes)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        response = client.get("/settings/mail/deliveries?limit=1")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        item = response.json()["items"][0]
        path = f"/settings/mail/deliveries/{item['source']}/{item['uid']}"
        assert client.get(path).json() == {**item, "resources": []}
        for query in ("limit=101", "limit=0", "cursor=" + "x" * 513):
            assert client.get("/settings/mail/deliveries?" + query).status_code == 400
        assert client.get("/settings/mail/deliveries?status=madeup").status_code == 400
        missing = client.get("/settings/mail/deliveries/unknown/bad")
        assert missing.status_code == 404
        for uid in ("zzzzzzzzzzz", "00000000000", "ZZZZZZZZZZZ", "bad"):
            response = client.get("/settings/mail/deliveries/notification/" + uid)
            assert response.status_code == 404
            assert response.json() == missing.json()
        attempts = client.get("/settings/mail/deliveries?view=attempts")
        assert attempts.status_code == 200
        attempt = attempts.json()["items"][0]
        attempt_path = f"/settings/mail/deliveries/smtp_attempt/{attempt['uid']}"
        assert client.get(attempt_path).json() == {**attempt, "resources": []}
        assert client.get("/settings/mail/deliveries?view=combined").status_code == 400
        actor.is_admin = False
        assert client.get("/settings/mail/deliveries?view=attempts").status_code == 403
        assert client.get(attempt_path).status_code == 403
        assert client.get("/settings/mail/deliveries").status_code == 403
        assert client.get(path).status_code == 403
        actor = 401
        assert client.get(attempt_path).status_code == 401
        assert client.get("/settings/mail/deliveries").status_code == 401
        assert client.get(path).status_code == 401


def test_allowlisted_reason_and_template_provenance(history_store):
    engine, _, _ = history_store
    with engine.begin() as connection:
        connection.execute(
            NotificationEmailDelivery.__table__.update()
            .where(NotificationEmailDelivery.__table__.c.id == 10)
            .values(
                template_name="secret-template-token",
                failure_reason="Delivery worker exited before recording an SMTP outcome",
            )
        )
    item = History.detail("notification", SnowflakeID(10).to_short_code())
    assert item["template"] is None
    assert item["template_provenance"] == "persisted"
    assert item["safe_error_code"] == "worker_outcome_unknown"
    assert "secret" not in json.dumps(item)
    activity = History.detail("project_activity", SnowflakeID(10).to_short_code())
    assert activity["template_provenance"] == "native_sender"


def test_date_offsets_and_malformed_cursor_shapes(history_store):
    import base64

    filters = {"created_from": "2026-10-24T00:00:00Z", "created_to": "2026-10-25T00:00:00Z"}
    page = History.page(limit=1, **filters)
    equivalent = History.page(limit=1, created_from="2026-10-24T09:00:00+09:00", created_to="2026-10-25T09:00:00+09:00")
    assert equivalent == page
    data = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    for value in (
        [],
        {},
        None,
        [1, data[1], data[2], 10, 1],
        [1, data[1], None, 0, 1],
        [1, data[1], {}, 0, 1],
        [True, data[1], data[2], 0, 1],
        [1, data[1], data[2], True, 1],
        [1, data[1], data[2], 0, -1],
    ):
        cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        with pytest.raises(ValueError):
            History.page(cursor=cursor, **filters)
    with pytest.raises(ValueError):
        History.page(created_from="9999-12-31T23:59:59-23:00")


def test_actual_attempt_view_is_bounded_separate_and_safe(history_store):
    engine, at, statements = history_store
    with engine.begin() as connection:
        for i, outcome in enumerate(("accepted", "rejected", "unknown", "accepted")):
            connection.execute(
                MailDeliveryAttempt.__table__.insert().values(
                    id=SnowflakeID(1000 + i),
                    created_at=at,
                    started_at=at,
                    completed_at=at if outcome != "unknown" else None,
                    outcome=outcome,
                    recipient_masked="***@***",
                    subject="Langboard SMTP test message",
                    trigger="smtp_test",
                    safe_error_code="smtp_rejected" if outcome == "rejected" else None,
                    source="notification",
                    source_uid=SnowflakeID(10).to_short_code(),
                    linked_ids={"card_id": 99, "secret": "secret-token"},
                )
            )
    statements.clear()
    page = History.page(view="attempts", limit=2)
    assert len(page["items"]) == 2
    assert len(statements) == 2 and all("LIMIT" in statement for statement, _ in statements)
    assert page["coverage"]["view"] == "attempts"
    more = History.page(view="attempts", limit=2, cursor=page["next_cursor"])
    assert more["next_cursor"] is None
    items = page["items"] + more["items"]
    assert len({item["key"] for item in items}) == 4
    assert {item["source"] for item in items} == {"smtp_attempt"}
    assert all(item["record_kind"] == "smtp_attempt" for item in items)
    assert all(item["recorded_attempt_count"] == 4 for item in items)
    assert all(item["outbox_key"] == f"notification:{SnowflakeID(10).to_short_code()}" for item in items)
    assert all(item["subject"] == "Langboard SMTP test message" for item in items)
    assert all(item["retry_history"] is None and item["linked_ids"] is None for item in items)
    assert "secret" not in json.dumps(items)
    for status, count in (("sent", 2), ("failed", 1), ("uncertain", 1), ("pending", 0), ("suppressed", 0)):
        filtered = History.page(
            view="attempts", status=status, created_from="2026-10-25T00:00:00Z", created_to="2026-10-26T00:00:00Z"
        )
        assert len(filtered["items"]) == count
        assert all(item["status"] == status for item in filtered["items"])
        assert all(item["recorded_attempt_count"] == 4 for item in filtered["items"])
    for item in items:
        assert History.detail("smtp_attempt", item["uid"]) == item
    assert len(History.page(view="legacy")["items"]) == 18
    with pytest.raises(ValueError):
        History.page(view="legacy", cursor=page["next_cursor"])
    with pytest.raises(ValueError):
        History.page(view="combined")
    with engine.begin() as connection:
        connection.execute(
            MailDeliveryAttempt.__table__.update().values(
                subject="secret-subject",
                safe_error_code="secret-code",
                recipient_masked="secret-email",
                trigger="secret-trigger",
                template_name="secret-template",
                source_uid="bad",
            )
        )
    unsafe = History.page(view="attempts")
    assert "secret" not in json.dumps(unsafe)
    assert all(item["subject"] is None and item["outbox_key"] is None for item in unsafe["items"])


@pytest.mark.parametrize("trigger", ["signup", "project_invitation", "direct", "smtp_test"])
def test_native_sender_trigger_survives_sql_projection(history_store, trigger, monkeypatch):
    from langboard_shared.domain.services.factory.EmailService import EmailService

    engine, at, _ = history_store
    if trigger in ("signup", "project_invitation"):
        monkeypatch.setattr(EmailService, "_EmailService__get_template", lambda *args: ("Private", "Private"))
        monkeypatch.setattr(type(Env), "MAIL_FROM", property(lambda _: "sender@example.invalid"))
        message = EmailService.__new__(EmailService).prepare_template_message(
            "en-US",
            "private@example.test",
            trigger,
            {
                "firstname": "Private",
                "lastname": "User",
                "email": "private@example.test",
                "url": "https://example.test",
                "project_name": "Private",
                "inviter_name": "Private",
            },
        )
        metadata = message._langboard_attempt_metadata
    else:
        metadata = {"trigger": trigger}
    with engine.begin() as connection:
        connection.execute(
            MailDeliveryAttempt.__table__.insert().values(
                id=SnowflakeID(2000),
                created_at=at,
                started_at=at,
                outcome="unknown",
                recipient_masked="***@***",
                **metadata,
            )
        )
    item = History.page(view="attempts")["items"][0]
    assert item["trigger"] == trigger
    assert History.detail("smtp_attempt", item["uid"])["trigger"] == trigger


def test_recorded_associations_never_infer_original_retry_intent(history_store):
    engine, at, statements = history_store
    uid = SnowflakeID(10).to_short_code()
    with engine.begin() as connection:
        for i, (source, source_uid) in enumerate(
            [
                ("notification", uid),
                ("notification", uid),
                ("project_activity", uid),
                (None, None),
                (None, None),
                ("notification", "bad"),
            ]
        ):
            connection.execute(
                MailDeliveryAttempt.__table__.insert().values(
                    id=SnowflakeID(2000 + i),
                    created_at=at,
                    started_at=at,
                    outcome="unknown",
                    trigger="direct",
                    recipient_masked="***",
                    source=source,
                    source_uid=source_uid,
                )
            )
    statements.clear()
    unassociated = History.page(view="attempts", limit=3)
    assert all(item["recorded_attempt_count"] is None for item in unassociated["items"])
    assert len(statements) == 1
    statements.clear()
    page = History.page(view="attempts", limit=4)
    assert len(page["items"]) == 4
    assert len(statements) == 2
    assert all("LIMIT" in statement.upper() for statement, _ in statements)
    assert "GROUP BY" in statements[-1][0].upper()
    items = History.page(view="attempts")["items"]
    for item in items:
        expected = {"notification": 2, "project_activity": 1}.get((item.get("outbox_key") or "").split(":")[0])
        assert item["recorded_attempt_count"] == expected
        assert item["retry_history"] is None
        assert History.detail("smtp_attempt", item["uid"]) == item
    assert History.detail("notification", uid)["recorded_attempt_count"] == 2
    assert History.detail("project_activity", uid)["recorded_attempt_count"] == 1
    unseen = History.detail("notification", SnowflakeID(11).to_short_code())
    assert unseen["recorded_attempt_count"] == 0
    assert unseen["retry_history"] is None
    statements.clear()
    assert len(History.page()["items"]) == 18
    assert len(statements) == 3
    assert all("LIMIT" in statement.upper() for statement, _ in statements)
