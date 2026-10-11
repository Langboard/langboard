"""Human stop requests use native card authority and retain runtime evidence."""

from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ..models import AppExecutionLease, AppExecutionRequest, Card, Project, ProjectAssignedUser, ProjectRole, User
from .AppExecutionLeases import _result
from .AppExecutionRequests import AppExecutionRequestConflict
from .AppGovernance import AppGovernanceDenied, _current


def request_human_runtime_stop(service, actor, project_id, card_id, request_id, generation, channel=None, *, app_key=None):
    """Revoke this permit; only the external runtime can attest actual termination.

    App revocation must not prevent a still-authorized human from stopping a
    retained runtime. A stop never creates a lease or grants execution again.
    """
    if type(generation) is not int or generation < 1:
        raise ValueError("A positive execution generation is required")
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        current = _human_control_scope(db, service, actor, project_id, card_id, channel)
        request = db.exec(SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.id == request_id,
            AppExecutionRequest.project_id == project_id,
            AppExecutionRequest.card_id == card_id,
        )).first()
        if request is None or (app_key is not None and request.app_key != app_key):
            raise AppGovernanceDenied()
        if request.generation != generation:
            raise AppExecutionRequestConflict()
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease).where(
            AppExecutionLease.request_id == request.id
        ).with_for_update()).first()
        if lease is None:
            raise AppExecutionRequestConflict()
        changed = lease.state == "authorized"
        if changed:
            lease.state = "stop_requested"
            lease.history = [*lease.history, {
                "state": "stop_requested", "at": SafeDateTime.now().isoformat(),
                "actor_uid": current.get_uid(), "reason": "human_request",
            }]
            db.update(lease)
        return {**_result(db, lease), "changed": changed, "execution_resumed": False}


def list_human_card_runtimes(service, actor, project_id, card_id, channel=None, *, before=None, app_key=None):
    """Bounded retained runtime identities; no private credentials or inferred runs."""
    from .AppExecutionLeases import runtime_timestamp

    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)).first()
        readable = service.card.resolve_readable_card(project_id, card_id, current, channel or CollaborationChannel.Api) if current else None
        if current is None or current.deleted_at or not current.activated_at or readable is None:
            raise AppGovernanceDenied()
        if readable[0].owner_id != current.id:
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project_id, ProjectRole.user_id == current.id
            )).first()
            if role is None or not role.is_granted("read"):
                raise AppGovernanceDenied()
        query = SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.project_id == project_id, AppExecutionRequest.card_id == card_id
        )
        if app_key is not None:
            query = query.where(AppExecutionRequest.app_key == app_key)
        if before is not None:
            query = query.where(AppExecutionRequest.id < before)
        page = db.exec(query.order_by(AppExecutionRequest.id.desc()).limit(11)).all()
        requests = page[:10]
        next_cursor = requests[-1].get_uid() if len(page) > 10 else None
        if not requests:
            return {"items": [], "limit": 10, "next_cursor": None}
        leases = db.exec(SqlBuilder.select.table(AppExecutionLease).where(
            AppExecutionLease.request_id.in_([request.id for request in requests])
        )).all()
        by_request = {lease.request_id: lease for lease in leases}
        from .ExecutionGeneration import execution_generations
        current_generation = execution_generations([card_id]).get(card_id, 0)
        resumes = {lease.request_id: next((entry.get("request_uid") for entry in lease.history
                   if entry.get("action") == "resume_requested"), None) for lease in leases}
        return {"items": [{
            "request_uid": request.get_uid(), "app_key": request.app_key,
            "generation": request.generation,
            "state": by_request[request.id].state if request.id in by_request else "awaiting_permit",
            "expires_at": runtime_timestamp(by_request[request.id].expires_at) if request.id in by_request else None,
            "evidence_kind": "runtime_authorization",
            "current_generation": current_generation,
            "resume_request_uid": resumes.get(request.id),
        } for request in requests], "limit": 10, "next_cursor": next_cursor}


