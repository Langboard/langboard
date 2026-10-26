"""Native optional policy authority at the existing requester claim boundary.

Responses never dispatch. The approval FK is the authoritative attachment; caller
JSON cannot supply a binding proof. Revalidate the persisted round at claim and
completion, using the original requester's current card and mutation authority.
"""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import text
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ..models import (
    ChatGraphApprovalRequest,
    DecisionCase,
    DecisionResponse,
    EditorGraphApprovalRequest,
    GraphApprovalRequest,
    InternalBotRun,
    User,
)
from ..models.GraphApprovalRequest import GraphApprovalStatus
from ..models.InternalBotRun import InternalBotRunStatus
from ..models.ProjectRole import ProjectRoleAction
from .DecisionFlow import (
    _NATIVE_AUTHORITY_TABLES,
    DecisionConflict,
    DecisionDenied,
    DecisionFlow,
    DecisionMissing,
    _aware,
    _hash,
    _uid,
    card_target_revision,
)


class DecisionHITL:
    @staticmethod
    def bindable(db, project, card, approval_uid):
        approval = db.exec(
            SqlBuilder.select.table(GraphApprovalRequest).where(GraphApprovalRequest.id == _uid(approval_uid))
        ).first()
        if (
            approval is None
            or approval.decision_case_id is not None
            or approval.execution_plan is None
            or approval.status != GraphApprovalStatus.Pending
            or approval.expires_at is not None
            and _aware(approval.expires_at) <= datetime.now(timezone.utc)
        ):
            raise DecisionConflict("Native approval is unavailable or already bound")
        run = db.exec(
            SqlBuilder.select.table(InternalBotRun)
            .where(InternalBotRun.id == _uid(approval.execution_plan["native_run_uid"]))
            .with_for_update()
        ).first()
        # Match resume writers: authority tables, run, then approval. The first
        # read only resolves the run; reread the binding under its row lock.
        approval = db.exec(
            SqlBuilder.select.table(GraphApprovalRequest)
            .where(GraphApprovalRequest.id == _uid(approval_uid))
            .with_for_update()
        ).first()
        if (
            approval is None
            or approval.decision_case_id is not None
            or approval.status != GraphApprovalStatus.Pending
            or approval.execution_plan is None
            or run is None
            or _uid(approval.execution_plan["native_run_uid"]) != run.id
            or approval.expires_at is not None
            and _aware(approval.expires_at) <= datetime.now(timezone.utc)
            or run.status != InternalBotRunStatus.AwaitingApproval
            or run.project_id != project.id
            or run.scope_table != "card"
            or run.scope_uid != card.get_uid()
            or approval.requested_by_user_id != run.user_id
        ):
            raise DecisionDenied("Approval must target this canonical card and original requester")
        detail_cls = ChatGraphApprovalRequest if approval.request_type.value == "chat" else EditorGraphApprovalRequest
        detail = db.exec(
            SqlBuilder.select.table(detail_cls).where(detail_cls.approval_request_id == approval.id)
        ).first()
        if detail is None or detail.scope_table != "card" or detail.scope_id != card.id:
            raise DecisionDenied("Native approval detail does not match the card")
        if detail_cls is ChatGraphApprovalRequest:
            from ..models import ChatHistory

            source = db.exec(
                SqlBuilder.select.table(ChatHistory).where(ChatHistory.id == run.ai_chat_history_id)
            ).first()
            interrupt = source.message.graph_interrupt if source else None
            if detail.chat_history_id != run.ai_chat_history_id or detail.chat_session_id != run.chat_session_id:
                raise DecisionDenied("Native chat identity changed")
        else:
            interrupt = run.request_payload.get("graph_interrupt")
            if detail.document_name != run.request_payload.get("document_name"):
                raise DecisionDenied("Native editor document changed")
        if not isinstance(interrupt, dict) or not approval.matches_execution_plan(run, interrupt):
            raise DecisionConflict("Native immutable execution plan changed")
        return approval

    @staticmethod
    def rebind_edit(db, original, successor):
        """Preserve policy and participants, but require new votes on the fresh plan.

        The caller holds the native authority and run/approval locks and has just
        revalidated the original policy. No old response is copied or retargeted.
        """
        if original.decision_case_id is None:
            return
        case = db.exec(
            SqlBuilder.select.table(DecisionCase).where(DecisionCase.id == original.decision_case_id).with_for_update()
        ).first()
        if case is None:
            raise DecisionConflict("Original decision round disappeared")
        snapshot = deepcopy(case.snapshot)
        snapshot["native_binding"] = {"approval_uid": successor.get_uid(), "execution_plan": successor.execution_plan}
        request_id = str(uuid4())
        fresh = DecisionCase(
            project_id=case.project_id,
            card_id=case.card_id,
            creator_id=case.creator_id,
            request_id=request_id,
            request_hash=_hash({"request_id": request_id, "snapshot": snapshot}),
            round_id=str(uuid4()),
            source_kind=case.source_kind,
            question=case.question,
            target_revision=case.target_revision,
            policy_revision=case.policy_revision,
            snapshot=snapshot,
            actor_snapshot=deepcopy(case.actor_snapshot),
            deadline=case.deadline,
        )
        db.insert(fresh)
        successor.decision_case_id = fresh.id

    @staticmethod
    def validate(approval, run, *, completing=False, claimant_id=None, decision=None, recording_result=False):
        """Called only by the shared immutable-plan predicate, never from supplied proof."""
        from .DomainService import DomainService

        service = DomainService()
        try:
            with DbSession.use(readonly=False) as db:
                if DbEngine.get_main_engine().dialect.name == "postgresql":
                    db.exec(text("LOCK TABLE " + _NATIVE_AUTHORITY_TABLES + " IN SHARE ROW EXCLUSIVE MODE"))
                case = db.exec(
                    SqlBuilder.select.table(DecisionCase)
                    .where(DecisionCase.id == approval.decision_case_id)
                    .with_for_update()
                ).first()
                if completing:
                    claimant_id = run.user_id
                    decision = run.request_payload.get("resume")
                editing = isinstance(decision, dict) and "edit" in decision
                if (
                    case is None
                    or case.source_kind != "graph_approval"
                    or case.state not in (("pending", "needs_info", "passed") if editing else ("passed",))
                ):
                    return False
                if (
                    approval.execution_plan is None
                    or approval.requested_by_user_id != run.user_id
                    or approval.status != GraphApprovalStatus.Pending
                    or approval.expires_at is not None
                    and _aware(approval.expires_at) <= SafeDateTime.now()
                    or completing
                    and (run.lease_expires_at is None or _aware(run.lease_expires_at) <= SafeDateTime.now())
                ):
                    return False
                if completing:
                    claimant_id = run.user_id
                    decision = run.request_payload.get("resume")
                if claimant_id != run.user_id or (
                    not approval.validates_edit(decision)
                    if editing
                    else decision != {"approved": True, "rejected": False}
                ):
                    return False
                actor = db.exec(SqlBuilder.select.table(User).where(User.id == run.user_id)).first()
                if actor is None or actor.deleted_at is not None or actor.activated_at is None:
                    return False
                flow = DecisionFlow(service)
                project, card = flow._authorized(
                    db,
                    actor,
                    run.project_id.to_short_code(),
                    case.card_id.to_short_code(),
                    CollaborationChannel.Api,
                    manage=True,
                )
                actions = service.project.get_user_role_actions_by_project(actor, project)
                required = {
                    "read": ProjectRoleAction.Read.value,
                    "create": ProjectRoleAction.CardWrite.value,
                    "edit": ProjectRoleAction.CardUpdate.value,
                    "delete": ProjectRoleAction.CardDelete.value,
                }.get(approval.permission)
                if required is None or (
                    "*" not in actions and not {ProjectRoleAction.Update.value, required}.issubset(actions)
                ):
                    return False
                if (
                    case.project_id != run.project_id
                    or run.scope_table != "card"
                    or run.scope_uid != card.get_uid()
                    # Dispatch requires a fresh target. Completion records an already
                    # executed effect, while retaining all current authority and
                    # immutable claim/plan fences below.
                    or not recording_result
                    and card_target_revision(card) != case.target_revision
                    or case.policy_revision
                    != _hash({"policy": case.snapshot["policy"], "participant_uids": case.snapshot["participant_uids"]})
                    or case.snapshot.get("native_binding")
                    != {"approval_uid": approval.get_uid(), "execution_plan": approval.execution_plan}
                    or case.deadline is not None
                    and _aware(case.deadline) <= SafeDateTime.now()
                ):
                    return False
                eligible = flow._eligible(db, case, card, CollaborationChannel.Api)
                # All frozen participants retain authority, including uncast threshold voters.
                if eligible != frozenset(case.snapshot["participant_uids"]):
                    return False
                result = flow._evaluation(
                    db, case, card, CollaborationChannel.Api, datetime.now(timezone.utc), dispatch=True
                )
                if result.state not in (("pending", "needs_info", "passed") if editing else ("passed",)):
                    return False
                last = db.exec(
                    SqlBuilder.select.table(DecisionResponse)
                    .where(DecisionResponse.decision_id == case.id)
                    .order_by(DecisionResponse.response_order.desc())
                ).first()
                receipt = {
                    "decision_uid": case.get_uid(),
                    "round_id": case.round_id,
                    "policy_revision": case.policy_revision,
                    "target_revision": case.target_revision,
                    "snapshot_hash": _hash(
                        {
                            "snapshot": case.snapshot,
                            "actor_snapshot": case.actor_snapshot,
                            "deadline": _aware(case.deadline).isoformat() if case.deadline else None,
                        }
                    ),
                    "requester_uid": actor.get_uid(),
                    "creator_uid": case.creator_id.to_short_code(),
                    "final_responder_uid": last.actor_id.to_short_code() if last else None,
                }
                if completing and run.request_payload.get("resume_decision") != receipt:
                    return False
                object.__setattr__(approval, "_decision_receipt", receipt)
                return True
        except (DecisionConflict, DecisionDenied, DecisionMissing, ValueError, KeyError, TypeError):
            return False
        finally:
            service.close()
