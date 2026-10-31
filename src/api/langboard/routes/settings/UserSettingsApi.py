import re
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse
from fastapi import Path, Query, Request, status
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import (
    ApiErrorCode,
    ApiException,
    AppRouter,
    EEditorCollaborationType,
    JsonResponse,
    collaborative_block,
    collaborative_edit,
    collaborative_text,
    create_editor_collaboration_document_id,
)
from langboard_shared.core.schema import OpenApiSchema, PaginatedList
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ApiKeyRole, IdentityProvider, McpRole, SettingRole, User, UserProfile
from langboard_shared.domain.models.ApiKeyRole import ApiKeyRoleAction
from langboard_shared.domain.models.bases.BaseRoleModel import ALL_GRANTED
from langboard_shared.domain.models.McpRole import McpRoleAction
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.UserService import UserOwnershipError
from langboard_shared.Env import Env
from langboard_shared.filter import RoleFilter
from langboard_shared.publishers import AppSettingPublisher
from langboard_shared.security import Auth, RoleFinder
from .Form import (
    CreateUserForm,
    DeleteSelectedUsersForm,
    LinkOidcIdentityForm,
    UpdateApiKeyRoleForm,
    UpdateMcpRoleForm,
    UpdateSettingRoleForm,
    UpdateUserForm,
)


def _is_allowed_oidc_issuer(issuer: str) -> bool:
    """Require TLS except for loopback issuers used by local development."""

    parsed = urlparse(issuer)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return (
        Env.ENVIRONMENT == "development"
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )


@AppRouter.api.put(
    "/settings/users/{user_uid}/identity-links/oidc",
    tags=["AppSettings.User"],
    responses=(
        OpenApiSchema()
        .suc({"identity_link": "object"})
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF1004)
        .err(409, ApiErrorCode.EX1003)
        .get()
    ),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def link_user_oidc_identity(
    user_uid: str,
    form: LinkOidcIdentityForm,
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    """Link one reviewed OIDC issuer and subject to an existing user account."""

    target_user = service.user.get_by_id_like(user_uid)
    if not target_user:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    issuer = form.issuer.strip().rstrip("/")
    subject = form.subject.strip()
    if not subject or not _is_allowed_oidc_issuer(issuer):
        raise ApiException.BadRequest_400()

    current = service.identity_link.get_by_provider_external_id(IdentityProvider.Oidc, subject, issuer)
    if current and current.user_id != target_user.id:
        raise ApiException.Conflict_409(ApiErrorCode.EX1003)

    identity_link = service.identity_link.upsert_user_link(
        user=target_user,
        provider=IdentityProvider.Oidc,
        external_id=subject,
        issuer=issuer,
        email=form.email.strip().lower() if form.email else None,
    )
    return JsonResponse(content={"identity_link": identity_link.api_response()})


@AppRouter.api.get(
    "/settings/users",
    tags=["AppSettings.User"],
    responses=(
        OpenApiSchema()
        .suc(
            PaginatedList.api_schema(
                (
                    (User, UserProfile),
                    {
                        "schema": {
                            "activated_at": "string?",
                            "account_status": "string",
                            "last_signed_in_at": "string?",
                            "latest_business_activity_at": "string?",
                            "is_admin": "bool",
                            "setting_role_actions": [ALL_GRANTED, SettingRoleAction],
                            "api_key_role_actions": [ALL_GRANTED, ApiKeyRoleAction],
                            "mcp_role_actions": [ALL_GRANTED, McpRoleAction],
                        }
                    },
                    {
                        "full_access_emails": [str],
                    },
                )
            )
        )
        .auth()
        .forbidden()
        .get()
    ),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_users_in_settings(service: DomainService = DomainService.scope()) -> JsonResponse:
    if service.user.count_not_deleted() > Env.SETTINGS_USER_LIST_MAX_ITEMS:
        raise ApiException.ContentTooLarge_413()

    users = service.user.get_api_list_in_settings()
    full_access_emails = Env.FULL_ADMIN_ACCESS_EMAILS

    return JsonResponse(
        content={
            **PaginatedList(records=users).model_dump(),
            "full_access_emails": list(full_access_emails),
        }
    )


def _sign_in_bound(value: str | None) -> datetime | None:
    """Explicit ISO 8601 timezone required; normalize to the UTC database clock."""
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from None


@AppRouter.api.get(
    "/settings/users/page",
    tags=["AppSettings.User"],
    responses=OpenApiSchema()
    .suc(
        {
            "records": [dict],
            "total_records": "integer",
            "page": "integer",
            "limit": "integer",
            "full_access_emails": [str],
        }
    )
    .auth()
    .forbidden()
    .get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_users_page_in_settings(
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    query: Annotated[str, Query(max_length=200)] = "",
    account_status: Literal["all", "active", "inactive"] = "all",
    identity_provider: Literal["all", "oidc", "scim"] = "all",
    email: Annotated[str, Query(max_length=200)] = "",
    firstname: Annotated[str, Query(max_length=200)] = "",
    lastname: Annotated[str, Query(max_length=200)] = "",
    exclude_user_uid: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z]{11}$")] = None,
    last_signed_in_from: Annotated[
        str | None,
        Query(max_length=40, description="Latest successful sign-in, inclusive ISO 8601 timezone-aware lower bound"),
    ] = None,
    last_signed_in_before: Annotated[
        str | None,
        Query(max_length=40, description="Latest successful sign-in, exclusive ISO 8601 timezone-aware upper bound"),
    ] = None,
    last_business_activity_from: Annotated[
        str | None,
        Query(max_length=40, description="Latest business mutation, inclusive ISO 8601 timezone-aware lower bound"),
    ] = None,
    last_business_activity_before: Annotated[
        str | None,
        Query(max_length=40, description="Latest business mutation, exclusive ISO 8601 timezone-aware upper bound"),
    ] = None,
    sort_by: Literal["email", "firstname", "lastname", "created_at"] = "created_at",
    sort_direction: Literal["asc", "desc"] = "desc",
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    signed_in_from = _sign_in_bound(last_signed_in_from)
    signed_in_before = _sign_in_bound(last_signed_in_before)
    if signed_in_from is not None and signed_in_before is not None and signed_in_from >= signed_in_before:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    business_from = _sign_in_bound(last_business_activity_from)
    business_before = _sign_in_bound(last_business_activity_before)
    if business_from is not None and business_before is not None and business_from >= business_before:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    return JsonResponse(
        content={
            "records": service.user.get_api_list_in_settings(
                offset=(page - 1) * limit,
                limit=limit,
                query=query,
                account_status=account_status,
                identity_provider=identity_provider,
                column_filters={"email": email, "firstname": firstname, "lastname": lastname},
                exclude_user_uid=exclude_user_uid,
                last_signed_in_from=signed_in_from,
                last_signed_in_before=signed_in_before,
                last_business_activity_from=business_from,
                last_business_activity_before=business_before,
                sort_by=sort_by,
                sort_direction=sort_direction,
            ),
            "total_records": service.user.count_in_settings(
                query=query,
                account_status=account_status,
                identity_provider=identity_provider,
                column_filters={"email": email, "firstname": firstname, "lastname": lastname},
                exclude_user_uid=exclude_user_uid,
                last_signed_in_from=signed_in_from,
                last_signed_in_before=signed_in_before,
                last_business_activity_from=business_from,
                last_business_activity_before=business_before,
            ),
            "page": page,
            "limit": limit,
            "full_access_emails": list(Env.FULL_ADMIN_ACCESS_EMAILS),
        }
    )


@AppRouter.api.get(
    "/settings/users/{user_uid}/business-activity",
    tags=["AppSettings.User"],
    responses=OpenApiSchema()
    .suc({"records": [dict], "page": "integer", "limit": "integer", "has_more": "bool"})
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF1004)
    .get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_user_business_activity_in_settings(
    user_uid: Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")],
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    project_uid: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z]{11}$")] = None,
    action: Annotated[str | None, Query(max_length=100, pattern=r"^[a-z_]+$")] = None,
    occurred_from: Annotated[str | None, Query(max_length=40)] = None,
    occurred_before: Annotated[str | None, Query(max_length=40)] = None,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    start, end = _sign_in_bound(occurred_from), _sign_in_bound(occurred_before)
    if start is not None and end is not None and start >= end:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    target = service.user.get_by_id_like(user_uid)
    if not target:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)
    return JsonResponse(
        content=service.user.get_business_activity_in_settings(
            user,
            target,
            page=page,
            limit=limit,
            project_uid=project_uid,
            action=action,
            occurred_from=start,
            occurred_before=end,
        ),
        headers={"Cache-Control": "no-store"},
    )


@AppRouter.api.get(
    "/settings/users/{user_uid}/sign-in-history",
    tags=["AppSettings.User"],
    responses=OpenApiSchema()
    .suc({"records": [dict], "page": "integer", "limit": "integer", "has_more": "bool"})
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF1004)
    .get(),
)
@RoleFilter.add(
    SettingRole,
    [SettingRoleAction.UserRead, SettingRoleAction.UserAuditRead],
    RoleFinder.setting,
    allowed_all_admin=False,
)
@AuthFilter.add("admin")
def get_user_sign_in_history_in_settings(
    user_uid: Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")],
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    outcome: Literal["all", "success", "failure"] = "all",
    occurred_from: Annotated[str | None, Query(max_length=40)] = None,
    occurred_before: Annotated[str | None, Query(max_length=40)] = None,
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    start, end = _sign_in_bound(occurred_from), _sign_in_bound(occurred_before)
    if start is not None and end is not None and start >= end:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    result = service.user.get_sign_in_history_in_settings(
        user_uid,
        page=page,
        limit=limit,
        is_success=None if outcome == "all" else outcome == "success",
        occurred_from=start,
        occurred_before=end,
    )
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.get(
    "/settings/users/{user_uid}/effective-permissions",
    tags=["AppSettings.User"],
    responses=OpenApiSchema().suc({"inspection": dict}).auth().forbidden().err(404, ApiErrorCode.NF1004).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_user_effective_permissions_in_settings(
    user_uid: Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")],
    project_uid: Annotated[str, Query(pattern=r"^[0-9A-Za-z]{11}$")],
    request: Request,
    card_uid: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z]{11}$")] = None,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    """Inspect native board or card read access without authenticating as the target."""
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectRole
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction

    project_id = SnowflakeID.from_short_code(project_uid)
    # Resolve caller scope before looking up target grants or resource metadata.
    if project_id not in service.card.resolve_work_visibility_contexts(
        user, CollaborationChannel.Api, project_ids={project_id}
    ):
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)
    channel = (
        request.scope.get("collaboration_channel", CollaborationChannel.Api) if card_uid else CollaborationChannel.Api
    )
    readable_card = None
    if card_uid:
        readable_card = service.card.resolve_readable_card(project_id, card_uid, user, channel, metadata_only=True)
        if readable_card is None:
            raise ApiException.NotFound_404(ApiErrorCode.NF1004)
    with DbSession.use(readonly=False) as db:
        target = db.exec(
            SqlBuilder.select.table(User)
            .where(User.id == SnowflakeID.from_short_code(user_uid), User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        ).first()
        project = db.exec(
            SqlBuilder.select.table(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        ).first()
        if target is None or project is None:
            raise ApiException.NotFound_404(ApiErrorCode.NF1004)
        member = db.exec(
            SqlBuilder.select.table(ProjectAssignedUser).where(
                ProjectAssignedUser.project_id == project_id, ProjectAssignedUser.user_id == target.id
            )
        ).first()
        role = db.exec(
            SqlBuilder.select.table(ProjectRole)
            .where(ProjectRole.project_id == project_id, ProjectRole.user_id == target.id)
            .execution_options(populate_existing=True)
        ).first()
    allowed = project_id in service.card.resolve_work_visibility_contexts(
        target, CollaborationChannel.Api, project_ids={project_id}
    )
    if card_uid:
        allowed = (
            allowed
            and service.card.resolve_readable_card(project_id, card_uid, target, channel, metadata_only=True)
            is not None
        )
    sources = []
    if allowed:
        if project.owner_id == target.id:
            sources.append({"type": "project_owner"})
        if member and target.is_admin:
            sources.append({"type": "administrator_project_membership"})
        if member and role and role.is_granted(ProjectRoleAction.Read):
            sources.append({"type": "direct_project_role", "uid": role.get_uid()})
        if card_uid:
            sources.append({"type": "native_card_visibility"})
    return JsonResponse(
        content={
            "inspection": {
                "user_uid": target.get_uid(),
                "resource": {"type": "card", "uid": readable_card[1].get_uid()}
                if readable_card
                else {"type": "project", "uid": project.get_uid()},
                "action": "card.read" if card_uid else "read",
                "allowed": allowed,
                "sources": sources,
                "reason": None
                if allowed
                else ("native_card_read_scope_denied" if card_uid else "native_project_read_scope_denied"),
                "channel": channel.value,
            }
        },
        headers={"Cache-Control": "no-store"},
    )


@AppRouter.api.get(
    "/settings/users/{user_uid}",
    tags=["AppSettings.User"],
    responses=OpenApiSchema().suc({"user": dict}).auth().forbidden().err(404, ApiErrorCode.NF1004).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_user_in_settings(
    user_uid: Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")],
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    users = service.user.get_api_list_in_settings(user_uid=user_uid, limit=1)
    if not users:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)
    return JsonResponse(content={"user": users[0]})


@AppRouter.api.post(
    "/settings/users",
    tags=["AppSettings.User"],
    responses=OpenApiSchema(201).auth().err(409, ApiErrorCode.EX1003).forbidden().get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserCreate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def create_user_in_settings(form: CreateUserForm, service: DomainService = DomainService.scope()) -> JsonResponse:
    user, _ = service.user.get_by_email(form.email)
    if user:
        raise ApiException.Conflict_409(ApiErrorCode.EX1003)

    form_dict = form.model_dump()
    if form.should_activate:
        now = SafeDateTime.now()
        form_dict["created_at"] = now
        form_dict["updated_at"] = now
        form_dict["activated_at"] = now

    user, user_profile = service.user.create(form_dict)
    api_user = user.api_response()
    api_user.update(user_profile.api_response())
    api_user["created_at"] = user.created_at
    api_user["activated_at"] = user.activated_at
    api_user["is_admin"] = user.is_admin
    api_user["setting_role_actions"] = []
    api_user["api_key_role_actions"] = []
    api_user["mcp_role_actions"] = []
    AppSettingPublisher.user_created({"user": api_user})

    return JsonResponse(status_code=status.HTTP_201_CREATED)


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{user_uid}", "user"),
        "firstname",
        "firstname",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{user_uid}", "user"),
        "lastname",
        "lastname",
    ),
)
@AppRouter.api.put(
    "/settings/users/{user_uid}",
    tags=["AppSettings.User"],
    responses=OpenApiSchema()
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF1004)
    .err(409, ApiErrorCode.OP1005)
    .err(409, ApiErrorCode.OP1004)
    .get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_user_in_settings(
    user_uid: str, form: UpdateUserForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    target_user = service.user.get_by_id_like(user_uid)
    if not target_user:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    if user.id == target_user.id and (form.is_admin is False or form.activate is False):
        raise ApiException.Conflict_409(ApiErrorCode.OP1005)

    form_dict = form.model_dump(exclude_unset=True)
    if user.id != target_user.id:
        if form.activate:
            form_dict["activated_at"] = SafeDateTime.now()
        elif form.activate is False:
            form_dict["activated_at"] = None

    try:
        if not service.user.update(target_user, form_dict, from_setting=True):
            raise ApiException.NotFound_404(ApiErrorCode.NF1004)
    except UserOwnershipError:
        raise ApiException.Conflict_409(ApiErrorCode.OP1004) from None

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{user_uid}", "user")
    )
)
@AppRouter.api.delete(
    "/settings/users/{user_uid}",
    tags=["AppSettings.User"],
    responses=OpenApiSchema()
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF1004)
    .err(409, ApiErrorCode.OP1003)
    .err(409, ApiErrorCode.OP1004)
    .get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserDelete], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def delete_user_in_settings(
    user_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    target_user = service.user.get_by_id_like(user_uid)
    if not target_user:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    if target_user.id == user.id:
        raise ApiException.Conflict_409(ApiErrorCode.OP1003)

    try:
        service.user.delete(target_user)
    except UserOwnershipError:
        raise ApiException.Conflict_409(ApiErrorCode.OP1004) from None

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{user_uids}", "user")
    )
)
@AppRouter.api.delete(
    "/settings/users",
    tags=["AppSettings.User"],
    responses=OpenApiSchema().auth().forbidden().err(409, ApiErrorCode.OP1004).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserDelete], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def delete_selected_users_in_settings(
    form: DeleteSelectedUsersForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    user_uid = user.get_uid()
    form.user_uids = [uid for uid in form.user_uids if uid != user_uid]

    try:
        service.user.delete_selected(form.user_uids)
    except UserOwnershipError:
        raise ApiException.Conflict_409(ApiErrorCode.OP1004) from None

    return JsonResponse()


@AppRouter.api.put(
    "/settings/users/{user_uid}/setting-role",
    tags=["AppSettings.User"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF1004).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_user_setting_role(
    user_uid: str,
    form: UpdateSettingRoleForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    target_user = service.user.get_by_id_like(user_uid)
    if not target_user:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    # Prevent self-demotion from super admin
    if user.id == target_user.id:
        all_actions = list(SettingRoleAction._member_map_.values())
        if set(form.actions) != set(all_actions):
            raise ApiException.BadRequest_400(ApiErrorCode.OP1003)

    # Grant all permissions
    all_actions = list(SettingRoleAction._member_map_.values())
    if set(form.actions) == set(all_actions) or ALL_GRANTED in form.actions:
        service.user.grant_all_setting_roles(target_user)
    elif not form.actions:
        service.user.grant_setting_roles(target_user, [])
    else:
        service.user.grant_setting_roles(target_user, form.actions)

    return JsonResponse()


@AppRouter.api.put(
    "/settings/users/{user_uid}/api-key-role",
    tags=["AppSettings.User"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF1004).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_user_api_key_role(
    user_uid: str, form: UpdateApiKeyRoleForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    target_user = service.user.get_by_id_like(user_uid)
    if not target_user:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    all_actions = ApiKeyRole.get_all_actions()

    if set(form.actions) == set(all_actions) or ALL_GRANTED in form.actions:
        service.api_key.grant_all_roles(target_user)
    elif not form.actions:
        service.api_key.grant_roles(target_user, [])
    else:
        service.api_key.grant_roles(target_user, form.actions)

    return JsonResponse()


@AppRouter.api.put(
    "/settings/users/{user_uid}/mcp-role",
    tags=["AppSettings.User"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF1004).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.UserUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_user_mcp_role(
    user_uid: str, form: UpdateMcpRoleForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    target_user = service.user.get_by_id_like(user_uid)
    if not target_user:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    all_actions = McpRole.get_all_actions()

    if set(form.actions) == set(all_actions) or ALL_GRANTED in form.actions:
        service.mcp_tool_group.grant_all_roles(target_user)
    elif not form.actions:
        service.mcp_tool_group.grant_roles(target_user, [])
    else:
        service.mcp_tool_group.grant_roles(target_user, form.actions)

    return JsonResponse()