def _human_control_scope(db, service, actor, project_id, card_id, channel):
    project = _current(db, Project, project_id)
    card = _current(db, Card, card_id)
    current = _current(db, User, actor.id)
    if (
        project is None or project.deleted_at
        or card is None or card.deleted_at or card.project_id != project_id
        or current is None or current.deleted_at or not current.activated_at
        or service.card.resolve_readable_card(project_id, card_id, current, channel or CollaborationChannel.Api) is None
    ):
        raise AppGovernanceDenied()
    if project.owner_id != current.id:
        member = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(
            ProjectAssignedUser.project_id == project_id, ProjectAssignedUser.user_id == current.id
        )).first()
        role = db.exec(SqlBuilder.select.table(ProjectRole).where(
            ProjectRole.project_id == project_id, ProjectRole.user_id == current.id
        )).first()
        if member is None or role is None or not role.is_granted("card_update"):
            raise AppGovernanceDenied()
    return current


def request_human_runtime_resume(service, actor, project_id, card_id, request_id, generation, channel=None, *, app_key=None, new_run=False, expected_generation=None, expected_revision=None):
    """Explicit human choice; retain old leases and reuse the readiness producer."""
    from .AppExecutionGrant import evaluate_connection_execution_grant
    from .AppExecutionRequests import accept_authorized_execution_request
    from .ExecutionGeneration import execution_generations

    if type(generation) is not int or generation < 1:
        raise ValueError("A positive execution generation is required")
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        producer = None
        if new_run:
            from ...tasks.webhooks.ExecutionReadinessUow import ExecutionReadinessUow
            producer = ExecutionReadinessUow(db)
            # Same producer lock before native card/lease locks.
            producer.watch([card_id])
        current = _human_control_scope(db, service, actor, project_id, card_id, channel)
        request = db.exec(SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.id == request_id, AppExecutionRequest.project_id == project_id,
            AppExecutionRequest.card_id == card_id,
        )).first()
        if request is None or (app_key is not None and request.app_key != app_key):
            raise AppGovernanceDenied()
        if request.generation != generation:
            raise AppExecutionRequestConflict()
        # Same card lock order as stop and runtime checks. Old lease is never authorized.
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease).where(
            AppExecutionLease.request_id == request.id
        ).with_for_update()).first()
        latest = execution_generations([card_id]).get(card_id, 0)
        if lease is None or lease.state != "stopped":
            raise AppExecutionRequestConflict()
        action = "new_run_requested" if new_run else "resume_requested"
        prior = next((entry for entry in lease.history if entry.get("action") in ("resume_requested", "new_run_requested")), None)
        if prior is not None and (prior.get("action") != action or prior.get("generation") != latest):
            raise AppExecutionRequestConflict()
        if new_run:
            if type(expected_generation) is not int or expected_generation < 1 or expected_revision is None:
                raise ValueError("Observed generation and card revision required")
            if prior is not None:
                if prior.get("source_generation") != expected_generation or prior.get("source_revision") != expected_revision.isoformat():
                    raise AppExecutionRequestConflict()
            else:
                if latest != generation or expected_generation != latest:
                    raise AppExecutionRequestConflict()
                try:
                    producer.request_new_run(card_id, expected_generation, expected_revision)
                except ValueError:
                    raise AppExecutionRequestConflict() from None
                producer.finish()
                latest = execution_generations([card_id]).get(card_id, 0)
        elif latest <= generation:
            raise AppExecutionRequestConflict()
        existing = db.exec(SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.card_id == card_id, AppExecutionRequest.generation == latest
        )).first()
        if (existing is not None and prior is None) or (prior is not None and (
            existing is None or existing.get_uid() != prior.get("request_uid")
        )):
            raise AppExecutionRequestConflict()
        authority = evaluate_connection_execution_grant(request.connection_id, project_id, card_id, latest)
        if authority["app_key"] != request.app_key:
            raise AppGovernanceDenied()
        result = accept_authorized_execution_request(db, authority, project_id, card_id, latest)
        if result["changed"]:
            lease.history = [*lease.history, {"state": "stopped", "action": action,
                "at": SafeDateTime.now().isoformat(), "actor_uid": current.get_uid(),
                "request_uid": result["request_uid"], "generation": latest,
                **({"source_generation": expected_generation, "source_revision": expected_revision.isoformat()} if new_run else {})}]
            db.update(lease)
        return {**result, "state": action, "execution_resumed": False}
