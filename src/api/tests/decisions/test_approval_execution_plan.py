"""Native PostgreSQL/SQLite approval plans and HTTP claim/completion receipts."""

import hashlib
import os
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langboard.routes.auth import SocketAuthApi as api
from langboard.routes.auth.forms import SocketBoardChatResumeResultForm, SocketEditorResumeClaimForm
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models as m
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.GraphApprovalRequest import GraphApprovalStatus
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.InternalBotRun import InternalBotRunKind, InternalBotRunStatus
from langboard_shared.domain.services.factory.GraphApprovalRequestService import GraphApprovalRequestService
from langboard_shared.infrastructure.repositories.factory.InternalBotRunRepository import InternalBotRunRepository
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.pool import StaticPool


@pytest.fixture(params=["sqlite", "postgresql"])
def native(request, monkeypatch):
    url = os.getenv("LANGBOARD_APPROVAL_TEST_DATABASE_URL") if request.param == "postgresql" else "sqlite://"
    if not url:
        pytest.skip("Explicit owned PostgreSQL required")
    engine = create_engine(
        url,
        **(
            {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if request.param == "sqlite" else {}
        ),
    )
    required = {
        x.__table__
        for x in (
            m.User,
            m.Project,
            m.SettingRole,
            m.ScimGroup,
            m.ScimGroupMember,
            m.ProjectRole,
            m.ProjectAssignedUser,
            m.BoardTeam,
            m.BoardTeamMember,
            m.Card,
            m.EmployeeMembershipPolicy,
            m.UserIdentityLink,
            m.DecisionCase,
            m.DecisionOption,
            m.DecisionResponse,
            m.InternalBot,
            m.InternalBotRun,
            m.GraphApprovalRequest,
            m.EditorGraphApprovalRequest,
        )
    }
    existing = set(inspect(engine).get_table_names())
    pending = list(required)
    while pending:
        for fk in pending.pop().foreign_keys:
            if fk.column.table not in required:
                required.add(fk.column.table)
                pending.append(fk.column.table)
    m.User.metadata.create_all(engine, tables=list(required))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    with DbSession.use(readonly=False) as db:
        user = m.User(
            firstname="Plan",
            lastname="Owner",
            email=f"{uuid4()}@example.invalid",
            password="test",
            activated_at=SafeDateTime.now(),
        )
        db.insert(user)
        project = m.Project(title="Plan", owner_id=user.id)
        db.insert(project)
        bot = m.InternalBot(
            bot_type=InternalBotType.EditorChat,
            display_name="Plan",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(bot)
    repo = InternalBotRunRepository(lambda _: None, lambda _: None)
    run = m.InternalBotRun(
        request_key=str(uuid4()),
        request_digest="test",
        client_task_id="task",
        user_id=user.id,
        project_id=project.id,
        internal_bot_id=bot.id,
        kind=InternalBotRunKind.EditorChat,
        scope_table="card",
        scope_uid=project.get_uid(),
        graph_thread_id="thread-identity-1",
        graph_session_id="session-identity-1",
        attempt=1,
        status=InternalBotRunStatus.Streaming,
        request_payload={"document_name": "document"},
    )
    with DbSession.use(readonly=False) as db:
        db.insert(run)
    value = {
        "type": "approval_request",
        "origin_type": "editor",
        "scope_table": "card",
        "scope_uid": run.scope_uid,
        "document_name": "document",
        "thread_id": "thread-identity-1",
        "session_id": "session-identity-1",
        "run_id": "graph-run",
        "tool_name": "update_card",
        "action_type": "api_call",
        "api_name": "update_card",
        "permission": "edit",
        "preview": {},
        "request_payload": {"title": "approved"},
    }
    plan = {key: value[key] for key in ("tool_name", "action_type", "api_name", "thread_id", "session_id")}
    plan.update(
        version=1,
        plan_id="plan",
        arguments=deepcopy(value["request_payload"]),
        args_hash=hashlib.sha256(b'{"title":"approved"}').hexdigest(),
    )
    value["execution_plan"] = plan
    interrupt = {"id": "interrupt", "value": value}
    prepared = GraphApprovalRequestService(lambda _: None, lambda _: None, SimpleNamespace())
    approval = prepared.prepare_editor_interrupt(run, interrupt)
    assert repo.pause_editor(run.id, 1, "", interrupt, approval)
    yield engine, repo, run, approval, user, project, interrupt
    # Derived fixtures add approval and lifecycle tables referencing these rows.
    created = set(inspect(engine).get_table_names()) - existing
    owned = [table for table in m.User.metadata.sorted_tables if table.name in created]
    m.User.metadata.drop_all(engine, tables=owned)
    engine.dispose()


def claim(f):
    _, repo, run, approval, user, *_ = f
    return repo.claim_editor_resume(approval.id, run.project_id, user.id, {"approved": True, "rejected": False}, 30)


@pytest.mark.parametrize(
    "field,value",
    [
        ("args_hash", "mutated"),
        ("session_id", "foreign"),
        ("generation", 2),
        ("tool_name", "delete_card"),
        ("plan_id", "other"),
    ],
)
def test_mutated_interrupt_cannot_claim(native, field, value):
    engine, repo, run, approval, *_ = native
    saved = repo.get_by_id(run.id)
    payload = deepcopy(saved.request_payload)
    payload["graph_interrupt"]["value"]["execution_plan"][field] = value
    with engine.begin() as c:
        c.execute(update(m.InternalBotRun).where(m.InternalBotRun.id == run.id).values(request_payload=payload))
    assert claim(native) is None


@pytest.mark.parametrize(
    "status", [GraphApprovalStatus.Cancelled, GraphApprovalStatus.Rejected, GraphApprovalStatus.Expired]
)
def test_closed_approval_cannot_claim(native, status):
    engine, _, _, approval, *_ = native
    with engine.begin() as c:
        c.execute(update(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == approval.id).values(status=status))
    assert claim(native) is None


def test_native_http_claim_complete_replay_and_current_authority(native, monkeypatch):
    _, repo, run, approval, user, project, _ = native
    service = SimpleNamespace(
        internal_bot_run=SimpleNamespace(
            get_editor_run_by_approval=repo.get_editor_run_by_approval,
            claim_editor_resume=repo.claim_editor_resume,
            get_editor_run=repo.get_by_id,
            complete_editor_resume=lambda rid, attempt, text, interrupt: repo.complete_editor_resume(
                rid, attempt, text, interrupt, None
            ),
        ),
        graph_approval_request=SimpleNamespace(get_api_response=lambda approval: approval.api_response()),
        project=SimpleNamespace(
            get_by_id_like=lambda _: project, get_user_role_actions_by_project=lambda *_: ["update"]
        ),
    )
    monkeypatch.setattr(api, "_authenticate_internal_socket", lambda _: None)
    monkeypatch.setattr(api, "_authenticate_socket_user", lambda _: user)
    monkeypatch.setattr(api, "_get_authorized_editor_run_context", lambda *_: (user, None, None))
    monkeypatch.setattr(api.Auth, "get_user_by_id", lambda _: user)
    monkeypatch.setattr(api.AuthSecurity, "create_bot_one_time_token", lambda *_: "test-credential")
    monkeypatch.setattr(api.GraphApprovalPublisher, "updated", lambda *_: None)
    app = FastAPI()

    @app.post("/claim")
    def http_claim(request: Request, form: SocketEditorResumeClaimForm):
        return api.claim_socket_editor_resume(request, approval.get_uid(), form, service)

    @app.post("/complete")
    def http_complete(request: Request, form: SocketBoardChatResumeResultForm):
        return api.complete_socket_editor_resume(request, run.get_uid(), form, service)

    with TestClient(app) as client:
        response = client.post(
            "/claim", json={"project_uid": project.get_uid(), "resume": {"approved": True, "rejected": False}}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resume"]["execution_plan"] == approval.execution_plan
        assert body["resume"]["approval_uid"] == approval.get_uid()
        assert claim(native) is None
        completion = {
            "attempt": body["attempt"],
            "thread_id": "thread-identity-1",
            "session_id": "session-identity-1",
            "response_text": "done",
        }
        # A revoked approver is checked before any completion receipt mutation.
        monkeypatch.setattr(api.Auth, "get_user_by_id", lambda _: None)
        assert client.post("/complete", json=completion).status_code == 403
        assert repo.get_by_id(run.id).status == InternalBotRunStatus.Resuming
        monkeypatch.setattr(api.Auth, "get_user_by_id", lambda _: user)
        first = client.post("/complete", json=completion)
        assert first.status_code == 200, first.text
        assert first.json()["newly_applied"] is True
        again = client.post("/complete", json=completion)
        assert again.status_code == 200, again.text
        assert again.json()["newly_applied"] is False
        assert repo.complete_editor_resume(run.id, body["attempt"], "different", None, None) is None


@pytest.mark.parametrize(
    "tamper", ["arguments", "approval_identity", "generation", "expired", "lease_expired", "multi_hitl"]
)
def test_completion_rejects_changed_claim_snapshot(native, tamper):
    from datetime import timedelta

    engine, repo, run, approval, *_ = native
    claimed = claim(native)
    assert claimed
    if tamper == "expired":
        with engine.begin() as c:
            c.execute(
                update(m.GraphApprovalRequest)
                .where(m.GraphApprovalRequest.id == approval.id)
                .values(expires_at=SafeDateTime.now() - timedelta(seconds=1))
            )
    elif tamper == "lease_expired":
        with engine.begin() as c:
            c.execute(
                update(m.InternalBotRun)
                .where(m.InternalBotRun.id == run.id)
                .values(lease_expires_at=SafeDateTime.now() - timedelta(seconds=1))
            )
    else:
        payload = deepcopy(claimed.request_payload)
        if tamper == "arguments":
            payload["graph_interrupt"]["value"]["request_payload"]["title"] = "mutated"
        elif tamper == "approval_identity":
            payload["resume_approval_uid"] = "other"
        elif tamper == "generation":
            payload["resume_execution_plan"]["generation"] = 99
        else:
            payload["graph_interrupt"]["value"]["decision_case_uid"] = "multi-party"
        with engine.begin() as c:
            c.execute(update(m.InternalBotRun).where(m.InternalBotRun.id == run.id).values(request_payload=payload))
    assert repo.complete_editor_resume(run.id, claimed.attempt, "done", None, None) is None
    assert repo.get_by_id(run.id).status == InternalBotRunStatus.Resuming


def test_multi_hitl_attachment_cannot_use_legacy_claim(native):
    engine, repo, run, approval, *_ = native
    payload = deepcopy(repo.get_by_id(run.id).request_payload)
    payload["graph_interrupt"]["value"].pop("execution_plan")
    payload["graph_interrupt"]["value"]["decision_uid"] = "multi-party"
    with engine.begin() as c:
        c.execute(update(m.InternalBotRun).where(m.InternalBotRun.id == run.id).values(request_payload=payload))
        c.execute(
            update(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == approval.id).values(execution_plan=None)
        )
    assert claim(native) is None


def test_nondurable_plan_cannot_silently_use_legacy_path():
    service = GraphApprovalRequestService(lambda _: None, lambda _: None, SimpleNamespace())
    assert service.create_from_interrupt(None, {"type": "approval_request", "execution_plan": {"version": 1}}) is None


@pytest.mark.parametrize(
    "mutation", [{"version": True}, {"arguments": {"bad": float("nan")}}, {"native_run_uid": "foreign"}]
)
def test_malformed_native_snapshot_rejected(native, mutation):
    _, _, run, _, _, _, interrupt = native
    changed = deepcopy(interrupt)
    changed["value"]["execution_plan"].update(mutation)
    with pytest.raises(ValueError):
        m.GraphApprovalRequest.freeze_execution_plan(changed["value"], changed["id"], run)


def test_additive_migration_retains_plan_and_rejects_loss(native):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine, _, _, approval, *_ = native
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024100000-6efa789b0123.py"
    spec = importlib.util.spec_from_file_location("approval_plan_delta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "5def678a9012"
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE graph_approval_request DROP COLUMN execution_plan")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(
            update(m.GraphApprovalRequest)
            .where(m.GraphApprovalRequest.id == approval.id)
            .values(execution_plan=approval.execution_plan)
        )
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Cannot discard"):
                migration.downgrade()


@pytest.mark.parametrize("origin", ["editor", "chat"])
@pytest.mark.parametrize("deadline", ["not-a-deadline", "", 0, False, {}, [], "2026-13-01T00:00:00Z"])
def test_malformed_deadline_cannot_create_unlimited_approval(native, deadline, origin):
    _, repo, run, _, _, _, interrupt = native
    changed = deepcopy(interrupt)
    changed["value"]["expires_at"] = deadline
    service = GraphApprovalRequestService(lambda _: None, lambda _: None, SimpleNamespace())
    if origin == "chat":
        changed["value"]["origin_type"] = "chat"
        run = deepcopy(run)
        run.chat_session_id = run.user_id
        run.ai_chat_history_id = run.user_id
    prepare = service.prepare_board_chat_interrupt if origin == "chat" else service.prepare_editor_interrupt
    with pytest.raises(ValueError, match="approval expiry"):
        prepare(run, changed)
    assert repo.get_by_id(run.id).status == InternalBotRunStatus.AwaitingApproval


@pytest.mark.parametrize("deadline", [None, "2027-01-02T03:04:05Z", "2027-01-02T12:04:05+09:00"])
def test_approval_deadline_preserves_optional_and_utc_semantics(native, deadline):
    from datetime import datetime, timezone

    _, _, run, _, _, _, interrupt = native
    changed = deepcopy(interrupt)
    changed["value"]["expires_at"] = deadline
    service = GraphApprovalRequestService(lambda _: None, lambda _: None, SimpleNamespace())
    approval = service.prepare_editor_interrupt(run, changed)
    expected = None if deadline is None else datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert approval.expires_at == expected
