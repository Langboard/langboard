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
from langboard_shared.domain.models import NotificationEmailDelivery, ProjectActivityEmailDelivery, User
from langboard_shared.domain.models.NotificationEmailDelivery import NotificationEmailDeliveryStatus as Status
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services.MailDeliveryHistory import MailDeliveryHistory as History
from langboard_shared.helpers import MiddlewareHelper
from sqlalchemy import event


@pytest.fixture
def history_store(board):
    _, user, project, *_ = board
    engine = DbEngine.get_main_engine()
    NotificationEmailDelivery.__table__.create(engine)
    ProjectActivityEmailDelivery.__table__.create(engine)
    at = SafeDateTime(2026, 10, 25, 0, 0)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    with engine.begin() as connection:
        for source, model in History.SOURCES.items():
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
    )


def test_safe_projection_and_honest_evidence(history_store):
    page = History.page()
    serialized = json.dumps(page)
    assert "secret" not in serialized
    assert "formats" not in serialized and "failure_reason" not in serialized and "review_note" not in serialized
    assert page["coverage"]["direct_email_paths"] == "not_covered"
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
        item = response.json()["items"][0]
        path = f"/settings/mail/deliveries/{item['source']}/{item['uid']}"
        assert client.get(path).json() == item
        for query in ("limit=101", "limit=0", "cursor=" + "x" * 513):
            assert client.get("/settings/mail/deliveries?" + query).status_code == 400
        assert client.get("/settings/mail/deliveries?status=madeup").status_code == 400
        missing = client.get("/settings/mail/deliveries/unknown/bad")
        assert missing.status_code == 404
        for uid in ("zzzzzzzzzzz", "00000000000", "ZZZZZZZZZZZ", "bad"):
            response = client.get("/settings/mail/deliveries/notification/" + uid)
            assert response.status_code == 404
            assert response.json() == missing.json()
        actor.is_admin = False
        assert client.get("/settings/mail/deliveries").status_code == 403
        assert client.get(path).status_code == 403
        actor = 401
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
