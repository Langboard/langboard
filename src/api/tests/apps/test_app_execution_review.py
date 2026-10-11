# ruff: noqa: F811
"""Native Review movement retains current app authority and immutable replay."""
import importlib
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppExecutionReviewApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import (
    AppDefinition,
    AppExecutionReview,
    BoardAppBinding,
    Card,
    EmployeeMembershipPolicy,
    ProjectExecutionBinding,
)
from langboard_shared.domain.services.AppExecutionLeases import check_app_runtime
from langboard_shared.domain.services.AppExecutionStarts import report_app_execution_start
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from sqlalchemy import text
from test_app_execution_start import start_scope


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_app_review_native_http_current_fences_rollback_and_replay(board, monkeypatch):
    connection, card, token, request, lease_id, runtime = start_scope(board, monkeypatch)
    engine = DbEngine.get_main_engine()
    EmployeeMembershipPolicy.__table__.create(engine)
    ProjectExecutionBinding.__table__.create(engine)
    module = importlib.import_module("langboard.migrations.versions.20261011221000-b86548cd52e3")
    original = module.op
    try:
        with engine.begin() as db:
            module.op = Operations(MigrationContext.configure(db))
            module.upgrade()
            db.execute(text("CREATE SEQUENCE content_change_seq START 100"))
    finally:
        module.op = original
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    moves = []
    monkeypatch.setattr(CardService, "notify_order_changed", lambda *args: moves.append(args))
    report_app_execution_start(token, board[2].id, card.id, request.id, lease_id, runtime, "process-1")
    with DbSession.atomic() as db:
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        definition.declaration = {**definition.declaration, "workflow_requirements": {"required": ["ready"], "optional": ["review"]}}
        db.update(definition)
        board[5][1].workflow_stage = "review"
        board[6][1].key = "review"
        board[6][1].is_builtin = True
        db.update(board[5][1])
        db.update(board[6][1])
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        binding.workflow_mapping = {**binding.workflow_mapping, "review": board[5][1].get_uid()}
        db.update(binding)
    body = {"generation": request.generation, "expected_card_revision": card.last_change_seq,
            "expected_binding_revision": binding.edit_revision(), "evidence_reference": "result:review-1"}
    path = f"/apps/v1/boards/{board[2].get_uid()}/cards/{card.get_uid()}/execution-requests/{request.get_uid()}/review"
    app = FastAPI()
    app.include_router(AppRouter.api)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json={**body, "actor_uid": board[1].get_uid()}, headers=headers).status_code == 400
        assert client.post(path, json=body, headers=headers).status_code == 403  # Not stopped or started.
        check_app_runtime(lease_id, runtime, stopped=True)
        assert client.post(path, json={**body, "generation": request.generation + 1}, headers=headers).status_code == 403
        assert client.post(path, json={**body, "expected_card_revision": 99}, headers=headers).status_code == 409
        assert client.post(path, json={**body, "expected_binding_revision": "stale"}, headers=headers).status_code == 409
        # Read consent remains independent; execute consent removal blocks this write.
        with DbSession.atomic() as db:
            saved_grants = binding.granted_capabilities
            binding.granted_capabilities = ["resources.read"]
            db.update(binding)
        assert client.post(path, json=body, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            binding.granted_capabilities = saved_grants
            db.update(binding)
        body["expected_binding_revision"] = binding.edit_revision()
        # A native policy failure must roll back movement and receipt together.
        from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService
        with monkeypatch.context() as failure:
            def fail_policy(*args):
                raise ValueError("Native policy failed")
            failure.setattr(WorkflowStagePolicyService, "apply_transition", fail_policy)
            assert client.post(path, json=body, headers=headers).status_code == 400
        with DbSession.use(readonly=False) as db:
            assert db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)).first().project_column_id == board[5][0].id
            assert not db.exec(SqlBuilder.select.table(AppExecutionReview)).all()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: client.post(path, json=body, headers=headers), range(4)))
        assert all(result.status_code == 200 for result in results), [result.text for result in results]
        assert sum(result.json()["changed"] for result in results) == 1
        first = results[0]
        assert first.status_code == 200, first.text
        assert first.json()["workflow_transitioned"] is True and first.json()["workflow_stage"] == "review"
        assert first.json()["started"] is False and first.json()["execution_resumed"] is False
        replay = client.post(path, json=body, headers=headers)
        assert replay.status_code == 200 and replay.json()["changed"] is False
        assert len(moves) == 1
        # Consume the independently built wheel over the same native HTTP route.
        import asyncio
        from types import SimpleNamespace
        from langboard_sdk import AppExecution, HttpTransport
        async def transport(method, route, **kwargs):
            return client.request(method, route, headers=headers, **kwargs)
        sdk = AppExecution(HttpTransport(SimpleNamespace(request=transport)))
        sdk_replay = asyncio.run(sdk.request_review(board[2].get_uid(), card.get_uid(),
                                request_uid=request.get_uid(), **body))
        assert sdk_replay["changed"] is False and sdk_replay["to_column_uid"] == board[5][1].get_uid()
        assert client.post(path, json={**body, "evidence_reference": "different"}, headers=headers).status_code == 409
        with DbSession.atomic() as db:
            board[6][1].counts_as_completed = True
            db.update(board[6][1])
        assert client.post(path, json=body, headers=headers).status_code == 409
        with DbSession.atomic() as db:
            board[6][1].counts_as_completed = False
            db.update(board[6][1])
            connection.state = "revoked"
            db.update(connection)
        assert client.post(path, json=body, headers=headers).status_code == 401
        with engine.begin() as db:
            module.op = Operations(MigrationContext.configure(db))
            with pytest.raises(RuntimeError, match="retained"):
                module.downgrade()
        module.op = original
