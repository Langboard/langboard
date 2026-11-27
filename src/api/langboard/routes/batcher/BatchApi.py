from fastapi import Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiPermission, AppRouter, JsonResponse
from langboard_shared.domain.models import Bot, User
from langboard_shared.helpers.AgentApiPermissionHelper import get_agent_allowed_permissions
from langboard_shared.security import Auth
from starlette.datastructures import Headers
from .BatchForm import BatchForm
from .BatchRunner import execute_batch_request_schemas


@AppRouter.schema(form=BatchForm, permission=ApiPermission.Read)
@AppRouter.api.post(
    "/batch",
    tags=["Batcher"],
    description="Batch API for processing multiple requests in a single call. The response will be a list of responses corresponding to each request schema provided in the form.",
)
@AuthFilter.add()
async def batch_apis(request: Request, form: BatchForm, user_or_bot: User | Bot = Auth.scope("all")):
    allowed_permissions = get_agent_allowed_permissions(Headers(raw=request.headers.raw), default_read=True)
    responses = await execute_batch_request_schemas(request, form.request_schemas, user_or_bot, allowed_permissions)
    return JsonResponse(content=responses)
