from fastapi import Depends
from langboard_shared.ai import BotScheduleHelper
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import BotSchedule, Project, ProjectBotSchedule, ProjectRole
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder
from ..forms import BotSchedulePagination


@AppRouter.schema(query=BotSchedulePagination, permission=ApiPermission.Read)
@AppRouter.api.get(
    "/bot/{bot_uid}/project/{project_uid}/schedules",
    tags=["Bot.Schedule"],
    description="Get all bot cron schedules for a specific project.",
    responses=(
        OpenApiSchema()
        .suc({"schedules": [(BotSchedule, ProjectBotSchedule)], "target": Project})
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2013)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add()
def get_bot_schedules_by_project(
    bot_uid: str,
    project_uid: str,
    pagination: BotSchedulePagination = Depends(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    bot = service.bot.get_by_id_like(bot_uid)
    if not bot:
        raise ApiException.NotFound_404(ApiErrorCode.NF2013)

    project = service.project.get_by_id_like(project_uid)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2013)

    schedules = BotScheduleHelper.get_all_by_scope(
        ProjectBotSchedule, bot, project, as_api=True, pagination=pagination, status=pagination.status
    )

    return JsonResponse(content={"schedules": schedules, "target": project.api_response()})
