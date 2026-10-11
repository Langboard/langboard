"""Human stop requests use native card authority and retain runtime evidence."""

from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ..models import AppExecutionLease, AppExecutionRequest, Card, Project, ProjectAssignedUser, ProjectRole, User
from .AppExecutionLeases import _result
from .AppExecutionRequests import AppExecutionRequestConflict
from .AppGovernance import AppGovernanceDenied, _current


def request_human_runtime_stop(service, actor, project_id, card_id, request_id, generation, channel=None):
    """Revoke this permit; only the external runtime can attest actual termination.

    App revocation must not prevent a still-authorized human from stopping a
    retained runtime. A stop never creates a lease or grants execution again.
    """
    if type(generation) is not int or generation < 1:
        raise ValueError("A positive execution generation is required")
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
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
        request = db.exec(SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.id == request_id,
            AppExecutionRequest.project_id == project_id,
            AppExecutionRequest.card_id == card_id,
        )).first()
        if request is None:
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
