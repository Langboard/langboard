"""Current retained starts are explicit, sanitized, non-mutating read snapshots."""

from datetime import datetime, timedelta
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AppConnectionCredential,
    AppExecutionLease,
    AppExecutionStart,
    BoardAppBinding,
    CardAppOwnership,
    CardAppResourceSelection,
)
from langboard_shared.domain.services.AppExecutionLeases import runtime_timestamp
from langboard_shared.domain.services.AppExecutionStarts import report_app_execution_start
from langboard_shared.domain.services.CardAgentExecution import card_agent_execution
from sqlalchemy import event, text
from test_app_execution_start import start_scope


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_current_start_snapshot_is_sanitized_read_only_and_not_liveness(board, monkeypatch):
    _, card, token, request, lease_id, runtime_token = start_scope(board, monkeypatch)
    assert card_agent_execution(board[2].id, card.id) is None
    receipt = report_app_execution_start(
        token, board[2].id, card.id, request.id, lease_id, runtime_token, "SECRET-process"
    )
    statements = []
    engine = DbEngine.get_main_engine()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = card_agent_execution(board[2].id, card.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result["start_uid"] == receipt["start_uid"]
    assert result["request_uid"] == request.get_uid()
    assert result["current_authorization"] == "authorized" and result["process_liveness"] == "unknown"
    assert set(result) == {
        "schema_version",
        "start_uid",
        "request_uid",
        "app_key",
        "generation",
        "reported_at",
        "evaluated_at",
        "authorization_expires_at",
        "evidence_kind",
        "current_authorization",
        "process_liveness",
    }
    assert result["evidence_kind"] == "app_attestation"
    assert all(
        datetime.fromisoformat(result[key]).tzinfo
        for key in ("reported_at", "evaluated_at", "authorization_expires_at")
    )
    assert datetime.fromisoformat(result["evaluated_at"]) < datetime.fromisoformat(result["authorization_expires_at"])
    assert not any(
        word in " ".join(statements).upper()
        for word in ("FOR UPDATE", "FOR SHARE", "PG_ADVISORY", "INSERT ", "UPDATE ", "DELETE ")
    )
    assert card_agent_execution(board[2].id + 1, card.id) is None
    assert card_agent_execution(board[2].id, card.id + 1) is None
    with DbSession.use(readonly=False) as db:
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease)).first()
        assert result["authorization_expires_at"] == runtime_timestamp(lease.expires_at)
        assert lease.state == "authorized" and len(lease.history) == 1
        assert len(db.exec(SqlBuilder.select.table(AppExecutionStart)).all()) == 1


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "gate",
    [
        "credential_revoked",
        "credential_expired",
        "credential_deleted",
        "runtime_expired",
        "stopped",
        "stop_requested",
        "connection",
        "private",
        "deleted",
        "generation",
        "content",
        "ownership",
        "binding",
        "resources",
    ],
)
def test_withdrawn_authority_never_presents_retained_start_as_current(board, monkeypatch, gate):
    connection, card, token, request, lease_id, runtime_token = start_scope(board, monkeypatch)
    report_app_execution_start(token, board[2].id, card.id, request.id, lease_id, runtime_token, "SECRET-process")
    with DbSession.atomic() as db:
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease)).first()
        credential = db.exec(
            SqlBuilder.select.table(AppConnectionCredential).where(AppConnectionCredential.id == lease.credential_id)
        ).first()
        original_expiry = lease.expires_at
        if gate == "credential_revoked":
            credential.revoked_at = SafeDateTime.now()
            db.update(credential)
        elif gate == "credential_expired":
            credential.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(credential)
        elif gate == "credential_deleted":
            db.delete(credential)
        elif gate == "runtime_expired":
            lease.expires_at = SafeDateTime.now() - timedelta(seconds=1)
            db.update(lease)
        elif gate in ("stopped", "stop_requested"):
            lease.state = gate
            db.update(lease)
        elif gate == "connection":
            connection.state = "revoked"
            db.update(connection)
        elif gate == "private":
            card.visibility = "PRIVATE"
            card.owner_user_id = board[1].id
            card.created_by_user_id = board[1].id
            db.update(card)
        elif gate == "deleted":
            card.deleted_at = SafeDateTime.now()
            db.update(card)
        elif gate == "generation":
            db.exec(
                text("UPDATE card_execution_generation SET execution_generation=4 WHERE card_id=:id"),
                params={"id": int(card.id)},
            )
        elif gate == "content":
            card.last_change_seq += 1
            db.update(card)
        elif gate == "ownership":
            row = db.exec(SqlBuilder.select.table(CardAppOwnership)).first()
            row.revision += 1
            db.update(row)
        elif gate == "binding":
            row = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
            row.state = "disabled"
            db.update(row)
        elif gate == "resources":
            row = db.exec(SqlBuilder.select.table(CardAppResourceSelection)).first()
            row.revision += 1
            db.update(row)
    assert card_agent_execution(board[2].id, card.id) is None
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(AppExecutionStart)).all()) == 1
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease)).first()
        assert lease.state == (gate if gate in ("stopped", "stop_requested") else "authorized")
        if gate != "runtime_expired":
            assert lease.expires_at == original_expiry


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_native_detail_http_projects_only_explicit_authorized_start(board, monkeypatch):
    from types import SimpleNamespace
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from langboard.routes.board import BoardCardApi
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.domain.models import EmployeeMembershipPolicy
    from langboard_shared.domain.services.DomainService import DomainService

    _, card, token, request, lease_id, runtime_token = start_scope(board, monkeypatch)
    report_app_execution_start(token, board[2].id, card.id, request.id, lease_id, runtime_token, "SECRET-process")
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
    native = DomainService().card
    # Body rendering is unrelated to this contract. Canonical read permission,
    # current audience, grant/credential evaluation and the route remain native.
    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_canonical_card=native.resolve_canonical_card,
            get_details=lambda *args, **kwargs: {"uid": card.get_uid()},
            can_delete=lambda *args: False,
        ),
        project=SimpleNamespace(get_user_role_actions_by_project=lambda *args: ["read"]),
    )
    route = next(route for route in AppRouter.api.routes if route.endpoint is BoardCardApi.get_card_details)
    app = FastAPI()
    app.include_router(APIRouter(routes=[route]))
    for dependency in route.dependant.dependencies:
        if dependency.name == "user_or_bot":
            app.dependency_overrides[dependency.call] = lambda: board[1]
        elif dependency.name == "service":
            app.dependency_overrides[dependency.call] = lambda: service
    path = f"/board/{board[2].get_uid()}/card/{card.get_uid()}"
    with TestClient(app) as client:
        response = client.get(path + "?sections=agent_execution")
        assert response.status_code == 200, response.text
        assert response.json()["agent_execution"]["request_uid"] == request.get_uid()
        assert "SECRET" not in response.text and "runtime-1" not in response.text and runtime_token not in response.text
        monkeypatch.setattr(BoardCardApi, "card_agent_execution", lambda *args: pytest.fail("Unrequested projection"))
        assert "agent_execution" not in client.get(path + "?sections=description").json()
        with DbSession.atomic() as db:
            card.deleted_at = SafeDateTime.now()
            db.update(card)
        assert client.get(path + "?sections=agent_execution").status_code == 404


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_explicit_native_active_handoff_preserves_snapshot_until_content_changes(board, monkeypatch):
    from langboard_shared.domain.models import (
        AppDefinition,
        Card,
        CardChangeReceipt,
        CardDecisionGate,
        DecisionCase,
        EmployeeMembershipPolicy,
        ProjectExecutionBinding,
    )
    from langboard_shared.domain.services.AppExecutionActive import activate_app_runtime
    from langboard_shared.domain.services.AppExecutionGrant import evaluate_current_execution_grant
    from langboard_shared.domain.services.factory.CardService import CardService

    _, card, token, request, lease_id, runtime = start_scope(board, monkeypatch)
    engine = DbEngine.get_main_engine()
    EmployeeMembershipPolicy.__table__.create(engine)
    ProjectExecutionBinding.__table__.create(engine, checkfirst=True)
    CardChangeReceipt.__table__.create(engine, checkfirst=True)
    # Native stage movement reads absent gate bindings; retain their real FK dependencies.
    DecisionCase.__table__.create(engine, checkfirst=True)
    CardDecisionGate.__table__.create(engine, checkfirst=True)
    with engine.begin() as db:
        db.execute(text("CREATE SEQUENCE content_change_seq START 100"))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(CardService, "notify_order_changed", lambda *args: None)
    with DbSession.atomic() as db:
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        definition.declaration = {
            **definition.declaration,
            "workflow_requirements": {"required": ["ready"], "optional": ["active"]},
        }
        db.update(definition)
        board[5][1].workflow_stage = "active"
        board[6][1].key = "active"
        board[6][1].is_builtin = True
        db.update(board[5][1])
        db.update(board[6][1])
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        binding.workflow_mapping = {**binding.workflow_mapping, "active": board[5][1].get_uid()}
        db.update(binding)
        request.authority = evaluate_current_execution_grant(token, board[2].id, card.id, request.generation)
        db.update(request)
    report_app_execution_start(token, board[2].id, card.id, request.id, lease_id, runtime, "SECRET-active")
    before = card_agent_execution(board[2].id, card.id)
    activate_app_runtime(
        token, board[2].id, card.id, request.id, lease_id, runtime, card.last_change_seq, binding.edit_revision()
    )
    after = card_agent_execution(board[2].id, card.id)
    assert after["start_uid"] == before["start_uid"] and after["process_liveness"] == "unknown"
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        current.last_change_seq += 1
        db.update(current)
    assert card_agent_execution(board[2].id, card.id) is None


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
def test_snapshot_expires_at_earliest_credential_or_runtime_fence(board, monkeypatch):
    _, card, token, request, lease_id, runtime = start_scope(board, monkeypatch)
    report_app_execution_start(token, board[2].id, card.id, request.id, lease_id, runtime, "SECRET-expiry")
    with DbSession.atomic() as db:
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease)).first()
        credential = db.exec(
            SqlBuilder.select.table(AppConnectionCredential).where(AppConnectionCredential.id == lease.credential_id)
        ).first()
        credential.expires_at = SafeDateTime.now() + timedelta(seconds=30)
        db.update(credential)
        expected_expiry = runtime_timestamp(credential.expires_at)
    assert datetime.fromisoformat(
        card_agent_execution(board[2].id, card.id)["authorization_expires_at"]
    ) == datetime.fromisoformat(expected_expiry)
