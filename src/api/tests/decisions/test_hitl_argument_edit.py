"""Native edit claims require fresh pending plans, identities, and policy votes."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
import pytest
from langboard.routes.auth import SocketAuthApi as api
from langboard.routes.auth.forms.Socket import SocketBoardChatResumeDecision, SocketEditorResumeClaimForm
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain import models as m
from langboard_shared.domain.models.GraphApprovalRequest import GraphApprovalStatus
from langboard_shared.domain.services.factory.GraphApprovalRequestService import GraphApprovalRequestService
from langboard_shared.domain.services.factory.InternalBotRunService import InternalBotRunService
from test_approval_execution_plan import native  # noqa: F401
from test_decision_flow import respond
from test_decision_hitl import bound, policy, policy_native  # noqa: F401


def edit_decision(approval):
    return dict(
        approved=False,
        rejected=False,
        edit=dict(
            plan_id=approval.execution_plan["plan_id"],
            args_hash=approval.execution_plan["args_hash"],
            tool_args={"form": {"title": "Edited"}},
        ),
    )


def successor(run, approval):
    value = deepcopy(run.request_payload["graph_interrupt"]["value"])
    value.pop("approval_uid", None)
    value.pop("status", None)
    value["request_payload"] = {"tool_args": deepcopy(run.request_payload["resume"]["edit"]["tool_args"])}
    value["execution_plan"] = {
        key: item
        for key, item in approval.execution_plan.items()
        if key not in {"run_id", "native_run_uid", "interrupt_id", "generation"}
    }
    value["execution_plan"].update(
        plan_id="fresh-plan",
        arguments=deepcopy(value["request_payload"]),
        args_hash=hashlib.sha256(
            json.dumps(value["request_payload"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    interrupt = {"id": "fresh-interrupt", "value": value}
    service = GraphApprovalRequestService(lambda _: None, lambda _: None, SimpleNamespace())
    return interrupt, service.prepare_editor_interrupt(run, interrupt)


def test_edit_claim_fresh_pending_separate_reapproval_and_replay(native):  # noqa: F811
    _, repo, run, approval, user, *_ = native
    original = deepcopy(approval.execution_plan)
    decision = edit_decision(approval)
    claimed = repo.claim_editor_resume(approval.id, run.project_id, user.id, decision, 30)
    assert claimed
    assert repo.claim_editor_resume(approval.id, run.project_id, user.id, decision, 30) is None
    assert repo.complete_editor_resume(run.id, claimed.attempt, "", None, None) is None
    interrupt, fresh = successor(claimed, approval)
    result = repo.complete_editor_resume(run.id, claimed.attempt, "", interrupt, fresh)
    assert result and result.newly_applied
    assert result.resolved_approval.status == GraphApprovalStatus.Resolved
    assert result.resolved_approval.execution_plan == original
    assert result.resolved_approval.preview_payload["superseded_by_approval_uid"] == fresh.get_uid()
    assert result.requested_approval.status == GraphApprovalStatus.Pending
    assert result.run.request_payload["resolved_interrupt"]["value"]["resolution"] == "edited"
    assert repo.complete_editor_resume(run.id, claimed.attempt, "", interrupt, fresh).newly_applied is False
    assert (
        repo.claim_editor_resume(approval.id, run.project_id, user.id, dict(approved=True, rejected=False), 30) is None
    )
    assert repo.claim_editor_resume(fresh.id, run.project_id, user.id, dict(approved=True, rejected=False), 30)


def test_duplicate_edits_are_claimed_once(bound):  # noqa: F811
    fixture, repo, run, approval = bound
    user = fixture[2][0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(
            pool.map(
                lambda _: repo.claim_editor_resume(approval.id, run.project_id, user.id, edit_decision(approval), 30),
                range(4),
            )
        )
    assert sum(item is not None for item in claims) == 1


@pytest.mark.parametrize("mutation", ["plan_id", "args_hash", "actor", "mixed", "legacy"])
def test_stale_tampered_or_unauthorized_edit_denied(native, mutation):  # noqa: F811
    engine, repo, run, approval, user, *_ = native
    decision = edit_decision(approval)
    actor = user.id
    if mutation in ("plan_id", "args_hash"):
        decision["edit"][mutation] = "stale"
    elif mutation == "actor":
        actor = run.project_id
    elif mutation == "mixed":
        decision["approved"] = True
        with pytest.raises(ValueError):
            repo.claim_editor_resume(approval.id, run.project_id, actor, decision, 30)
        return
    else:
        from sqlalchemy import update

        with engine.begin() as db:
            db.execute(
                update(m.GraphApprovalRequest)
                .where(m.GraphApprovalRequest.id == approval.id)
                .values(execution_plan=None)
            )
    assert repo.claim_editor_resume(approval.id, run.project_id, actor, decision, 30) is None


def test_policy_edit_requires_fresh_votes_and_cannot_reuse_old_round(bound):  # noqa: F811
    fixture, repo, run, approval = bound
    case = policy(bound)
    case = respond(fixture, respond(fixture, case, 1), 2)
    assert case["state"] == "passed"
    claimed = repo.claim_editor_resume(approval.id, run.project_id, fixture[2][0].id, edit_decision(approval), 30)
    assert claimed and InternalBotRunService.validate_policy_credentials(claimed)
    interrupt, fresh = successor(claimed, approval)
    result = repo.complete_editor_resume(run.id, claimed.attempt, "", interrupt, fresh)
    assert result
    with DbSession.use(readonly=True) as db:
        saved = db.exec(
            SqlBuilder.select.table(m.GraphApprovalRequest).where(m.GraphApprovalRequest.id == fresh.id)
        ).first()
        fresh_case = db.exec(
            SqlBuilder.select.table(m.DecisionCase).where(m.DecisionCase.id == saved.decision_case_id)
        ).first()
        responses = db.exec(
            SqlBuilder.select.table(m.DecisionResponse).where(m.DecisionResponse.decision_id == fresh_case.id)
        ).all()
    assert saved.decision_case_id != result.resolved_approval.decision_case_id
    assert fresh_case.round_id != case["round_id"]
    assert fresh_case.state == "pending" and responses == []
    assert fresh_case.snapshot["native_binding"]["execution_plan"] == fresh.execution_plan
    assert (
        repo.claim_editor_resume(fresh.id, run.project_id, fixture[2][0].id, dict(approved=True, rejected=False), 30)
        is None
    )
    cases = fixture[1].list(fixture[2][0], fixture[3].get_uid(), fixture[5].get_uid())
    summary = next(item for item in cases["items"] if item["uid"] == fresh_case.get_uid())
    passed = respond(fixture, respond(fixture, summary, 1), 2)
    assert passed["state"] == "passed"
    assert repo.claim_editor_resume(fresh.id, run.project_id, fixture[2][0].id, dict(approved=True, rejected=False), 30)


def test_edit_form_is_exclusive_and_bounded():
    valid = dict(
        approved=False,
        rejected=False,
        edit=dict(plan_id="plan", args_hash="hash", tool_args={"form": {"title": "Edit"}}),
    )
    assert SocketBoardChatResumeDecision(**valid).edit
    assert SocketEditorResumeClaimForm(project_uid="123456789ab", resume=valid)
    for change in ({"approved": True}, {"instruction": "also"}, {"reason": "reason"}, {"edit": {"tool_args": {}}}):
        with pytest.raises(ValueError):
            SocketBoardChatResumeDecision(**{**valid, **change})


def test_edit_http_uses_current_native_form_validator_before_claim(native, monkeypatch):  # noqa: F811
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.routing.ApiPermission import ApiPermission
    from pydantic import BaseModel, Field

    _, repo, run, approval, user, project, _ = native

    class CurrentForm(BaseModel):
        title: str = Field(min_length=2)

    def update_card():
        raise AssertionError("Validation must never execute the business endpoint")

    update_card._schema = {"form": CurrentForm, "query": None, "permission": ApiPermission.Edit}
    from fastapi.routing import APIRoute

    route = APIRoute("/fixture/update", update_card, methods=["POST"], name="update_card")
    monkeypatch.setattr(AppRouter.api, "routes", [*AppRouter.api.routes, route])
    monkeypatch.setattr(api, "_authenticate_internal_socket", lambda _: None)
    monkeypatch.setattr(api, "_authenticate_socket_user", lambda _: user)
    monkeypatch.setattr(api, "_get_authorized_editor_run_context", lambda *_: (user, None, None))
    monkeypatch.setattr(api.AuthSecurity, "create_bot_one_time_token", lambda *_: "fixture-server-credential")
    service = SimpleNamespace(
        project=SimpleNamespace(
            get_by_id_like=lambda _: project, get_user_role_actions_by_project=lambda *_: ["update", "card_update"]
        ),
        internal_bot_run=SimpleNamespace(
            get_editor_run_by_approval=repo.get_editor_run_by_approval, claim_editor_resume=repo.claim_editor_resume
        ),
    )
    app = FastAPI()

    @app.post("/claim")
    def claim_http(request: Request, form: SocketEditorResumeClaimForm):
        return api.claim_socket_editor_resume(request, approval.get_uid(), form, service)

    with TestClient(app, raise_server_exceptions=False) as client:
        invalid = edit_decision(approval)
        invalid["edit"]["tool_args"]["form"]["title"] = 123
        denied = client.post("/claim", json={"project_uid": project.get_uid(), "resume": invalid})
        assert denied.status_code == 422
        assert repo.get_by_id(run.id).attempt == 1
        allowed = client.post("/claim", json={"project_uid": project.get_uid(), "resume": edit_decision(approval)})
        assert allowed.status_code == 200
        assert allowed.json()["resume"]["edit"]["tool_args"] == {"form": {"title": "Edited"}}
