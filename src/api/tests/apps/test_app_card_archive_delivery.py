# ruff: noqa: F811
"""Archive consent, signed delivery and current grants are independent of runtime start."""

import hashlib
import hmac
import json
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security import KeyVault
from langboard_shared.domain.models import (
    AppCardArchive,
    AppExecutionOutbox,
    BoardAppBinding,
    Card,
    ProjectColumn,
    WebhookSetting,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardArchive import configure_archive_sync
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, finish_app_event
from langboard_shared.domain.services.AppEventDestination import APP_CARD_ARCHIVE_EVENT, bind_app_event_destination
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_card_creation import call, creation  # noqa: F401


def setup_archive(creation, monkeypatch, enabled=True):
    board, _, connection, binding, resource, definition, _ = creation
    queues = []
    from langboard_shared.domain.services import AppEventDelivery

    monkeypatch.setattr(AppEventDelivery, "enqueue_event", queues.append)
    monkeypatch.setattr(KeyVault, "get_key", staticmethod(lambda _: "test-signing-secret"))
    monkeypatch.setattr(CardService, "notify_order_changed", lambda *args: None)
    with DbSession.atomic() as db:
        board[1].is_admin = True
        db.update(board[1])
        definition.declaration = {
            **definition.declaration,
            "capabilities": [*definition.declaration["capabilities"], "events.receive"],
            "trust": {"authentication_origins": [], "mcp_origins": [], "data_origins": ["https://app.example"]},
            "card_lifecycle": {"issue": {"archive": {"mode": "external", "action": "resolve_issue",
                               "default_enabled": False, "description": "Resolve linked issue after explicit consent."},
                               "restore": "retain_external_state"}},
        }
        db.update(definition)
        binding.granted_capabilities = [*binding.granted_capabilities, "events.receive"]
        db.update(binding)
        webhook = WebhookSetting(name="App archive", url="https://app.example/events", secret_id="test-secret",
                                 events=[APP_CARD_ARCHIVE_EVENT])
        db.insert(webhook)
        archive = ProjectColumn(project_id=board[2].id, name="Archive", is_archive=True, order=20)
        db.insert(archive)
    # Creating from the current app credentials is needed after trust changes.
    from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential

    token = issue_connection_credential(board[1], connection.id)["token"]
    current = (board, token, connection, binding, resource, definition, creation[6])
    call(current)
    bind_app_event_destination(board[1], connection.id, webhook.id, None)
    service = DomainService()
    try:
        if enabled:
            saved = configure_archive_sync(service, board[1], board[2].id, connection.app_key,
                                           binding.edit_revision(), ["issue"])
            assert saved["archive_sync_types"] == ["issue"]
        with DbSession.atomic() as db:
            card = db.exec(SqlBuilder.select.table(Card).where(Card.title == "ERP issue")).first()
        assert service.card.change_order(board[1], board[2], card, 0, archive)
    finally:
        service.close()
    return card, queues, webhook


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_archive_signed_delivery_does_not_resume_or_apply_external_change(creation, monkeypatch):
    card, queues, _ = setup_archive(creation, monkeypatch)
    with DbSession.atomic() as db:
        row = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
        assert row.receipt["state"] == "pending" and row.receipt["external_action"] == "resolve_issue"
        assert row.receipt["external_state"] is None
        assert queues == [event.id]
        archived = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        archive = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == archived.project_column_id)).first()
    service = DomainService()
    try:
        assert service.card.change_order(creation[0][1], creation[0][2], card, 0, archive)
    finally:
        service.close()
    # Archive reorder retains the same incarnation and pending external operation.
    delivery = claim_app_event(event.id, 1)
    assert delivery is not None
    body = json.loads(delivery["body"])
    assert body["card_uid"] == card.get_uid() and body["archive_uid"] == row.get_uid()
    assert body["action"] == "resolve_issue" and body["external_resource_id"] == "issue-1"
    timestamp = delivery["headers"]["X-Langboard-Webhook-Timestamp"]
    signature = hmac.new(b"test-signing-secret", timestamp.encode() + b"." + delivery["body"], hashlib.sha256).hexdigest()
    assert delivery["headers"]["X-Langboard-Webhook-Signature"] == "v1=" + signature
    assert claim_app_event(event.id, 1) is None
    finish_app_event(event.id, delivery["claim_token"], delivered=True)
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        assert current.receipt["state"] == "pending" and current.receipt["external_state"] is None
        assert db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first().state == "delivered"


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("first_state,second_state", [("failed", "unknown"), ("unknown", "applied")])
@pytest.mark.parametrize("delivery_lock_first", [False, True])
def test_concurrent_archive_outcomes_recheck_revision_after_actual_row_lock(
    creation, monkeypatch, first_state, second_state, delivery_lock_first
):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.services.AppCardArchive import report_archive_outcome
    from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
    from sqlalchemy import func, select, text

    setup_archive(creation, monkeypatch)
    board, _, connection, *_ = creation
    token = issue_connection_credential(board[1], connection.id)["token"]
    with DbSession.atomic() as db:
        row = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
    assert claim_app_event(event.id, 1) is not None
    first_outcome = {"state": first_state, "external_state": None, "readback_confirmed": False}
    second_outcome = {
        "state": second_state, "external_state": "resolved" if second_state == "applied" else None,
        "readback_confirmed": second_state == "applied",
    }
    locked, release, contender_ready = Event(), Event(), Event()
    contender_pid = []

    def winner():
        with DbSession.atomic() as db:
            if delivery_lock_first:
                # Delivery owns outbox first, then revalidates archive authority.
                db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(
                    AppExecutionOutbox.id == event.id
                ).with_for_update()).first()
            else:
                receipt = report_archive_outcome(token, board[2].id, row.id, 0, first_outcome)
            locked.set()
            assert release.wait(15), "Test did not release archive transaction"
            if delivery_lock_first:
                receipt = report_archive_outcome(token, board[2].id, row.id, 0, first_outcome)
            return receipt

    def contender():
        with DbSession.atomic() as db:
            contender_pid.append(db.exec(select(func.pg_backend_pid())).first()[0])
            contender_ready.set()
            return report_archive_outcome(token, board[2].id, row.id, 0, second_outcome)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(winner)
        assert locked.wait(10)
        second = pool.submit(contender)
        try:
            assert contender_ready.wait(10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with DbEngine.get_main_engine().connect() as db:
                    waiting = db.execute(text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                    ), {"pid": contender_pid[0]}).scalar_one()
                if waiting == "Lock":
                    break
                if second.done():
                    pytest.fail("Archive report did not wait on the concurrent transaction")
                time.sleep(0.02)
            else:
                pytest.fail("No PostgreSQL archive lock waiter observed")
        finally:
            release.set()
        assert first.result(timeout=10)["changed"] is True
        with pytest.raises(AppGovernanceConflict):
            second.result(timeout=10)
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(AppCardArchive).where(AppCardArchive.id == row.id)).first()
        assert saved.receipt["outcome_revision"] == 1
        assert saved.receipt["provider_report"] == first_outcome
    assert report_archive_outcome(token, board[2].id, row.id, 0, first_outcome)["changed"] is False


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_external_archive_defaults_off_and_stale_config_denied(creation, monkeypatch):
    _, queues, _ = setup_archive(creation, monkeypatch, enabled=False)
    board, _, connection, binding, *_ = creation
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppCardArchive)).first().receipt["state"] == "retained"
        assert db.exec(SqlBuilder.select.table(AppExecutionOutbox)).all() == []
    assert queues == []
    service = DomainService()
    try:
        with pytest.raises(AppGovernanceConflict):
            configure_archive_sync(service, board[1], board[2].id, connection.app_key, "0" * 64, ["issue"])
        with pytest.raises(ValueError, match="explicit external"):
            configure_archive_sync(service, board[1], board[2].id, connection.app_key, binding.edit_revision(), ["undeclared"])
    finally:
        service.close()


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("change", ["consent", "reenable", "disable_reenable", "resource", "connection", "definition", "restore", "ownership", "payload", "destination"])
def test_archive_pending_delivery_rechecks_current_authority(creation, monkeypatch, change):
    card, _, _ = setup_archive(creation, monkeypatch)
    board, _, connection, binding, resource, definition, _ = creation
    with DbSession.atomic() as db:
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
        if change in ("consent", "reenable"):
            binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
            binding.archive_sync_types = []
            db.update(binding)
        elif change == "resource":
            resource.access_state = "revoked"
            db.update(resource)
        elif change == "connection":
            connection.state = "revoked"
            db.update(connection)
        elif change == "definition":
            definition.is_enabled = False
            db.update(definition)
        elif change == "restore":
            card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
            card.archived_at = None
            db.update(card)
        elif change == "ownership":
            from langboard_shared.domain.models import CardAppOwnership
            owner = db.exec(SqlBuilder.select.table(CardAppOwnership)).first()
            owner.revision += 1
            db.update(owner)
        elif change == "payload":
            event.payload = {**event.payload, "action": "delete_issue"}
            db.update(event)
        elif change == "destination":
            from langboard_shared.domain.models import AppEventDestination
            destination = db.exec(SqlBuilder.select.table(AppEventDestination)).first()
            destination.revision += 1
            db.update(destination)
    if change == "destination":
        from langboard_shared.domain.services.AppCardArchive import read_archive_outcome
        from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
        from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict
        token = issue_connection_credential(board[1], connection.id)["token"]
        with DbSession.atomic() as db:
            row = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        with pytest.raises(AppGovernanceConflict):
            read_archive_outcome(token, board[2].id, row.id)
    if change in ("reenable", "disable_reenable"):
        service = DomainService()
        try:
            if change == "disable_reenable":
                with DbSession.atomic() as db:
                    binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
                disabled = service.workflow_stage.disable_app_binding(board[1], board[2].get_uid(), connection.app_key,
                                                                      binding.get_uid(), binding.edit_revision())
                assert disabled.archive_sync_types == []
                assert disabled.archive_sync_revision == binding.archive_sync_revision + 1
                with DbSession.atomic() as db:
                    current = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
                    current.state = "enabled"
                    current.granted_capabilities = binding.granted_capabilities
                    db.update(current)
                    binding = current
            saved = configure_archive_sync(service, board[1], board[2].id, connection.app_key,
                                           binding.edit_revision(), [])
            configure_archive_sync(service, board[1], board[2].id, connection.app_key, saved["revision"], ["issue"])
        finally:
            service.close()
    assert claim_app_event(event.id, 1) is None
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first().state == "blocked"


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_public_archive_outcome_requires_readback_and_current_authority(creation, monkeypatch):
    import langboard.routes.settings.AppCardArchiveOutcomeApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential

    setup_archive(creation, monkeypatch)
    board, _, connection, binding, *_ = creation
    token = issue_connection_credential(board[1], connection.id)["token"]
    with DbSession.atomic() as db:
        row = db.exec(SqlBuilder.select.table(AppCardArchive)).first()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first()
    claimed = claim_app_event(event.id, 1)
    app = FastAPI()
    app.include_router(AppRouter.api)
    url = f"/apps/v1/boards/{board[2].get_uid()}/archives/{row.get_uid()}/outcome"
    headers = {"Authorization": f"Bearer {token}"}
    async def sdk_read():
        from httpx import ASGITransport, AsyncClient
        from langboard_sdk import AppCards, HttpTransport
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as session:
            return await AppCards(HttpTransport(session)).read_archive_outcome(board[2].get_uid(), row.get_uid())
    async def sdk_report(expected_revision, state, external_state, readback_confirmed):
        from httpx import ASGITransport, AsyncClient
        from langboard_sdk import AppCards, HttpTransport
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as session:
            return await AppCards(HttpTransport(session)).report_archive_outcome(
                board[2].get_uid(), row.get_uid(), expected_revision=expected_revision,
                state=state, external_state=external_state, readback_confirmed=readback_confirmed)
    import asyncio
    with TestClient(app) as client:
        read_url = url.removesuffix("/outcome")
        assert client.get(read_url).status_code == 401
        receipt = client.get(read_url, headers=headers)
        assert receipt.status_code == 200 and receipt.headers["cache-control"] == "no-store"
        assert receipt.json()["receipt"]["outcome_revision"] == 0
        assert receipt.json()["delivery"]["state"] == "delivering"
        wrong_board = read_url.replace(board[2].get_uid(), "00000000002")
        assert client.get(wrong_board, headers=headers).status_code == 403
        body = {"expected_revision": 0, "outcome": {"state": "applied", "external_state": "resolved", "readback_confirmed": False}}
        assert client.post(url, json=body, headers=headers).status_code == 400
        assert client.post(url, json=body).status_code == 401
        assert asyncio.run(sdk_report(0, "unknown", None, False))["receipt"]["state"] == "unknown"
        receipt = asyncio.run(sdk_read())
        assert receipt["receipt"]["outcome_revision"] == 1
        assert receipt["receipt"]["state"] == "unknown"
        finish_app_event(event.id, claimed["claim_token"], delivered=False)
        assert claim_app_event(event.id, 1) is None
        with DbSession.atomic() as db:
            assert db.exec(SqlBuilder.select.table(AppExecutionOutbox)).first().state == "blocked"
        body["expected_revision"] = 1
        body["outcome"]["readback_confirmed"] = True
        response = asyncio.run(sdk_report(1, "applied", "resolved", True))
        assert response["receipt"]["state"] == "applied"
        assert response["receipt"]["outcome_source"] == "authenticated_app_report"
        assert asyncio.run(sdk_read())["receipt"]["outcome_revision"] == 2
        assert asyncio.run(sdk_report(1, "applied", "resolved", True))["changed"] is False
        body["outcome"]["external_state"] = "unresolved"
        assert client.post(url, json=body, headers=headers).status_code == 409
        assert client.get(read_url, headers=headers).status_code == 200

        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
            current.archive_sync_types = []
            db.update(current)
        body["outcome"]["external_state"] = "resolved"
        assert client.post(url, json=body, headers=headers).status_code == 409
        assert client.get(read_url, headers=headers).status_code == 409
        from langboard_sdk import NativeApiError
        with pytest.raises(NativeApiError) as denied:
            asyncio.run(sdk_read())
        assert denied.value.status_code == 409

