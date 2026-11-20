from fastapi import HTTPException, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from .NotificationSettingForm import CardActivityNotificationForm, NotificationSettingForm, NotificationSettingTypeForm


@AppRouter.api.put(
    "/notification/settings/all",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_notification_subscription(
    form: NotificationSettingForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_all(user, form.channel, form.is_unsubscribed)
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/type",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_type_notification_subscription(
    form: NotificationSettingTypeForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    if form.notification_type not in NotificationType and form.notification_type not in NotificationType._member_names_:
        return JsonResponse(content={"notification_types": []})

    notification_types = service.user_notification_setting.toggle_type(
        user, form.channel, form.notification_type, form.is_unsubscribed
    )
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/project",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_project_notification_subscription(
    form: NotificationSettingForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_project(user, form.channel, form.is_unsubscribed)
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/column",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_column_notification_subscription(
    form: NotificationSettingForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_column(user, form.channel, form.is_unsubscribed)
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/card",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_card_notification_subscription(
    form: NotificationSettingForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_card(user, form.channel, form.is_unsubscribed)
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/wiki",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_wiki_notification_subscription(
    form: NotificationSettingForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_wiki(user, form.channel, form.is_unsubscribed)
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/project/{project_uid}",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def toggle_project_notification_subscription(
    project_uid: str,
    form: NotificationSettingForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_project(
        user, form.channel, form.is_unsubscribed, project_uid
    )
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/project/{project_uid}/column/{column_uid}",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def toggle_column_notification_subscription(
    project_uid: str,
    column_uid: str,
    form: NotificationSettingForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_column(
        user, form.channel, form.is_unsubscribed, project_uid, column_uid
    )
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/project/{project_uid}/card/{card_uid}",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def toggle_card_notification_subscription(
    project_uid: str,
    card_uid: str,
    form: NotificationSettingForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_card(
        user, form.channel, form.is_unsubscribed, project_uid, card_uid
    )
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.put(
    "/notification/settings/project/{project_uid}/wiki/{wiki_uid}",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notification_types": [NotificationType]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def toggle_wiki_notification_subscription(
    project_uid: str,
    wiki_uid: str,
    form: NotificationSettingForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    notification_types = service.user_notification_setting.toggle_wiki(
        user, form.channel, form.is_unsubscribed, project_uid, wiki_uid
    )
    return JsonResponse(
        content={"notification_types": [notification_type.value for notification_type in notification_types]}
    )


@AppRouter.api.get("/notification/settings/project/{project_uid}/card/{card_uid}/activity", tags=["Notification"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_card_activity_notification_mode(
    request: Request,
    project_uid: str,
    card_uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        mode = service.user_notification_setting.card_activity_mode(
            user, project_uid, card_uid, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api)
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Card unavailable") from None
    return JsonResponse(content={"mode": mode})


@AppRouter.api.put("/notification/settings/project/{project_uid}/card/{card_uid}/activity", tags=["Notification"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def set_card_activity_notification_mode(
    request: Request,
    project_uid: str,
    card_uid: str,
    form: CardActivityNotificationForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        mode = service.user_notification_setting.card_activity_mode(
            user,
            project_uid,
            card_uid,
            form.mode,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Card unavailable") from None
    return JsonResponse(content={"mode": mode})
