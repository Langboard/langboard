from fastapi import Depends
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import BotLog, ProjectColumn, ProjectColumnBotLog, ProjectRole
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder
from ..forms import BotLogPagination


@AppRouter.schema(query=BotLogPagination, permission=ApiPermission.Read)
@AppRouter.api.get(
    "/bot/{bot_uid}/column/{column_uid}/logs",
    tags=["Bot.Log"],
    description="Get all bot logs for a specific column.",
    responses=(
        OpenApiSchema()
        .suc({"logs": [(BotLog, ProjectColumnBotLog)], "target": ProjectColumn})
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2014)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add()
def get_bot_logs_by_column(
    bot_uid: str,
    column_uid: str,
    pagination: BotLogPagination = Depends(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    bot = service.bot.get_by_id_like(bot_uid)
    if not bot:
        raise ApiException.NotFound_404(ApiErrorCode.NF2014)

    column = service.project_column.get_by_id_like(column_uid)
    if not column:
        raise ApiException.NotFound_404(ApiErrorCode.NF2014)

    logs = service.bot_log.get_api_list_by_scope(ProjectColumnBotLog, bot, column, pagination)

    return JsonResponse(content={"logs": logs, "target": column.api_response()})
