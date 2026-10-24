"""Lazy native automation summaries and explicit authorized execution details."""

from typing import Annotated
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.BoardAutomationRuns import read_runs
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder


def _read(request, project_uid, user, service, **kwargs):
    try:
        result = read_runs(
            service.workflow_stage,
            user,
            project_uid,
            request.scope.get("collaboration_channel", CollaborationChannel.Api),
            **kwargs,
        )
    except (ValueError, TypeError) as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/automation/runs", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_board_automation_runs(
    request: Request,
    project_uid: str,
    before: Annotated[
        str | None, Query(max_length=32, pattern=r"^(chat|schedule|workflow|log|mcp):[a-zA-Z0-9]{11}$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return _read(request, project_uid, user, service, before=before, limit=limit)


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/automation/runs/{source}/{uid}", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_board_automation_run(
    request: Request,
    project_uid: str,
    source: str,
    uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return _read(request, project_uid, user, service, source=source, uid=uid)
