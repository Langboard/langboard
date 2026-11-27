from fastapi import Depends
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import BotLog, Project, ProjectBotLog, ProjectRole
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder
from ..forms import BotLogPagination


@AppRouter.schema(query=BotLogPagination, permission=ApiPermission.Read)
@AppRouter.api.get(
    "/bot/{bot_uid}/project/{project_uid}/logs",
    tags=["Bot.Log"],
    description="Get all bot logs for a specific project.",
    responses=(
        OpenApiSchema()
        .suc({"logs": [(BotLog, ProjectBotLog)], "target": Project})
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2014)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add()
def get_bot_logs_by_project(
    bot_uid: str,
    project_uid: str,
    pagination: BotLogPagination = Depends(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    bot = service.bot.get_by_id_like(bot_uid)
    if not bot:
        raise ApiException.NotFound_404(ApiErrorCode.NF2014)

    project = service.project.get_by_id_like(project_uid)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2014)

    logs = service.bot_log.get_api_list_by_scope(ProjectBotLog, bot, project, pagination)

    return JsonResponse(content={"logs": logs, "target": project.api_response()})
