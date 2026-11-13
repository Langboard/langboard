# ruff: noqa: F811
"""Native SQL/HTTP/concurrency acceptance. Requires the normal managed API test runtime.

Metadata creation below prepares an isolated fixture; it is not migration proof.
Only storage and broker/signing seams are stubbed where a test explicitly says so.
"""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from inspect import signature
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from langboard.routes.board import AppTransferApi
from langboard.routes.settings import AppTransferReceiptApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import KeyVault
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppEventDestination, AppExecutionOutbox, AppExecutionRequest, CardAppOwnership,
    AppTransferRequest, Card, CardAttachment, WebhookSetting,
)
from langboard_shared.domain.services import AppEventDelivery, AppTransfers
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.domain.services.AppEventDelivery import claim_app_event, finish_app_event
from langboard_shared.domain.services.AppEventDestination import APP_TRANSFER_EVENT, bind_app_event_destination
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.Env import Env
from test_card_app_resources import prepare


def setup_transfer(board, monkeypatch):
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    connection, definition, binding, resources, card = prepare(board)
    engine = DbEngine.get_main_engine()
    for model in (WebhookSetting, AppEventDestination, AppExecutionOutbox, AppExecutionRequest, CardAppOwnership, AppTransferRequest, CardAttachment):
        model.__table__.create(engine, checkfirst=True)
    with engine.begin() as db:
        db.execute(text("CREATE TABLE card_execution_generation (card_id BIGINT PRIMARY KEY, execution_generation INTEGER NOT NULL)"))
        db.execute(text("INSERT INTO card_execution_generation VALUES (:card, 7)"), {"card": int(card.id)})
    with DbSession.atomic() as db:
        board[1].is_admin = True
        db.update(board[1])
        required = ["transfers.receive", "cards.read", "resources.read", "events.receive"]
        definition.declaration = {**definition.declaration, "capabilities": required,
                                  "trust": {"authentication_origins": [], "mcp_origins": [], "data_origins": ["https://app.example"]}}
        binding.granted_capabilities = required
        db.update(definition);db.update(binding)
        setting = WebhookSetting(name="Transfer test", url="https://app.example/events", secret_id="test-secret", events=[APP_TRANSFER_EVENT])
        db.insert(setting)
    bind_app_event_destination(board[1], connection.id, setting.id, None)
    token = issue_connection_credential(board[1], connection.id)["token"]
    queued = []
    monkeypatch.setattr(AppEventDelivery, "enqueue_event", queued.append)
    monkeypatch.setattr(KeyVault, "get_key", lambda _: "test-secret")
    review = AppTransfers.review_transfer(board[1], board[2].id, card.id, connection.app_key)
    target = next(row for row in review["targets"] if row["resource_uid"] == resources[0].get_uid())
    intent = {**review["intent"], "resource_uid": target["resource_uid"]}
    return connection, definition, binding, resources[0], card, token, target, intent, queued


def create(board, scope, request=None):
    connection, _, _, _, card, _, target, intent, _ = scope
    return AppTransfers.create_transfer(board[1], board[2].id, card.id, connection.app_key,
                                       request or str(uuid4()), connection.id, intent, target["authority"])


