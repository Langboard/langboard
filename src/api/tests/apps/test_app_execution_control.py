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
    from langboard_shared.domain.services.AppExecutionControl import (
        list_human_card_runtimes,
        request_human_runtime_resume,
        request_human_runtime_stop,
    )
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
    service = DomainService()
    assert list_human_card_runtimes(service, board[1], board[2].id, card.id, app_key="another-app")["items"] == []
    assert list_human_card_runtimes(service, board[1], board[2].id, card.id, app_key=request.app_key)["items"][0]["request_uid"] == request.get_uid()
    for operation in (request_human_runtime_stop, request_human_runtime_resume):
        with pytest.raises(AppGovernanceDenied):
            operation(service, board[1], board[2].id, card.id, request.id, request.generation, app_key="another-app")
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
            connection.state = "connected"
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
        from langboard_shared.domain.services.AppExecutionGrant import evaluate_current_execution_grant
        from langboard_shared.domain.services.AppExecutionRequests import request_app_execution
        from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
        with DbSession.atomic() as db:
            board[2].owner_id = actor.id
            db.update(board[2])
        with pytest.raises(AppGovernanceDenied):
            evaluate_current_execution_grant(token, board[2].id, card.id, request.generation)
        with pytest.raises(AppGovernanceDenied):
            request_app_execution(token, board[2].id, card.id, request.generation,
                                  expected_authority_version=request.authority["authority_version"])
        with DbSession.atomic() as db:
            board[2].owner_id = 2
            db.update(board[2])
            connection.state = "revoked"
            db.update(connection)
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
        with DbSession.atomic() as db:
            board[2].owner_id = actor.id
            db.update(board[2])
        # Restoring app consent cannot resurrect the stopped generation.
        with DbSession.atomic() as db:
            connection.state = "connected"
            db.update(connection)
        with pytest.raises(AppGovernanceDenied):
            evaluate_current_execution_grant(token, board[2].id, card.id, request.generation)
        from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
        replacement_token = issue_connection_credential(actor, connection.id)["token"]
        with pytest.raises(AppGovernanceDenied):
            evaluate_current_execution_grant(replacement_token, board[2].id, card.id, request.generation)
        resume_path = path.removesuffix("/stop") + "/resume"
        assert client.post(resume_path, json={"generation": request.generation}, headers=headers).status_code == 409
        # A producer-issued newer readiness generation has its own authority;
        # this does not create a permit or resume the retained stopped runtime.
        from sqlalchemy import text
        with DbEngine.get_main_engine().begin() as db:
            db.execute(text("UPDATE card_execution_generation SET execution_generation=:generation WHERE card_id=:card_id"),
                       {"generation": request.generation + 1, "card_id": int(card.id)})
        assert evaluate_current_execution_grant(replacement_token, board[2].id, card.id, request.generation + 1)["generation"] == request.generation + 1
        assert check_app_runtime(lease.id, runtime_token)["state"] == "stopped"
        with DbSession.atomic() as db:
            board[5][0].workflow_stage = "active"
            db.update(board[5][0])
        assert client.post(resume_path, json={"generation": request.generation}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            board[5][0].workflow_stage = "ready"
            db.update(board[5][0])
        resumed = client.post(resume_path, json={"generation": request.generation}, headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "resume_requested"
        assert resumed.json()["generation"] == request.generation + 1
        assert resumed.json()["started"] is False and resumed.json()["execution_resumed"] is False
        assert resumed.json()["request_uid"] != request.get_uid()
        assert resumed.json()["changed"] is True
        replay = client.post(resume_path, json={"generation": request.generation}, headers=headers)
        assert replay.status_code == 200 and replay.json()["changed"] is False
        assert replay.json()["request_uid"] == resumed.json()["request_uid"]
        from langboard_shared.domain.models import AppExecutionOutbox, AppExecutionRequest
        with DbSession.use(readonly=False) as db:
            assert len(db.exec(SqlBuilder.select.table(AppExecutionRequest)).all()) == 2
            assert len(db.exec(SqlBuilder.select.table(AppExecutionOutbox)).all()) == 2
            retained = db.exec(SqlBuilder.select.table(AppExecutionLease)).first()
            assert retained.state == "stopped"
            assert len([entry for entry in retained.history if entry.get("action") == "resume_requested"]) == 1
        with DbSession.atomic() as db:
            connection.state = "revoked"
            db.update(connection)
        assert client.post(resume_path, json={"generation": request.generation}, headers=headers).status_code == 403


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_runtime_history_pagination_is_card_scoped_and_older_stop_is_accessible(board, monkeypatch):
    from langboard_shared.domain.models import AppExecutionRequest
    from langboard_shared.domain.services.AppExecutionControl import list_human_card_runtimes

    _, card, token, request, ack_id = permit_scope(board, monkeypatch)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
    authorize_app_runtime(token, board[2].id, card.id, request.id, ack_id, token_hex(32))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    with DbSession.atomic() as db:
        for index in range(12):
            db.insert(AppExecutionRequest(project_id=board[2].id, card_id=card.id,
                app_key=request.app_key, connection_id=request.connection_id,
                generation=request.generation + index + 1, authority={}))
        db.insert(AppExecutionRequest(project_id=board[2].id, card_id=card.id + 1,
            app_key=request.app_key, connection_id=request.connection_id, generation=99, authority={}))
    first = list_human_card_runtimes(DomainService(), board[1], board[2].id, card.id)
    assert len(first["items"]) == 10 and first["next_cursor"]
    assert all(item["state"] == "awaiting_permit" for item in first["items"])
    from langboard_shared.core.types import SnowflakeID
    second = list_human_card_runtimes(DomainService(), board[1], board[2].id, card.id,
                                     before=SnowflakeID.from_short_code(first["next_cursor"]))
    assert len(second["items"]) == 3 and second["next_cursor"] is None
    assert second["items"][-1]["request_uid"] == request.get_uid()
    assert second["items"][-1]["state"] == "authorized"
    assert not set(item["request_uid"] for item in first["items"]) & set(item["request_uid"] for item in second["items"])
    assert len({item["generation"] for item in [*first["items"], *second["items"]]}) == 13


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_panel_runtime_http_pins_app_and_current_human_authority(board, monkeypatch):
    from langboard.routes.board import AppPanelDataApi  # noqa: F401
    from langboard_shared.domain.models import AppDefinition, BoardAppBinding
    connection, card, token, request, ack_id = permit_scope(board, monkeypatch)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine())
    authorize_app_runtime(token, board[2].id, card.id, request.id, ack_id, token_hex(32))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    with DbSession.atomic() as db:
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == request.app_key)).first()
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == request.app_key)).first()
        definition.declaration = {**definition.declaration, "panel": {"url": "https://example.test/panel", "name": "Test panel"}}
        binding.granted_capabilities = [*binding.granted_capabilities, "panels.render"]
        db.update(definition)
        db.update(binding)
        revision = {"app_revision": definition.edit_revision(), "binding_revision": binding.edit_revision()}
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(board[1].id)
    headers = {"Authorization": f"Bearer {access}"}
    path = f"/board/{board[2].get_uid()}/apps/{request.app_key}/panel"
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        body = {**revision, "card_uid": card.get_uid()}
        listed = client.post(path + "/runtimes", json=body, headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["app_key"] == request.app_key
        assert "runtime_token" not in listed.text
        assert client.post(path + "/runtimes", json={**body, "binding_revision": "0" * 64}, headers=headers).status_code == 409
        control = path + f"/cards/{card.get_uid()}/runtimes/{request.get_uid()}/stop"
        assert client.post(control, json={**revision, "generation": request.generation + 1}, headers=headers).status_code == 409
        stopped = client.post(control, json={**revision, "generation": request.generation}, headers=headers)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["state"] == "stop_requested" and stopped.json()["execution_resumed"] is False
        with DbSession.atomic() as db:
            binding.granted_capabilities = [c for c in binding.granted_capabilities if c != "execution.run"]
            db.update(binding)
        assert client.post(path + "/runtimes", json=body, headers=headers).status_code == 404


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_explicit_new_run_reuses_producer_and_rolls_back_revoked_authority(board, monkeypatch):
    from langboard_shared.domain.models import (
        CardAssignedProjectLabel,
        CardAssignedUser,
        ProjectExecutionBinding,
        WebhookSetting,
    )
    from langboard_shared.domain.services.AppExecutionControl import (
        request_human_runtime_resume,
        request_human_runtime_stop,
    )
    from langboard_shared.domain.services.AppExecutionRequests import AppExecutionRequestConflict
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
    from langboard_shared.tasks.webhooks import ExecutionOutboxTask
    from langboard_shared.tasks.webhooks.ExecutionReadinessUow import WORK_EVENT, current_execution
    from sqlalchemy import text

    connection, card, token, request, ack_id = permit_scope(board, monkeypatch)
    engine = DbEngine.get_main_engine()
    EmployeeMembershipPolicy.__table__.create(engine)
    tables = set()
    def include(table):
        if table in tables:
            return
        tables.add(table)
        for fk in table.foreign_keys:
            include(fk.column.table)
    for model in (CardAssignedProjectLabel, CardAssignedUser, ProjectExecutionBinding):
        include(model.__table__)
    ProjectExecutionBinding.metadata.create_all(engine, tables=list(tables), checkfirst=True)
    with engine.begin() as db:
        db.execute(text("CREATE TABLE execution_outbox (id uuid PRIMARY KEY, project_id bigint NOT NULL, card_id bigint NOT NULL, execution_generation integer NOT NULL, occurred_at timestamptz NOT NULL, event_type text NOT NULL, payload_json jsonb, state text NOT NULL DEFAULT 'pending', UNIQUE(card_id, execution_generation))"))
        db.execute(WebhookSetting.__table__.insert(), WebhookSetting(id=1, name="Test webhook", secret_id="test-secret", url="https://example.invalid/hook", events=[WORK_EVENT]).model_dump())
        db.execute(text("UPDATE global_card_relationship_type SET machine_semantic='blocks', is_active=true"))
        # No relationship row is needed, but the readiness registry must be explicit.
        if not db.execute(text("SELECT id FROM global_card_relationship_type")).first():
            from langboard_shared.domain.models import GlobalCardRelationshipType
            db.execute(GlobalCardRelationshipType.__table__.insert(), GlobalCardRelationshipType(id=999, parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks").model_dump())
        type_id = db.execute(text("SELECT id FROM global_card_relationship_type LIMIT 1")).scalar_one()
    with DbSession.atomic() as db:
        db.insert(ProjectExecutionBinding(project_id=board[2].id, is_enabled=True,
            column_semantic_ids={str(int(card.project_column_id)): "ready"},
            prerequisite_relationship_type_id=type_id, webhook_id=1, webhook_uid="test-hook", events=[WORK_EVENT]))
    enqueued = []
    monkeypatch.setattr(ExecutionOutboxTask, "execution_outbox_task", enqueued.append)
    runtime_token = token_hex(32)
    permit = authorize_app_runtime(token, board[2].id, card.id, request.id, ack_id, runtime_token)
    service = DomainService()
    args = (service, board[1], board[2].id, card.id, request.id, request.generation)
    request_human_runtime_stop(*args)
    observed = current_execution(card.id)
    choice = {"new_run": True, "expected_generation": observed.generation, "expected_revision": observed.revision}
    with pytest.raises(AppExecutionRequestConflict):
        request_human_runtime_resume(*args, **choice)
    from langboard_shared.core.types import SnowflakeID
    assert check_app_runtime(SnowflakeID.from_short_code(permit['lease_uid']), runtime_token, stopped=True)["state"] == "stopped"
    with DbSession.atomic() as db:
        from langboard_shared.domain.models import BoardAppBinding
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == request.app_key)).first()
        capabilities = list(binding.granted_capabilities)
        binding.granted_capabilities = [c for c in capabilities if c != "execution.run"]
        db.update(binding)
    with pytest.raises(AppGovernanceDenied):
        request_human_runtime_resume(*args, **choice)
    assert current_execution(card.id).generation == observed.generation
    assert not enqueued
    with DbSession.atomic() as db:
        binding.granted_capabilities = capabilities
        db.update(binding)
    result = request_human_runtime_resume(*args, **choice)
    assert result["state"] == "new_run_requested" and result["generation"] == observed.generation + 1
    assert result["started"] is False and result["execution_resumed"] is False
    assert len(enqueued) == 1
    replay = request_human_runtime_resume(*args, **choice)
    assert replay["request_uid"] == result["request_uid"] and replay["changed"] is False
    assert len(enqueued) == 1
    with pytest.raises(AppExecutionRequestConflict):
        request_human_runtime_resume(*args)
    with engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM execution_outbox")).scalar_one() == 1
    with DbSession.use(readonly=False) as db:
        retained = db.exec(SqlBuilder.select.table(AppExecutionLease).where(AppExecutionLease.request_id == request.id)).first()
        assert retained.state == "stopped"
        assert len([entry for entry in retained.history if entry.get("action") == "new_run_requested"]) == 1
