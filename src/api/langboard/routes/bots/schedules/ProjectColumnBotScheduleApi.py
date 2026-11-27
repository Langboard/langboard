from fastapi import Depends
from langboard_shared.ai import BotScheduleHelper
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import BotSchedule, ProjectColumn, ProjectColumnBotSchedule, ProjectRole
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder
from ..forms import BotSchedulePagination


@AppRouter.schema(query=BotSchedulePagination, permission=ApiPermission.Read)
@AppRouter.api.get(
    "/bot/{bot_uid}/column/{column_uid}/schedules",
    tags=["Bot.Schedule"],
    description="Get all bot cron schedules for a specific column.",
    responses=(
        OpenApiSchema()
        .suc({"schedules": [(BotSchedule, ProjectColumnBotSchedule)], "target": ProjectColumn})
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2013)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add()
def get_bot_schedules_by_column(
    bot_uid: str,
    column_uid: str,
    pagination: BotSchedulePagination = Depends(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    bot = service.bot.get_by_id_like(bot_uid)
    if not bot:
        raise ApiException.NotFound_404(ApiErrorCode.NF2013)

    column = service.project_column.get_by_id_like(column_uid)
    if not column:
        raise ApiException.NotFound_404(ApiErrorCode.NF2013)

    schedules = BotScheduleHelper.get_all_by_scope(
        ProjectColumnBotSchedule, bot, column, as_api=True, pagination=pagination, status=pagination.status
    )

    return JsonResponse(content={"schedules": schedules, "target": column.api_response()})
