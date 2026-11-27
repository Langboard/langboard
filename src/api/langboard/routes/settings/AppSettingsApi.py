from fastapi import status
from langboard_shared.ai import BotScheduleHelper
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import (
    ApiErrorCode,
    ApiException,
    AppRouter,
    EEditorCollaborationType,
    JsonResponse,
    collaborative_block,
    collaborative_edit,
    create_editor_collaboration_document_id,
)
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import NotificationScheduleRule, SettingRole, User
from langboard_shared.domain.models.bases.BaseRoleModel import ALL_GRANTED
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from .Form import (
    CreateNotificationScheduleRuleForm,
    DeleteSelectedNotificationScheduleRulesForm,
    UpdateNotificationScheduleRuleForm,
)


@AppRouter.api.post("/settings/roles", tags=["AppSettings"], responses=OpenApiSchema().auth().forbidden().get())
@AuthFilter.add("user")
def get_setting_roles(user: User = Auth.scope("user"), service: DomainService = DomainService.scope()) -> JsonResponse:
    setting_role = service.user.get_setting_role(user)
    api_key_role = service.api_key.get_role(user)
    mcp_role = service.mcp_tool_group.get_role(user)
    api_key_role_actions = api_key_role.actions if api_key_role else None
    mcp_role_actions = mcp_role.actions if mcp_role else None

    if user.is_admin:
        api_key_role_actions = [ALL_GRANTED]
        mcp_role_actions = [ALL_GRANTED]

    return JsonResponse(
        content={
            "setting_role_actions": setting_role.actions if setting_role else None,
            "api_key_role_actions": api_key_role_actions,
            "mcp_role_actions": mcp_role_actions,
        }
    )


@AppRouter.api.get(
    "/settings/notification-schedule",
    tags=["AppSettings.NotificationSchedule"],
    responses=(
        OpenApiSchema()
        .suc({"notification_rules": [NotificationScheduleRule], "notification_rule_schema": "object"})
        .auth()
        .forbidden()
        .get()
    ),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.NotificationScheduleRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_notification_schedule_rules(service: DomainService = DomainService.scope()) -> JsonResponse:
    notification_rules = service.app_setting.get_api_notification_schedule_rules()
    notification_rule_schema = service.app_setting.get_notification_schedule_rule_schema()
    return JsonResponse(
        content={
            "notification_rules": notification_rules,
            "notification_rule_schema": notification_rule_schema,
        }
    )


@AppRouter.api.post(
    "/settings/notification-schedule/rule",
    tags=["AppSettings.NotificationSchedule"],
    responses=OpenApiSchema(201).suc({"notification_rule": NotificationScheduleRule}).auth().forbidden().get(),
)
@RoleFilter.add(
    SettingRole, [SettingRoleAction.NotificationScheduleCreate], RoleFinder.setting, allowed_all_admin=False
)
@AuthFilter.add("admin")
def create_notification_schedule_rule(
    form: CreateNotificationScheduleRuleForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    form.interval_str = BotScheduleHelper.utils.convert_valid_interval_str(form.interval_str)
    if not form.interval_str:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)

    rule = service.app_setting.create_notification_schedule_rule(form.model_dump())

    return JsonResponse(content={"notification_rule": rule.api_response()}, status_code=status.HTTP_201_CREATED)


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{rule_uid}", "notification-schedule-rule"
        )
    )
)
@AppRouter.api.put(
    "/settings/notification-schedule/rule/{rule_uid}",
    tags=["AppSettings.NotificationSchedule"],
    responses=OpenApiSchema().suc({"notification_rule": NotificationScheduleRule}).auth().forbidden().get(),
)
@RoleFilter.add(
    SettingRole, [SettingRoleAction.NotificationScheduleUpdate], RoleFinder.setting, allowed_all_admin=False
)
@AuthFilter.add("admin")
def update_notification_schedule_rule(
    rule_uid: str, form: UpdateNotificationScheduleRuleForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    if form.interval_str:
        form.interval_str = BotScheduleHelper.utils.convert_valid_interval_str(form.interval_str)
        if not form.interval_str:
            raise ApiException.BadRequest_400(ApiErrorCode.VA0000)

    result = service.app_setting.update_notification_schedule_rule(rule_uid, form.model_dump(exclude_unset=True))
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF3002)

    rule = service.app_setting.get_notification_schedule_rule(rule_uid)
    if not rule:
        raise ApiException.NotFound_404(ApiErrorCode.NF3002)

    return JsonResponse(content={"notification_rule": rule.api_response()})


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{rule_uid}", "notification-schedule-rule"
        )
    )
)
@AppRouter.api.delete(
    "/settings/notification-schedule/rule/{rule_uid}",
    tags=["AppSettings.NotificationSchedule"],
    responses=OpenApiSchema().auth().forbidden().get(),
)
@RoleFilter.add(
    SettingRole, [SettingRoleAction.NotificationScheduleDelete], RoleFinder.setting, allowed_all_admin=False
)
@AuthFilter.add("admin")
def delete_notification_schedule_rule(rule_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    result = service.app_setting.delete_notification_schedule_rule(rule_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3002)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{rule_uids}", "notification-schedule-rule"
        )
    )
)
@AppRouter.api.delete(
    "/settings/notification-schedule/rules",
    tags=["AppSettings.NotificationSchedule"],
    responses=OpenApiSchema().auth().forbidden().get(),
)
@RoleFilter.add(
    SettingRole, [SettingRoleAction.NotificationScheduleDelete], RoleFinder.setting, allowed_all_admin=False
)
@AuthFilter.add("admin")
def delete_selected_notification_schedule_rules(
    form: DeleteSelectedNotificationScheduleRulesForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.app_setting.delete_selected_notification_schedule_rules(form.rule_uids)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3002)

    return JsonResponse()
