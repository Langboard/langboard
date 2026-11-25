from fastapi import Request
from langboard_shared.core.db import DbSession
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services.ReminderWebPushPolicy import (
    ReminderWebPushPolicy,
    get_reminder_web_push_policy,
    set_reminder_web_push_policy,
)
from langboard_shared.domain.services.WebPushSubscription import (
    WebPushEndpointConflict,
    WebPushRegistration,
    WebPushSessionDenied,
    browser_push_public_key,
    browser_push_subscribed,
    change_browser_push,
)
from langboard_shared.Env import Env
from langboard_shared.security import Auth


@AppRouter.api.get("/notification/settings/web-push", tags=["Notification"])
@AuthFilter.add("user")
def browser_push_availability(request: Request, user: User = Auth.scope("user")) -> JsonResponse:
    key = browser_push_public_key()
    subscribed = False
    policy = ReminderWebPushPolicy()
    if key is not None:
        try:
            subscribed = browser_push_subscribed(request.cookies.get(Env.REFRESH_TOKEN_NAME, ""), user)
        except WebPushSessionDenied as exc:
            raise ApiException.Unauthorized_401(ApiErrorCode.AU1004) from exc
        with DbSession.use(readonly=False) as db:
            policy = get_reminder_web_push_policy(db, user.id)
    return JsonResponse(
        content={
            "available": key is not None,
            "application_server_key": key,
            "subscribed": subscribed,
            "policy": policy.model_dump(),
        },
        headers={"Cache-Control": "no-store"},
    )


@AppRouter.schema(form=WebPushRegistration, permission=ApiPermission.Edit)
@AppRouter.api.put("/notification/settings/web-push", tags=["Notification"])
@AuthFilter.add("user")
def register_browser_push(request: Request, form: WebPushRegistration, user: User = Auth.scope("user")) -> JsonResponse:
    if browser_push_public_key() is None:
        raise ApiException.NotFound_404()
    try:
        change_browser_push(request.cookies.get(Env.REFRESH_TOKEN_NAME, ""), user=user, registration=form)
    except WebPushSessionDenied as exc:
        raise ApiException.Unauthorized_401(ApiErrorCode.AU1004) from exc
    except WebPushEndpointConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.VA0000) from exc
    return JsonResponse(content={"subscribed": True}, headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.delete("/notification/settings/web-push", tags=["Notification"])
@AuthFilter.add("user")
def remove_browser_push(request: Request, user: User = Auth.scope("user")) -> JsonResponse:
    try:
        change_browser_push(request.cookies.get(Env.REFRESH_TOKEN_NAME, ""), user=user)
    except WebPushSessionDenied as exc:
        raise ApiException.Unauthorized_401(ApiErrorCode.AU1004) from exc
    return JsonResponse(content={"subscribed": False}, headers={"Cache-Control": "no-store"})


@AppRouter.schema(form=ReminderWebPushPolicy, permission=ApiPermission.Edit)
@AppRouter.api.put("/notification/settings/web-push/policy", tags=["Notification"])
@AuthFilter.add("user")
def set_browser_push_policy(
    request: Request,
    form: ReminderWebPushPolicy,
    user: User = Auth.scope("user"),
) -> JsonResponse:
    if browser_push_public_key() is None:
        raise ApiException.NotFound_404()
    try:
        browser_push_subscribed(request.cookies.get(Env.REFRESH_TOKEN_NAME, ""), user)
        set_reminder_web_push_policy(user, form)
    except WebPushSessionDenied as exc:
        raise ApiException.Unauthorized_401(ApiErrorCode.AU1004) from exc
    return JsonResponse(content=form.model_dump(), headers={"Cache-Control": "no-store"})
