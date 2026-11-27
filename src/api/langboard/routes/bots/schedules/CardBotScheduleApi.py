from fastapi import Depends
from langboard_shared.ai import BotScheduleHelper
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import BotSchedule, Card, CardBotSchedule, ProjectRole
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder
from ..forms import BotSchedulePagination


@AppRouter.schema(query=BotSchedulePagination, permission=ApiPermission.Read)
@AppRouter.api.get(
    "/bot/{bot_uid}/card/{card_uid}/schedules",
    tags=["Bot.Schedule"],
    description="Get all bot cron schedules for a specific card.",
    responses=(
        OpenApiSchema()
        .suc({"schedules": [(BotSchedule, CardBotSchedule)], "target": Card})
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2014)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add()
def get_bot_schedules_by_card(
    bot_uid: str,
    card_uid: str,
    pagination: BotSchedulePagination = Depends(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    bot = service.bot.get_by_id_like(bot_uid)
    if not bot:
        raise ApiException.NotFound_404(ApiErrorCode.NF2014)

    card = service.card.get_by_id_like(card_uid)
    if not card:
        raise ApiException.NotFound_404(ApiErrorCode.NF2014)

    schedules = BotScheduleHelper.get_all_by_scope(
        CardBotSchedule, bot, card, as_api=True, pagination=pagination, status=pagination.status
    )

    return JsonResponse(content={"schedules": schedules, "target": card.api_response()})
