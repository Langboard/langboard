"""Native Active handoff must preserve this lease and revoke on subsequent changes."""

import importlib
from datetime import timedelta
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppExecutionLeaseApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppDefinition,
    AppExecutionActive,
    AppExecutionLease,
    AppExecutionStart,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardChangeReceipt,
    EmployeeMembershipPolicy,
    ProjectExecutionBinding,
)
from langboard_shared.domain.services.AppExecutionActive import activate_app_runtime, read_app_active_handoff
from langboard_shared.domain.services.AppExecutionLeases import check_app_runtime
from langboard_shared.domain.services.AppExecutionStarts import report_app_execution_start
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.factory.CardService import CardService
from sqlalchemy import text
from test_app_execution_start import start_scope


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("revocation", ["content", "resource", "ownership", "binding", "mapping", "connection", "generation", "move", "completed_stage"])
def test_native_active_handoff_current_permit_replay_rollback_and_revocation(board, monkeypatch, revocation):
    connection, card, token, request, lease_id, runtime = start_scope(board, monkeypatch)
    engine = DbEngine.get_main_engine()
    EmployeeMembershipPolicy.__table__.create(engine)
    CardChangeReceipt.__table__.create(engine, checkfirst=True)
    ProjectExecutionBinding.__table__.create(engine, checkfirst=True)
    with engine.begin() as db:
        db.execute(text("CREATE SEQUENCE content_change_seq START 100"))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    moves = []
    monkeypatch.setattr(CardService, "notify_order_changed", lambda *args: moves.append(args))
    with DbSession.atomic() as db:
        # Optional stage is declared before admission in a real app. This fixture
        # adds it without changing the accepted binding or card revision.
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        definition.declaration = {**definition.declaration,
            "workflow_requirements": {"required": ["ready"], "optional": ["active"]},
            "capabilities": [*definition.declaration["capabilities"], "interactions.request"]}
        db.update(definition)
        board[5][1].workflow_stage = "active"
        board[6][1].key = "active"
        board[6][1].is_builtin = True
        db.update(board[5][1])
        db.update(board[6][1])
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        binding.granted_capabilities = [*binding.granted_capabilities, "interactions.request"]
        binding.workflow_mapping = {**binding.workflow_mapping, "active": board[5][1].get_uid()}
        db.update(binding)
        # Fixture re-admission is needed because mapping edits revoke old leases.
        from langboard_shared.domain.services.AppExecutionGrant import evaluate_current_execution_grant
        request.authority = evaluate_current_execution_grant(token, board[2].id, card.id, request.generation)
        db.update(request)
    for name in ("20261011090000-ed71a806b2f4", "20261011100000-fe82b917c3a5",
                 "20261011110000-a093ca28d4b6", "20261011120000-b1a4db39e5c7"):
        module = importlib.import_module(f"langboard.migrations.versions.{name}")
        original = module.op
        try:
            with engine.begin() as db:
                module.op = Operations(MigrationContext.configure(db))
                module.upgrade()
        finally:
            module.op = original
    args = (token, board[2].id, card.id, request.id, lease_id, runtime,
            card.last_change_seq, binding.edit_revision())
    with pytest.raises(AppGovernanceDenied):
        activate_app_runtime(*args)  # permit alone is not a start attestation
    report_app_execution_start(token, board[2].id, card.id, request.id, lease_id, runtime, "process-active-1")
    assert read_app_active_handoff(*args[:6])["state"] == "not_recorded"
    with pytest.raises(AppGovernanceConflict):
        activate_app_runtime(*args[:6], 99, args[7])
    from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService
    with monkeypatch.context() as failure:
        def fail_policy(*unused):
            raise ValueError("Native stage policy failed")
        failure.setattr(WorkflowStagePolicyService, "apply_transition", fail_policy)
        with pytest.raises(ValueError):
            activate_app_runtime(*args)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().project_column_id == board[5][0].id
        assert not db.exec(SqlBuilder.select.table(AppExecutionActive)).all()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: activate_app_runtime(*args), range(3)))
    assert sum(result["changed"] for result in results) == 1
    first = next(result for result in results if result["changed"])
    assert first["changed"] and first["state"] == "active_recorded"
    assert activate_app_runtime(*args) == {**first, "changed": False}
    assert read_app_active_handoff(*args[:6]) == {**first, "changed": False}
    assert check_app_runtime(lease_id, runtime)["permit_execution"] is True
    if revocation == "content":
        app = FastAPI()
        app.include_router(AppRouter.api)
        path = f"/apps/v1/boards/{board[2].get_uid()}/cards/{card.get_uid()}/execution-requests/{request.get_uid()}/active"
        body = {"lease_uid": lease_id.to_short_code(), "runtime_token": runtime,
                "expected_card_revision": args[6], "expected_binding_revision": args[7]}
        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.post(path, json=body).status_code == 401
            assert client.post(path, json={**body, "actor_uid": board[1].get_uid()}, headers=headers).status_code == 400
            replay = client.post(path, json=body, headers=headers)
            assert replay.status_code == 200 and replay.json()["changed"] is False
            read = client.post(path + "-receipt", json={"lease_uid": body["lease_uid"], "runtime_token": runtime}, headers=headers)
            assert read.status_code == 200 and read.json()["handoff_uid"] == first["handoff_uid"]
            assert read.headers["cache-control"] == "no-store"
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first()
        assert current.project_column_id == board[5][1].id and current.last_change_seq > 0
        assert len(db.exec(SqlBuilder.select.table(AppExecutionActive)).all()) == 1
    assert len(moves) == 1
    from langboard_shared.domain.services.AppExecutionAcknowledgments import read_app_execution_request
    observed = read_app_execution_request(token, board[2].id, card.id, request.id)
    assert observed["request_uid"] == request.get_uid()
    assert observed["authority"]["card_revision"] == current.last_change_seq
    assert observed["authority"]["authority_version"] != request.authority["authority_version"]
    from langboard_shared.domain.services.AppCardInteractions import (
        create_app_card_interaction,
        respond_to_app_card_interaction,
    )
    from langboard_shared.domain.services.DomainService import DomainService
    interaction = create_app_card_interaction(token, board[2].id, card.id, {
        "schema_version": 1, "kind": "action", "app_request_key": "active-human-choice",
        "prompt": {"en": "Choose the next action"},
        "questions": [{"id": "action", "prompt": {"en": "Action"}, "type": "single_choice",
                       "choices": [{"id": "continue", "label": {"en": "Continue"}}]}],
        "presentation": {"type": "choice_card"},
        "execution": {"request_uid": request.get_uid(), "generation": request.generation},
        "expires_at": (SafeDateTime.now() + timedelta(hours=1)).isoformat(),
    })
    answer = respond_to_app_card_interaction(DomainService(), board[1], board[2].id, card.id,
        SnowflakeID.from_short_code(interaction["interaction_uid"]), interaction["revision"], "human-active-answer",
        {"action": {"choice_id": "continue"}}, request.generation)
    assert answer["state"] == "answered" and answer["execution_resumed"] is False
    with DbSession.atomic() as db:
        if revocation == "content":
            current.title = "Changed after Active handoff"
            current.last_change_seq += 1
            db.update(current)
        elif revocation == "completed_stage":
            stage = board[6][1]
            stage.counts_as_completed = True
            db.update(stage)
        elif revocation == "move":
            current.project_column_id = board[5][0].id
            db.update(current)
        elif revocation == "resource":
            resource = db.exec(SqlBuilder.select.table(AppResourceBinding)).first()
            resource.access_revision += 1
            db.update(resource)
        elif revocation == "ownership":
            owner = db.exec(SqlBuilder.select.table(CardAppOwnership)).first()
            owner.revision += 1
            db.update(owner)
        elif revocation in ("binding", "mapping"):
            if revocation == "binding":
                binding.granted_capabilities = ["resources.read"]
            else:
                binding.workflow_mapping = {"ready": board[5][0].get_uid()}
            db.update(binding)
        elif revocation == "connection":
            connection.state = "revoked"
            db.update(connection)
        else:
            db.exec(text("UPDATE card_execution_generation SET execution_generation=4"))
    stopped = check_app_runtime(lease_id, runtime)
    assert stopped["state"] == "stop_requested" and not stopped["permit_execution"]
    with pytest.raises(AppGovernanceDenied):
        activate_app_runtime(*args)
    assert check_app_runtime(lease_id, runtime, stopped=True)["state"] == "stopped"
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(AppExecutionStart)).all()) == 1
        assert len(db.exec(SqlBuilder.select.table(AppExecutionActive)).all()) == 1
        assert [item["state"] for item in db.exec(SqlBuilder.select.table(AppExecutionLease)).first().history] == ["authorized", "stop_requested", "stopped"]