@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_public_consent_and_receipts_do_not_retroactively_queue_old_archive(creation, monkeypatch):
    import langboard.routes.board.CardAppArchiveApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    card, queues, _ = setup_archive(creation, monkeypatch, enabled=False)
    board = creation[0]
    with DbSession.atomic() as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(board[1].id)
    headers = {"Authorization": f"Bearer {access}"}
    async def sdk_configure():
        import importlib.metadata
        import json
        from pathlib import Path
        from httpx import ASGITransport, AsyncClient
        from langboard_sdk import AppManager, HttpTransport
        provenance = json.loads((Path(__file__).resolve().parents[3] / "sdk/vendor/provenance.json").read_text())
        assert importlib.metadata.version("langboard-sdk") == provenance["packages"]["langboard-sdk"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers,
                               cookies={Env.REFRESH_TOKEN_NAME: refresh}) as session:
            return await AppManager(HttpTransport(session), board[2].get_uid()).set_archive_sync(
                binding.app_key, binding.edit_revision(), ["issue"])
    import asyncio
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        url = f"/board/{board[2].get_uid()}/apps/{binding.app_key}/archive-sync"
        body = {"expected_revision": binding.edit_revision(), "card_types": ["issue"]}
        response = asyncio.run(sdk_configure())
        assert response["archive_sync_types"] == ["issue"]
        assert client.put(url, json=body, headers=headers).status_code == 409
        receipts = client.get(f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/archives", headers=headers)
        assert receipts.status_code == 200
        assert receipts.headers["cache-control"] == "no-store"
        assert receipts.json()["items"][0]["receipt"]["state"] == "retained"
        from langboard_shared.core.types import SnowflakeID
        other_access, other_refresh = AuthSecurity.authenticate(SnowflakeID(2))
        client.cookies.set(Env.REFRESH_TOKEN_NAME, other_refresh)
        body["expected_revision"] = response["revision"]
        assert client.put(url, json=body, headers={"Authorization": f"Bearer {other_access}"}).status_code == 403
    assert queues == []