def client_for(board):
    app = FastAPI()
    app.include_router(AppRouter.api)
    # Match existing native tests: exercise real route forms and service ACLs while
    # substituting only the already-authenticated human dependency.
    for endpoint in (AppTransferApi.sender_transfers, AppTransferApi.sender_transfer,
                     AppTransferApi.sender_transfer_retry, AppTransferApi.transfer_create, AppTransferApi.transfer_review):
        app.dependency_overrides[signature(endpoint).parameters["user"].default.dependency] = lambda: board[1]
    return TestClient(app)


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_durable_sender_http_recovery_and_app_receipts_do_not_start_execution(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    connection, _, _, _, card, token, target, intent, queued = scope
    request = str(uuid4())
    body = {"client_request_id": request, "connection_uid": connection.get_uid(), "intent": intent, "expected_authority": target["authority"]}
    with client_for(board) as client:
        path = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/{connection.app_key}/transfers"
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        first = response.json()
        replay = client.post(path, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["transfer_uid"] == first["transfer_uid"]
        # No browser UUID is supplied to the list: recovery comes from durable rows.
        history = client.get(f"/board/{board[2].get_uid()}/apps/{connection.app_key}/transfers", params={"limit": 1})
        assert history.status_code == 200, history.text
        assert history.json()["items"][0]["client_request_id"] == request
        assert history.headers["cache-control"] == "no-store"
        lookup = client.get(f"/board/{board[2].get_uid()}/app-transfers/by-request/{request}")
        assert lookup.json()["transfer_uid"] == first["transfer_uid"]
        app_path = f"/apps/v1/boards/{board[2].get_uid()}/transfers/{first['transfer_uid']}"
        assert client.get(app_path).status_code == 401
        assert client.get(app_path, headers={"Authorization": "Bearer invalid"}).status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(app_path, headers=headers).status_code == 200
        event_id = SnowflakeID.from_short_code(first["delivery"]["event_uid"])
        claim = claim_app_event(event_id, 1)
        assert claim is not None
        finish_app_event(event_id, claim["claim_token"], delivered=True)
        receipt = {"expected_revision": 0, "acknowledgment": "received", "provider_outcome": "unknown"}
        reported = client.post(app_path + "/receipt", json=receipt, headers=headers)
        assert reported.status_code == 200, reported.text
        assert reported.json()["receipt"]["provider_outcome"] == "unknown"
        assert reported.json()["delivery"]["state"] == "delivered"
        assert client.post(app_path + "/receipt", json=receipt, headers=headers).json()["changed"] is False
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(AppTransferRequest)).all()) == 1
        assert len(db.exec(SqlBuilder.select.table(AppExecutionOutbox)).all()) == 1
        assert db.exec(SqlBuilder.select.table(AppExecutionRequest)).all() == []
        assert db.exec(SqlBuilder.select.table(CardAppOwnership)).all() == []
        assert len(queued) == 1
    with DbEngine.get_main_engine().connect() as connection:
        assert connection.execute(text("SELECT execution_generation FROM card_execution_generation")).scalar_one() == 7


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_outbox_visibility_filtered_before_count_and_cursor(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    connection, _, _, _, card, *_ = scope
    visible = create(board, scope)
    with DbSession.atomic() as db:
        hidden = Card(project_id=board[2].id, project_column_id=card.project_column_id,
                      title="Hidden current identity", visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
        db.insert(hidden)
        hidden_request = AppTransferRequest(actor_id=board[1].id, client_request_id=str(uuid4()), project_id=board[2].id,
                                           card_id=hidden.id, app_key=connection.app_key, connection_id=connection.id,
                                           intent={}, authority={}, receipt={})
        db.insert(hidden_request)
        db.insert(AppExecutionOutbox(request_id=hidden_request.id, project_id=board[2].id, connection_id=connection.id,
                                     app_key=connection.app_key, event_type=APP_TRANSFER_EVENT))
    result = AppTransfers.list_sender_transfers(board[1], board[2].id, connection.app_key, limit=1)
    assert [item["transfer_uid"] for item in result["items"]] == [visible["transfer_uid"]]
    assert result["next_cursor"] is None
    assert hidden.get_uid() not in str(result)
    assert hidden_request.get_uid() not in str(result)
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        current.deleted_at = SafeDateTime.now();db.update(current)
    assert AppTransfers.list_sender_transfers(board[1], board[2].id, connection.app_key, limit=1) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_concurrent_create_and_retry_have_one_identity_one_budget_and_no_history_reset(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    request = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create(board, scope, request), range(4)))
    assert len({item["transfer_uid"] for item in results}) == 1
    assert sum(item["changed"] for item in results) == 1
    event_id = SnowflakeID.from_short_code(results[0]["delivery"]["event_uid"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: claim_app_event(event_id, 1), range(4)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    finish_app_event(event_id, winners[0]["claim_token"], delivered=False)
    for _ in range(3):
        claim = claim_app_event(event_id, 1)
        assert claim is not None
        finish_app_event(event_id, claim["claim_token"], delivered=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        retried = list(pool.map(lambda _: AppTransfers.retry_transfer(board[1], board[2].id, request, 0), range(4)))
    assert sum(item["changed"] for item in retried) == 1
    assert all(item["delivery"]["attempt_count"] == 4 for item in retried)
    assert all(len(item["delivery"]["history"]) == 8 for item in retried)
    claim = claim_app_event(event_id, 1)
    assert claim["attempt_count"] == 5
    assert claim["event_uid"] == results[0]["delivery"]["event_uid"]


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("change", ["connection", "resource", "capability", "card_acl", "attachment"])
def test_current_revocation_blocks_delivery_and_explicit_retry(board, monkeypatch, change):
    scope = setup_transfer(board, monkeypatch)
    connection, _, binding, resource, card, *_ = scope
    result = create(board, scope)
    event_id = SnowflakeID.from_short_code(result["delivery"]["event_uid"])
    with DbSession.atomic() as db:
        if change == "connection": connection.state = "disconnected";db.update(connection)
        elif change == "resource": resource.access_state = "revoked";resource.access_revision += 1;db.update(resource)
        elif change == "capability": binding.granted_capabilities = ["cards.read"];db.update(binding)
        elif change == "card_acl": card.visibility = "PRIVATE";card.owner_user_id = 2;card.created_by_user_id = 2;db.update(card)
        else: card.last_change_seq += 1;db.update(card)
    assert claim_app_event(event_id, 1) is None
    with pytest.raises((AppGovernanceDenied, AppGovernanceConflict)):
        AppTransfers.retry_transfer(board[1], board[2].id, result["client_request_id"], 0)


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_attachment_http_streams_native_configured_limit_and_closes_after_send(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    connection, _, _, resource, card, token, *_ = scope
    # More than the removed 16 MiB subset; memory still rolls to a temporary file.
    monkeypatch.setattr(type(Env), "MAX_FILE_SIZE_MB", property(lambda _: 20))
    chunk = b"x" * (64 * 1024)
    digest = sha256(chunk * 272).hexdigest()
    with DbSession.atomic() as db:
        attachment = CardAttachment(card_id=card.id, user_id=board[1].id, filename="large.bin", content_sha256=digest,
                                    file=FileModel(storage_type="local", storage_name="card_attachment", original_filename="large.bin",
                                                   filename="large.bin", path="https://must-not-be-fetched.invalid"))
        db.insert(attachment)
    review = AppTransfers.review_transfer(board[1], board[2].id, card.id, connection.app_key, attachment.id)
    target = next(item for item in review["targets"] if item["resource_uid"] == resource.get_uid())
    result = AppTransfers.create_transfer(board[1], board[2].id, card.id, connection.app_key, str(uuid4()), connection.id,
                                         {**review["intent"], "resource_uid": resource.get_uid()}, target["authority"])
    spools = []
    native_spool = AppTransfers.SpooledTemporaryFile
    def spool(*args, **kwargs):
        value = native_spool(*args, **kwargs);spools.append(value);return value
    monkeypatch.setattr(AppTransfers, "SpooledTemporaryFile", spool)
    def storage(file, destination):
        assert not DbSession.has_active_transaction()
        for _ in range(272): destination.write(chunk)
        return True
    monkeypatch.setattr(Storage, "download_file", storage)
    with client_for(board) as client:
        response = client.get(f"/apps/v1/boards/{board[2].get_uid()}/transfers/{result['transfer_uid']}/attachment",
                              headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text[:200]
    assert len(response.content) == 17 * 1024 * 1024
    assert response.headers["x-content-sha256"] == digest
    assert spools and all(file.closed for file in spools)


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("during_io", ["revoked", "replaced", "deleted", "hash", "oversize"])
def test_attachment_read_discards_spool_on_current_fence_loss(board, monkeypatch, during_io):
    scope = setup_transfer(board, monkeypatch)
    connection, _, _, resource, card, token, *_ = scope
    monkeypatch.setattr(type(Env), "MAX_FILE_SIZE_MB", property(lambda _: 1))
    digest = sha256(b"content").hexdigest()
    with DbSession.atomic() as db:
        attachment = CardAttachment(card_id=card.id, user_id=board[1].id, filename="file.bin", content_sha256=digest,
                                    file=FileModel(storage_type="local", storage_name="card_attachment", original_filename="file.bin", filename="file.bin", path="unused"))
        db.insert(attachment)
    review = AppTransfers.review_transfer(board[1], board[2].id, card.id, connection.app_key, attachment.id)
    target = next(item for item in review["targets"] if item["resource_uid"] == resource.get_uid())
    result = AppTransfers.create_transfer(board[1], board[2].id, card.id, connection.app_key, str(uuid4()), connection.id,
                                         {**review["intent"], "resource_uid": resource.get_uid()}, target["authority"])
    spools = []
    native_spool = AppTransfers.SpooledTemporaryFile
    def spool(*args, **kwargs):
        value = native_spool(*args, **kwargs);spools.append(value);return value
    monkeypatch.setattr(AppTransfers, "SpooledTemporaryFile", spool)
    def storage(file, destination):
        assert not DbSession.has_active_transaction()
        if during_io == "oversize": destination.write(b"x" * (1024 * 1024 + 1))
        destination.write(b"wrong" if during_io == "hash" else b"content")
        if during_io in ("revoked", "replaced", "deleted"):
            with DbSession.atomic() as db:
                if during_io == "revoked": connection.state = "revoked";db.update(connection)
                else:
                    current = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == attachment.id)).first()
                    if during_io == "replaced": current.replaced_by_id = current.id + 1
                    else: current.deleted_at = SafeDateTime.now()
                    db.update(current)
        return True
    monkeypatch.setattr(Storage, "download_file", storage)
    with pytest.raises((AppGovernanceDenied, AppGovernanceConflict, ValueError)):
        AppTransfers.read_transfer_attachment(token, board[2].id, SnowflakeID.from_short_code(result["transfer_uid"]))
    assert spools and all(file.closed for file in spools)


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_legacy_attachment_review_hashes_without_database_lock_and_preserves_native_row(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    connection, _, _, resource, card, token, *_ = scope
    digest = sha256(b"legacy content").hexdigest()
    with DbSession.atomic() as db:
        attachment = CardAttachment(card_id=card.id, user_id=board[1].id, filename="legacy.bin", content_sha256=None,
                                    file=FileModel(storage_type="local", storage_name="card_attachment", original_filename="legacy.bin",
                                                   filename="legacy.bin", path="unused"))
        db.insert(attachment)
    def storage(file, destination):
        assert not DbSession.has_active_transaction()
        destination.write(b"legacy content")
        return True
    monkeypatch.setattr(Storage, "download_file", storage)
    review = AppTransfers.review_transfer(board[1], board[2].id, card.id, connection.app_key, attachment.id)
    assert review["intent"]["content_sha256"] == digest
    target = next(item for item in review["targets"] if item["resource_uid"] == resource.get_uid())
    result = AppTransfers.create_transfer(board[1], board[2].id, card.id, connection.app_key, str(uuid4()), connection.id,
                                         {**review["intent"], "resource_uid": resource.get_uid()}, target["authority"])
    file, actual = AppTransfers.read_transfer_attachment(token, board[2].id, SnowflakeID.from_short_code(result["transfer_uid"]))
    try:
        assert file.read() == b"legacy content" and actual == digest
    finally:
        file.close()
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == attachment.id)).first()
        assert current.content_sha256 is None  # No implicit attachment migration or mutation.


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_visible_outbox_paging_survives_uuid_loss_and_revoked_membership_returns_no_rows(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    connection, *_ = scope
    first, second = create(board, scope), create(board, scope)
    page = AppTransfers.list_sender_transfers(board[1], board[2].id, connection.app_key, limit=1)
    assert [item["transfer_uid"] for item in page["items"]] == [second["transfer_uid"]]
    assert page["next_cursor"] == second["transfer_uid"]
    previous = AppTransfers.list_sender_transfers(board[1], board[2].id, connection.app_key, limit=1,
                                                  after_id=SnowflakeID.from_short_code(page["next_cursor"]))
    assert [item["transfer_uid"] for item in previous["items"]] == [first["transfer_uid"]]
    assert previous["next_cursor"] is None
    assert "history" not in page["items"][0]["receipt"]
    assert "history" not in page["items"][0]["delivery"]
    with DbSession.atomic() as db:
        from langboard_shared.domain.models import Project, ProjectAssignedUser
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == board[2].id)).first()
        project.owner_id = 2;db.update(project)
        memberships = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.project_id == project.id,
                                   ProjectAssignedUser.user_id == board[1].id)).all()
        for member in memberships: db.delete(member)
    assert AppTransfers.list_sender_transfers(board[1], board[2].id, connection.app_key) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_attachment_storage_rejects_borrowed_transaction_before_io(board, monkeypatch):
    scope = setup_transfer(board, monkeypatch)
    result = create(board, scope)
    connection, _, _, _, card, token, *_ = scope
    monkeypatch.setattr(Storage, "download_file", lambda *a: pytest.fail("Storage I/O must not occur in borrowed transaction"))
    with DbSession.atomic():
        with pytest.raises(ValueError, match="own their storage transaction"):
            AppTransfers.read_transfer_attachment(token, board[2].id, SnowflakeID.from_short_code(result["transfer_uid"]))
        with pytest.raises(ValueError, match="own its storage transaction"):
            AppTransfers.review_transfer(board[1], board[2].id, card.id, connection.app_key, 999)
