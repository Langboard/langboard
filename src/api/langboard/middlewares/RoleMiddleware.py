from fastapi import status
from langboard_shared.core.filter import FilterMiddleware
from langboard_shared.core.routing import ApiErrorCode, JsonResponse
from langboard_shared.domain.models import Bot, User
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleSecurity
from starlette.concurrency import run_in_threadpool
from starlette.routing import BaseRoute
from starlette.types import ASGIApp


class RoleMiddleware(FilterMiddleware):
    """Checks if the user is authenticated and has the correct permissions."""

    __auto_load__ = False

    def __init__(
        self,
        app: ASGIApp,
        routes: list[BaseRoute],
    ) -> None:
        FilterMiddleware.__init__(self, app, routes, RoleFilter)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        should_filter, child_scope = self.should_filter(scope)

        if should_filter:
            if "auth" not in scope:
                response = JsonResponse(status_code=status.HTTP_401_UNAUTHORIZED)
                await response(scope, receive, send)
                return

            user_or_bot: User | Bot = scope["auth"]
            if not user_or_bot.id:
                response = JsonResponse(status_code=status.HTTP_401_UNAUTHORIZED)
                await response(scope, receive, send)
                return

            if isinstance(user_or_bot, User):
                role_model, actions, role_finder, allowed_all_admin = RoleFilter.get_filtered(child_scope["endpoint"])
                if RoleSecurity.allows_admin_override(user_or_bot, allowed_all_admin=allowed_all_admin):
                    await self.app(scope, receive, send)
                    return

                role = RoleSecurity(role_model)

                is_authorized = await run_in_threadpool(
                    role.is_authorized,
                    user_or_bot.id,
                    child_scope["path_params"],
                    actions,
                    role_finder,
                )
                if not is_authorized:
                    response = JsonResponse(content=ApiErrorCode.PE1001, status_code=status.HTTP_403_FORBIDDEN)
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)
