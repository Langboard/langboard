"""Actual SQLite native policy, requester claim and HTTP authority boundaries."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models as m
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.InternalBotRun import InternalBotRunKind, InternalBotRunStatus
from langboard_shared.domain.services.DecisionFlow import DecisionConflict
from langboard_shared.domain.services.factory.GraphApprovalRequestService import GraphApprovalRequestService
from langboard_shared.domain.services.factory.InternalBotRunService import InternalBotRunService
from langboard_shared.infrastructure.repositories.factory.InternalBotRunRepository import InternalBotRunRepository
from sqlalchemy import update
from test_decision_flow import create, respond
from test_decision_flow import native as policy_native  # noqa: F401


@pytest.fixture
def bound(policy_native):  # noqa: F811
    engine, flow, users, board, _, card = policy_native
    required = {
        x.__table__ for x in (m.InternalBot, m.InternalBotRun, m.GraphApprovalRequest, m.EditorGraphApprovalRequest)
    }
    pending = list(required)
    while pending:
        for fk in pending.pop().foreign_keys:
            if fk.column.table not in required:
                required.add(fk.column.table)
                pending.append(fk.column.table)
    m.User.metadata.create_all(engine, tables=list(required))
    with DbSession.use(readonly=False) as db:
        role = db.exec(maybe_role(users[0], board)).first()
        role.actions = ["read", "update", "card_update"]
        db.update(role)
        bot = m.InternalBot(
            bot_type=InternalBotType.EditorChat,
            display_name="Native policy",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(bot)
        run = m.InternalBotRun(
            request_key=str(uuid4()),
            request_digest="test",
            client_task_id="task",
            user_id=users[0].id,
            project_id=board.id,
            internal_bot_id=bot.id,
            kind=InternalBotRunKind.EditorChat,
            scope_table="card",
            scope_uid=card.get_uid(),
            graph_thread_id="thread-policy",
            graph_session_id="session-policy",
            attempt=1,
            status=InternalBotRunStatus.Streaming,
            request_payload={"document_name": "document"},
        )
        db.insert(run)
    value = dict(
        type="approval_request",
        origin_type="editor",
        scope_table="card",
        scope_uid=card.get_uid(),
        document_name="document",
        thread_id=run.graph_thread_id,
        session_id=run.graph_session_id,
        run_id="graph",
        tool_name="update_card",
        action_type="api_call",
        api_name="update_card",
        permission="edit",
        preview={},
        request_payload={"title": "approved"},
    )
    import hashlib

    value["execution_plan"] = dict(
        version=1,
        plan_id="native-policy-plan",
        tool_name="update_card",
        action_type="api_call",
        api_name="update_card",
        arguments={"title": "approved"},
        args_hash=hashlib.sha256(b'{"title":"approved"}').hexdigest(),
        thread_id=run.graph_thread_id,
        session_id=run.graph_session_id,
    )
    interrupt = {"id": "native-policy-interrupt", "value": value}
    prepared = GraphApprovalRequestService(lambda _: None, lambda _: None, SimpleNamespace())
    approval = prepared.prepare_editor_interrupt(run, interrupt)
    repo = InternalBotRunRepository(lambda _: None, lambda _: None)
    assert repo.pause_editor(run.id, 1, "", interrupt, approval)
    yield policy_native, repo, run, approval


def maybe_role(user, board):
    from langboard_shared.core.db import SqlBuilder

    return SqlBuilder.select.table(m.ProjectRole).where(
        m.ProjectRole.user_id == user.id, m.ProjectRole.project_id == board.id
    )


def policy(bound, tree=None, participants=None, **changes):
    fixture, _, _, approval = bound
    users = fixture[2]
    participants = participants or [u.get_uid() for u in users[1:4]]
    tree = tree or dict(op="k_of_n", required=2, children=[{"actor_uid": uid} for uid in participants])
    return create(
        fixture,
        participants=participants,
        policy=tree,
        source_kind="graph_approval",
        approval_uid=approval.get_uid(),
        **changes,
    )


def claim(bound, actor=0):
    fixture, repo, run, approval = bound
    return repo.claim_editor_resume(
        approval.id, run.project_id, fixture[2][actor].id, {"approved": True, "rejected": False}, 30
    )


def passed(bound):
    case = policy(bound)
    case = respond(bound[0], case, 1)
    return respond(bound[0], case, 2)


def test_threshold_manual_requester_claim_and_completion_replay(bound):
    case = policy(bound)
    assert claim(bound) is None
    case = respond(bound[0], case, 1)
    assert case["state"] == "pending" and claim(bound) is None
    case = respond(bound[0], case, 2)
    assert case["state"] == "passed"
    assert claim(bound, 2) is None
    claimed = claim(bound)
    assert claimed and claimed.user_id == bound[0][2][0].id
    receipt = claimed.request_payload["resume_decision"]
    assert receipt["final_responder_uid"] == bound[0][2][2].get_uid()
    assert receipt["requester_uid"] == receipt["creator_uid"] == bound[0][2][0].get_uid()
    assert InternalBotRunService.validate_policy_credentials(claimed)
    assert claim(bound) is None
    result = bound[1].complete_editor_resume(claimed.id, claimed.attempt, "done", None, None)
    assert result and result.newly_applied
    replay = bound[1].complete_editor_resume(claimed.id, claimed.attempt, "done", None, None)
    assert replay and not replay.newly_applied


def test_nested_and_sequence_policy(bound):
    ids = [u.get_uid() for u in bound[0][2][1:5]]
    dev = dict(op="k_of_n", required=2, children=[{"actor_uid": uid} for uid in ids[:3]])
    case = policy(bound, dict(op="all", required=0, children=[dev, {"actor_uid": ids[3]}]), ids)
    case = respond(bound[0], case, 1)
    case = respond(bound[0], case, 2)
    assert claim(bound) is None
    case = respond(bound[0], case, 4)
    assert claim(bound)


def test_sequence_early_response_rejected(bound):
    ids = [u.get_uid() for u in bound[0][2][1:3]]
    case = policy(bound, dict(op="sequence", required=0, children=[{"actor_uid": uid} for uid in ids]), ids)
    with pytest.raises(DecisionConflict):
        respond(bound[0], case, 2)
    case = respond(bound[0], case, 1)
    assert claim(bound) is None
    case = respond(bound[0], case, 2)
    assert claim(bound)


@pytest.mark.parametrize(
    "mutation", ["participant", "requester", "deadline", "approval_expiry", "plan", "args", "generation", "policy"]
)
def test_dispatch_revalidates_current_authority_and_frozen_input(bound, mutation):
    passed(bound)
    fixture, repo, run, approval = bound
    engine, _, users, board, *_ = fixture
    with engine.begin() as db:
        if mutation in ("participant", "requester"):
            actor = users[1] if mutation == "participant" else users[0]
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == actor.id).values(actions=[]))
        elif mutation == "deadline":
            db.execute(update(m.DecisionCase).values(deadline=SafeDateTime.now() - timedelta(seconds=1)))
        elif mutation == "approval_expiry":
            db.execute(update(m.GraphApprovalRequest).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
        elif mutation == "policy":
            db.execute(update(m.DecisionCase).values(policy_revision="changed"))
        elif mutation == "generation":
            db.execute(update(m.InternalBotRun).where(m.InternalBotRun.id == run.id).values(attempt=4))
        else:
            plan = deepcopy(approval.execution_plan)
            if mutation == "plan":
                plan["plan_id"] = "changed"
            else:
                plan["arguments"] = {"title": "changed"}
            db.execute(
                update(m.GraphApprovalRequest)
                .where(m.GraphApprovalRequest.id == approval.id)
                .values(execution_plan=plan)
            )
    assert claim(bound) is None


def test_parallel_and_replayed_claim_exactly_one(bound):
    passed(bound)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: claim(bound), range(4)))
    assert sum(result is not None for result in results) == 1
    assert claim(bound) is None


@pytest.mark.parametrize("mutation", ["receipt", "revoke", "expiry"])
def test_credential_and_completion_reject_policy_bypass(bound, mutation):
    passed(bound)
    claimed = claim(bound)
    engine, _, users, *_ = bound[0]
    with engine.begin() as db:
        if mutation == "receipt":
            payload = deepcopy(claimed.request_payload)
            payload.pop("resume_decision")
            db.execute(
                update(m.InternalBotRun).where(m.InternalBotRun.id == claimed.id).values(request_payload=payload)
            )
        elif mutation == "revoke":
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
        else:
            db.execute(update(m.DecisionCase).values(deadline=SafeDateTime.now() - timedelta(seconds=1)))
    assert not InternalBotRunService.validate_policy_credentials(claimed)
    assert bound[1].complete_editor_resume(claimed.id, claimed.attempt, "done", None, None) is None


@pytest.fixture(params=["editor", "chat"])
def native_policy(bound, request):
    if request.param == "editor":
        return bound
    fixture, repo, run, approval = bound
    engine = fixture[0]
    required = {x.__table__ for x in (m.ChatSession, m.ChatHistory, m.ProjectChatSession, m.ChatGraphApprovalRequest)}
    m.User.metadata.create_all(engine, tables=list(required))
    from langboard_shared.core.db import ChatContentModel
    from langboard_shared.domain.models.GraphApprovalRequest import GraphApprovalRequestType

    with DbSession.use(readonly=False) as db:
        session = m.ChatSession(user_id=run.user_id)
        db.insert(session)
        project_session = m.ProjectChatSession(project_id=run.project_id, chat_session_id=session.id)
        db.insert(project_session)
        saved = repo.get_by_id(run.id)
        message = m.ChatHistory(
            chat_session_id=session.id,
            is_received=True,
            message=ChatContentModel(content="", graph_interrupt=saved.request_payload["graph_interrupt"]),
        )
        db.insert(message)
        run.kind = InternalBotRunKind.BoardChat
        run.status = InternalBotRunStatus.AwaitingApproval
        run.chat_session_id = session.id
        run.ai_chat_history_id = message.id
        db.update(run)
        approval.request_type = GraphApprovalRequestType.Chat
        approval.run_id = run.client_task_id
        db.update(approval)
        db.insert(
            m.ChatGraphApprovalRequest(
                approval_request_id=approval.id,
                scope_table="card",
                scope_id=fixture[5].id,
                chat_session_id=session.id,
                chat_history_id=message.id,
            )
        )
    return bound


def test_authenticated_http_requester_policy_boundary(native_policy, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from langboard.routes.auth import SocketAuthApi as api
    from langboard.routes.auth.forms import (
        SocketBoardChatResumeClaimForm,
        SocketBoardChatResumeResultForm,
        SocketEditorResumeClaimForm,
    )

    bound = native_policy
    fixture, repo, run, approval = bound
    _, flow, users, board, _, _ = fixture
    is_chat = run.kind == InternalBotRunKind.BoardChat
    case = policy(bound)
    current_actor = [users[0]]
    tokens = []
    service = SimpleNamespace(
        internal_bot_run=SimpleNamespace(
            get_editor_run_by_approval=repo.get_editor_run_by_approval,
            claim_editor_resume=repo.claim_editor_resume,
            get_editor_run=repo.get_by_id,
            complete_editor_resume=lambda rid, attempt, text, interrupt: repo.complete_editor_resume(
                rid, attempt, text, interrupt, None
            ),
            get_board_chat_run_by_ai_message=repo.get_board_chat_run_by_ai_message,
            claim_board_chat_resume=repo.claim_board_chat_resume,
            get_board_chat_run=repo.get_board_chat_run,
            complete_board_chat_resume=lambda rid, attempt, text, interrupt: repo.complete_board_chat_resume(
                rid, attempt, text, interrupt, None
            ),
        ),
        graph_approval_request=SimpleNamespace(get_api_response=lambda approval: approval.api_response()),
        project=flow.service.project,
    )
    monkeypatch.setattr(api, "_authenticate_internal_socket", lambda _: None)
    monkeypatch.setattr(api, "_authenticate_socket_user", lambda _: current_actor[0])
    monkeypatch.setattr(api, "_get_authorized_editor_run_context", lambda *_: (users[0], None, None))
    monkeypatch.setattr(api, "_authorize_board_chat_scope", lambda *_: None)
    monkeypatch.setattr(api, "is_subscription_authorized", lambda *_: True)
    monkeypatch.setattr(api.Auth, "get_user_by_id", lambda _: users[0])
    monkeypatch.setattr(
        api.AuthSecurity, "create_bot_one_time_token", lambda uid, *_: (tokens.append(uid) or "test-token")
    )
    monkeypatch.setattr(api.GraphApprovalPublisher, "updated", lambda *_: None)
    app = FastAPI()
    if is_chat:

        @app.post("/claim")
        def http_claim(request: Request, form: SocketBoardChatResumeClaimForm):
            return api.claim_socket_board_chat_resume(request, board.get_uid(), form, service)

        @app.post("/complete")
        def http_complete(request: Request, form: SocketBoardChatResumeResultForm):
            return api.complete_socket_board_chat_resume(request, board.get_uid(), run.get_uid(), form, service)

        payload = {
            "message_uid": run.ai_chat_history_id.to_short_code(),
            "thread_id": run.graph_thread_id,
            "session_id": run.graph_session_id,
            "approval_uid": approval.get_uid(),
            "resume": {"approved": True, "rejected": False},
        }
    else:

        @app.post("/claim")
        def http_claim(request: Request, form: SocketEditorResumeClaimForm):
            return api.claim_socket_editor_resume(request, approval.get_uid(), form, service)

        @app.post("/complete")
        def http_complete(request: Request, form: SocketBoardChatResumeResultForm):
            return api.complete_socket_editor_resume(request, run.get_uid(), form, service)

        payload = {"project_uid": board.get_uid(), "resume": {"approved": True, "rejected": False}}
    with TestClient(app) as client:
        assert client.post("/claim", json=payload).status_code == 409
        assert not tokens
        case = respond(fixture, case, 1)
        assert client.post("/claim", json=payload).status_code == 409
        assert not tokens
        case = respond(fixture, case, 2)
        current_actor[0] = users[2]
        assert client.post("/claim", json=payload).status_code in (403, 409)
        assert not tokens
        current_actor[0] = users[0]
        response = client.post("/claim", json=payload)
        assert response.status_code == 200, response.text
        assert tokens == [int(users[0].id)]
        assert response.json()["resume"]["execution_plan"] == approval.execution_plan
        assert client.post("/claim", json=payload).status_code == 409
        assert tokens == [int(users[0].id)]
        completion = {
            "attempt": response.json()["attempt"],
            "thread_id": run.graph_thread_id,
            "session_id": run.graph_session_id,
            "response_text": "native policy completed",
        }
        first = client.post("/complete", json=completion)
        assert first.status_code == 200, first.text
        assert first.json()["newly_applied"]
        replay = client.post("/complete", json=completion)
        assert replay.status_code == 200, replay.text
        assert not replay.json()["newly_applied"]


@pytest.mark.parametrize("choice", ["reject", "needs_info", "abstain"])
def test_nonapproval_responses_never_claim(bound, choice):
    from test_decision_flow import form

    case = policy(bound)
    case = respond(bound[0], case, 1, form(case, choice=choice))
    if case["state"] in ("pending", "needs_info"):
        case = respond(bound[0], case, 2, form(case, choice=choice))
    assert claim(bound) is None


def test_source_binding_and_response_uuid_replay(bound):
    from langboard.routes.board.forms.Decision import CreateDecisionForm
    from langboard_shared.domain.services.DecisionFlow import card_target_revision
    from test_decision_flow import form

    fixture, _, _, approval = bound
    _, flow, users, board, _, card = fixture
    # Policy creator differs from the durable requesting user and final responder.
    with DbSession.use(readonly=False) as db:
        role = db.exec(maybe_role(users[4], board)).first()
        role.actions = ["read", "card_update"]
        db.update(role)
    ids = [user.get_uid() for user in users[1:4]]
    payload = CreateDecisionForm(
        request_id=uuid4(),
        source_kind="graph_approval",
        source_ref=card.get_uid(),
        approval_uid=approval.get_uid(),
        question="Bound policy",
        target_revision=card_target_revision(card),
        participant_uids=ids,
        policy=dict(op="k_of_n", required=2, children=[{"actor_uid": uid} for uid in ids]),
    )
    case = flow.create(users[4], board.get_uid(), card.get_uid(), payload)
    replay = flow.create(users[4], board.get_uid(), card.get_uid(), payload)
    assert replay["uid"] == case["uid"] and replay["replayed"]
    response_form = form(case)
    case = respond(fixture, case, 1, response_form)
    replay = respond(fixture, case, 1, response_form)
    assert replay["replayed"] and replay["revision"] == case["revision"]
    assert claim(bound) is None
    case = respond(fixture, case, 2)
    claimed = claim(bound)
    receipt = claimed.request_payload["resume_decision"]
    assert receipt["requester_uid"] == users[0].get_uid()
    assert receipt["creator_uid"] == users[4].get_uid()
    assert receipt["final_responder_uid"] == users[2].get_uid()


def test_additive_policy_migration_null_compatibility_and_retention(tmp_path):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024120000-8abc901d2345.py"
    spec = importlib.util.spec_from_file_location("native_policy_delta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE decision_case(id BIGINT PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE graph_approval_request(id BIGINT PRIMARY KEY, thread_id TEXT NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO graph_approval_request VALUES (1,'retained')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT thread_id,decision_case_id FROM graph_approval_request").one() == (
            "retained",
            None,
        )
        connection.exec_driver_sql("INSERT INTO graph_approval_request VALUES (2,'legacy',NULL)")
        connection.exec_driver_sql("INSERT INTO decision_case VALUES (42)")
        connection.exec_driver_sql("UPDATE graph_approval_request SET decision_case_id=42 WHERE id=1")
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("UPDATE graph_approval_request SET decision_case_id=42 WHERE id=2")
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("UPDATE graph_approval_request SET decision_case_id=99 WHERE id=2")
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Cannot discard"):
                migration.downgrade()
    engine.dispose()


@pytest.mark.parametrize("permission", ["delete", "create", "unknown"])
def test_plan_specific_mutation_authority_required(bound, permission):
    passed(bound)
    engine = bound[0][0]
    with engine.begin() as db:
        db.execute(
            update(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == bound[3].id).values(permission=permission)
        )
    assert claim(bound) is None


def test_expired_lease_recovery_does_not_repeat_claim(bound):
    passed(bound)
    claimed = claim(bound)
    engine = bound[0][0]
    with engine.begin() as db:
        db.execute(
            update(m.InternalBotRun)
            .where(m.InternalBotRun.id == claimed.id)
            .values(lease_expires_at=SafeDateTime.now() - timedelta(seconds=1))
        )
    assert not InternalBotRunService.validate_policy_credentials(claimed)
    recovered = bound[1].mark_expired_uncertain(SafeDateTime.now(), 100)
    assert len(recovered) == 1 and recovered[0].status == InternalBotRunStatus.Uncertain
    assert claim(bound) is None
    assert bound[1].complete_editor_resume(claimed.id, claimed.attempt, "late", None, None) is None


@pytest.mark.parametrize("revoked", [None, "requester", "participant", "visibility", "receipt", "plan"])
def test_approved_card_mutation_completion_retains_current_fences(native_policy, revoked):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.services.DecisionFlow import card_target_revision

    bound = native_policy
    passed(bound)
    fixture, repo, run, _ = bound
    engine, flow, users, _, _, card = fixture
    is_chat = run.kind == InternalBotRunKind.BoardChat
    if is_chat:
        claimed = repo.claim_board_chat_resume(
            run.project_id, run.user_id, run.ai_chat_history_id, run.graph_thread_id,
            run.graph_session_id, bound[3].id, {"approved": True, "rejected": False}, 30,
        )
    else:
        claimed = claim(bound)
    assert claimed
    before = card_target_revision(card)
    # Execute the frozen approved arguments through the native card repository.
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(m.Card).where(m.Card.id == card.id)).first()
        current.title = claimed.request_payload["resume_execution_plan"]["arguments"]["title"]
        flow.service.card.repo.card.update(current)
    with DbSession.use(readonly=True) as db:
        current = db.exec(SqlBuilder.select.table(m.Card).where(m.Card.id == card.id)).first()
        assert current.title == "approved" and card_target_revision(current) != before
    # Credentials remain a pre-dispatch boundary, even after the effect occurred.
    assert not InternalBotRunService.validate_policy_credentials(claimed)
    with engine.begin() as db:
        if revoked in ("requester", "participant"):
            actor = users[0] if revoked == "requester" else users[1]
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == actor.id).values(actions=[]))
        elif revoked == "visibility":
            db.execute(update(m.Card).where(m.Card.id == card.id).values(visibility="PRIVATE", owner_user_id=users[0].id, created_by_user_id=users[0].id))
        elif revoked == "receipt":
            payload = deepcopy(claimed.request_payload)
            payload["resume_decision"]["target_revision"] = "forged"
            db.execute(update(m.InternalBotRun).where(m.InternalBotRun.id == claimed.id).values(request_payload=payload))
        elif revoked == "plan":
            plan = deepcopy(bound[3].execution_plan)
            plan["plan_id"] = "changed"
            db.execute(update(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == bound[3].id).values(execution_plan=plan))
    complete = repo.complete_board_chat_resume if is_chat else repo.complete_editor_resume
    if revoked:
        assert complete(claimed.id, claimed.attempt, "approved mutation done", None, None) is None
    else:
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(
                lambda _: complete(claimed.id, claimed.attempt, "approved mutation done", None, None), range(4)
            ))
        assert all(results)
        assert sum(result.newly_applied for result in results) == 1
        replay = complete(claimed.id, claimed.attempt, "approved mutation done", None, None)
        assert replay and not replay.newly_applied
        assert complete(claimed.id, claimed.attempt, "different result", None, None) is None


@pytest.mark.parametrize("already_claimed", [False, True])
def test_native_delete_scope_cancellation_and_soft_delete_fail_closed(native_policy, monkeypatch, already_claimed):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models.GraphApprovalRequest import GraphApprovalStatus
    from langboard_shared.publishers import GraphApprovalPublisher

    fixture, repo, run, approval = native_policy
    engine, flow, users, board, _, card = fixture
    required = {model.__table__ for name, model in vars(m).items()
                if name.endswith("GraphApprovalRequest") and hasattr(model, "__table__")}
    pending = list(required)
    while pending:
        for foreign in pending.pop().foreign_keys:
            if foreign.column.table not in required:
                required.add(foreign.column.table)
                pending.append(foreign.column.table)
    m.User.metadata.create_all(engine, tables=list(required))
    passed(native_policy)
    is_chat = run.kind == InternalBotRunKind.BoardChat
    claimed = None
    if already_claimed:
        claimed = (repo.claim_board_chat_resume(
            run.project_id, run.user_id, run.ai_chat_history_id, run.graph_thread_id,
            run.graph_session_id, approval.id, {"approved": True, "rejected": False}, 30,
        ) if is_chat else claim(native_policy))
        assert claimed
    service = flow.service.graph_approval_request
    monkeypatch.setattr(GraphApprovalPublisher, "updated", lambda *_: None)
    monkeypatch.setattr(service, "_GraphApprovalRequestService__acknowledge_graph", lambda *_: None)
    # These are the native deletion boundaries in CardService._delete_card.
    # Exercise their actual persistence without unrelated notification/cleanup.
    service.cancel_pending_by_scope(board, "card", card.get_uid(), reason="card deleted")
    flow.service.card.repo.card.delete(card)
    with DbSession.use(readonly=True) as db:
        stored = db.exec(SqlBuilder.select.table(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == approval.id)).first()
        saved = repo.get_by_id(run.id)
        deleted = db.exec(SqlBuilder.select.table(m.Card).where(m.Card.id == card.id)).first()
        assert deleted is None  # Native soft-delete filtering.
        assert stored.status == GraphApprovalStatus.Cancelled
        assert saved.status == InternalBotRunStatus.Cancelled
        assert saved.lease_expires_at is None and saved.finished_at is not None
        if claimed:
            assert saved.attempt == claimed.attempt
            for key in ("resume_decision", "resume_execution_plan", "resume_approval_uid", "resume"):
                assert saved.request_payload[key] == claimed.request_payload[key]
    if is_chat:
        assert repo.claim_board_chat_resume(
            run.project_id, run.user_id, run.ai_chat_history_id, run.graph_thread_id,
            run.graph_session_id, approval.id, {"approved": True, "rejected": False}, 30,
        ) is None
    else:
        assert claim(native_policy) is None
    if claimed:
        assert not InternalBotRunService.validate_policy_credentials(claimed)
        complete = repo.complete_board_chat_resume if is_chat else repo.complete_editor_resume
        assert complete(claimed.id, claimed.attempt, "deleted", None, None) is None


@pytest.mark.parametrize("boundary", ["attempt", "approval", "scope", "expiry", "rollback", "host_rollback", "completed", "race"])
def test_claimed_lifecycle_cancellation_fences(native_policy, monkeypatch, boundary):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models.GraphApprovalRequest import GraphApprovalStatus

    fixture, repo, run, approval = native_policy
    engine, _, users, _, _, card = fixture
    passed(native_policy)
    is_chat = run.kind == InternalBotRunKind.BoardChat
    claimed = (repo.claim_board_chat_resume(
        run.project_id, run.user_id, run.ai_chat_history_id, run.graph_thread_id,
        run.graph_session_id, approval.id, {"approved": True, "rejected": False}, 30,
    ) if is_chat else claim(native_policy))
    assert claimed
    close = repo.close_board_chat_approval if is_chat else repo.close_editor_approval
    complete = repo.complete_board_chat_resume if is_chat else repo.complete_editor_resume
    payload = deepcopy(claimed.request_payload)
    if boundary in ("attempt", "approval", "scope"):
        with engine.begin() as db:
            if boundary == "attempt":
                values = {"attempt": claimed.attempt + 1}
            elif boundary == "scope":
                values = {"scope_uid": users[4].get_uid()}
            else:
                payload["resume_approval_uid"] = users[4].get_uid()
                values = {"request_payload": payload}
            db.execute(update(m.InternalBotRun).where(m.InternalBotRun.id == run.id).values(**values))
        assert close(approval.id, GraphApprovalStatus.Cancelled, "card deleted") is None
    elif boundary == "expiry":
        assert close(approval.id, GraphApprovalStatus.Expired, "expired") is None
    elif boundary == "rollback":
        original = DbSession.update

        def fail_run(db, model):
            if isinstance(model, m.InternalBotRun):
                raise RuntimeError("cancel run persistence failed")
            original(db, model)

        with monkeypatch.context() as patcher:
            patcher.setattr(DbSession, "update", fail_run)
            with pytest.raises(RuntimeError, match="cancel run persistence failed"):
                close(approval.id, GraphApprovalStatus.Cancelled, "card deleted")
    elif boundary == "host_rollback":
        with pytest.raises(RuntimeError, match="host deletion failed"):
            with DbSession.atomic():
                assert close(approval.id, GraphApprovalStatus.Cancelled, "card deleted")
                native_policy[0][1].service.card.repo.card.delete(card)
                raise RuntimeError("host deletion failed")
        with DbSession.use(readonly=True) as db:
            assert db.exec(SqlBuilder.select.table(m.Card).where(m.Card.id == card.id)).first()
    elif boundary == "completed":
        first = complete(run.id, claimed.attempt, "done", None, None)
        assert first and first.newly_applied
        assert close(approval.id, GraphApprovalStatus.Cancelled, "card deleted") is None
        replay = complete(run.id, claimed.attempt, "done", None, None)
        assert replay and not replay.newly_applied
        return
    else:
        with ThreadPoolExecutor(max_workers=2) as workers:
            cancellation = workers.submit(close, approval.id, GraphApprovalStatus.Cancelled, "card deleted")
            completion = workers.submit(complete, run.id, claimed.attempt, "done", None, None)
            cancelled, completed = cancellation.result(), completion.result()
        assert (cancelled is not None) != (completed is not None)
        saved = repo.get_by_id(run.id)
        assert saved.lease_expires_at is None
        if completed:
            assert saved.status == InternalBotRunStatus.Completed
            replay = complete(run.id, claimed.attempt, "done", None, None)
            assert replay and not replay.newly_applied
        else:
            assert saved.status == InternalBotRunStatus.Cancelled
            assert complete(run.id, claimed.attempt, "done", None, None) is None
        return
    with DbSession.use(readonly=True) as db:
        stored = db.exec(SqlBuilder.select.table(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == approval.id)).first()
        saved = repo.get_by_id(run.id)
        assert stored.status == GraphApprovalStatus.Pending
        assert saved.status == InternalBotRunStatus.Resuming and saved.lease_expires_at is not None
        assert saved.request_payload == payload
        if is_chat:
            message = db.exec(SqlBuilder.select.table(m.ChatHistory).where(m.ChatHistory.id == run.ai_chat_history_id)).first()
            assert message.message.graph_interrupt["value"]["status"] == "pending"
