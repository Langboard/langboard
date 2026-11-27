from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import Bot, InternalBot, User
from langboard_shared.domain.services import DomainService
from langboard_shared.security import Auth


@AppRouter.api.get("/health", tags=["Global"], responses=OpenApiSchema().get())
def health_check() -> JsonResponse:
    return JsonResponse(content={"status": "ok"})


@AppRouter.api.get(
    "/global/internal-bot/{bot_uid}",
    tags=["Global"],
    responses=(
        OpenApiSchema().suc({"internal_bot": InternalBot}).auth().forbidden().err(404, ApiErrorCode.NF3004).get()
    ),
)
@AuthFilter.add("user")
def get_internal_bot(
    bot_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    internal_bot = service.internal_bot.get_by_id_like(bot_uid)
    if not internal_bot:
        raise ApiException.NotFound_404(ApiErrorCode.NF3004)

    return JsonResponse(content={"internal_bot": internal_bot.api_response(is_setting=user.is_admin)})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/global/bots",
    tags=["Global"],
    responses=OpenApiSchema().suc({"bots": [Bot]}).auth().forbidden().get(),
)
@AuthFilter.add()
def get_bots(service: DomainService = DomainService.scope()) -> JsonResponse:
    bots = service.bot.get_api_list(False)

    return JsonResponse(content={"bots": bots})
