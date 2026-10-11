# ruff: noqa: F811
"""Human card authority can stop a retained runtime without app credentials."""
from secrets import token_hex
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.board import CardAppInteractionsApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import AppExecutionLease, EmployeeMembershipPolicy
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppExecutionLeases import authorize_app_runtime, check_app_runtime
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.Env import Env
from test_app_execution_lease import permit_scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_human_stop_fences_runtime_and_retains_idempotent_history(board, monkeypatch):
    connection, card, token, request, ack_id = permit_scope(board, monkeypatch)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
    runtime_token = token_hex(32)
    permit = authorize_app_runtime(token, board[2].id, card.id, request.id, ack_id, runtime_token)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    actor = board[1]
    with DbSession.atomic() as db:
        board[2].owner_id = 2
        db.update(board[2])
        board[4].actions = ["read", "card_update"]
        db.update(board[4])
    path = f"/board/{board[2].get_uid()}/card/{card.get_uid()}/apps/executions/{request.get_uid()}/stop"
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    # Keep the genuine native card ACL; fixture card service only supplies its
    # existing dependency tables through the normal scope setup.
    assert DomainService().card.resolve_readable_card(board[2].id, card.id, actor) is not None
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        assert client.post(path, json={"generation": request.generation}, headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert client.post(path, json={"generation": request.generation, "actor_uid": actor.get_uid()}, headers=headers).status_code == 400
        assert client.post(path, json={"generation": request.generation + 1}, headers=headers).status_code == 409
        # Read permission is insufficient, even after app revocation.
        with DbSession.atomic() as db:
            board[4].actions = ["read"]
            db.update(board[4])
        assert client.post(path, json={"generation": request.generation}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            board[4].actions = ["read", "card_update"]
            db.update(board[4])
            connection.state = "revoked"
            db.update(connection)
        listed = client.get(path.rsplit("/", 2)[0], headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["request_uid"] == request.get_uid()
        assert listed.json()["items"][0]["state"] == "authorized"
        assert "runtime_token" not in listed.text and "token_hash" not in listed.text
        first = client.post(path, json={"generation": request.generation}, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["state"] == "stop_requested"
        assert client.get(path.rsplit("/", 2)[0], headers=headers).json()["items"][0]["state"] == "stop_requested"
        assert first.json()["permit_execution"] is False
        assert first.json()["started"] is False
        assert first.json()["execution_resumed"] is False
        assert first.json()["lease_uid"] == permit["lease_uid"]
        assert first.json()["changed"] is True
        second = client.post(path, json={"generation": request.generation}, headers=headers)
        assert second.status_code == 200 and second.json()["changed"] is False
        foreign_path = path.replace(board[2].get_uid(), board[5][0].get_uid())
        assert client.post(foreign_path, json={"generation": request.generation}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            board[4].actions = ["read"]
            db.update(board[4])
        assert client.post(path, json={"generation": request.generation}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            board[4].actions = ["read", "card_update"]
            db.update(board[4])
        with DbSession.use(readonly=False) as db:
            lease = db.exec(SqlBuilder.select.table(AppExecutionLease)).first()
            events = [event for event in lease.history if event.get("reason") == "human_request"]
            assert len(events) == 1 and events[0]["actor_uid"] == actor.get_uid()
        assert check_app_runtime(lease.id, runtime_token)["permit_execution"] is False
        assert check_app_runtime(lease.id, runtime_token, stopped=True)["state"] == "stopped"
        final = client.post(path, json={"generation": request.generation}, headers=headers)
        assert final.status_code == 200 and final.json()["state"] == "stopped"
        assert final.json()["changed"] is False
