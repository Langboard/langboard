from datetime import timedelta
from time import perf_counter
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import (
    ApiErrorCode,
    ApiException,
    ApiPermission,
    AppRouter,
    EEditorCollaborationType,
    JsonResponse,
    collaborative_block,
    collaborative_edit,
    create_editor_collaboration_document_id,
)
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardRelationship,
    Checklist,
    GlobalCardRelationshipType,
    Project,
    ProjectBotSchedule,
    ProjectBotScope,
    ProjectColumn,
    ProjectColumnBotSchedule,
    ProjectColumnBotScope,
    ProjectLabel,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.bases import ALL_GRANTED
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from ...card_workspace.domain import ArchivedCardCursor
from .forms import InviteProjectMemberForm, ProjectInvitationForm


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.post(
    "/board/{project_uid}/available",
    tags=["Board"],
    description="Check if the project is available.",
    responses=OpenApiSchema().suc({"title": "string"}).auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def is_project_available(project_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    return JsonResponse(content={"title": project.title})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}",
    tags=["Board"],
    description="Get project details without recording a visit. Use the explicit visit endpoint to record recency.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "project": (
                    Project,
                    {
                        "schema": {
                            "all_members": [User],
                            "invited_member_uids": "string[]",
                            "current_auth_role_actions": [ALL_GRANTED, ProjectRoleAction],
                            "labels": [ProjectLabel],
                        }
                    },
                ),
                "project_bot_scopes": [ProjectBotScope],
                "project_bot_schedules": [ProjectBotSchedule],
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2001)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project(
    project_uid: str,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
    include_automation: bool = True,
) -> JsonResponse:
    timings = [perf_counter()]
    result = service.project.get_details(user_or_bot, project_uid, is_setting=False)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    project, response = result
    timings.append(perf_counter())
    project_bot_scopes = service.project.get_api_bot_scope_list(project) if include_automation else []
    project_bot_schedules = service.project.get_api_bot_schedule_list(project) if include_automation else []
    timings.append(perf_counter())
    result = JsonResponse(
        content={
            "project": response,
            "project_bot_scopes": project_bot_scopes,
            "project_bot_schedules": project_bot_schedules,
        }
    )
    timings.append(perf_counter())
    result.headers["Server-Timing"] = ", ".join(
        f"{name};dur={(end - start) * 1000:.3f}"
        for name, start, end in zip(
            ("project_details", "project_bots", "project_serialize"), timings, timings[1:]
        )
    )
    return result


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/visit",
    tags=["Board"],
    description="Record an explicit project visit after loading the board. Bot requests do not record visits.",
    responses=OpenApiSchema().suc({}).auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def record_project_visit(
    project_uid: str,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    if isinstance(user_or_bot, User):
        service.project.set_last_view(user_or_bot, project)
    return JsonResponse()


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/automation",
    tags=["Board"],
    description="Get board automation on demand without recording a visit.",
    responses=OpenApiSchema()
    .suc(
        {
            "project_bot_scopes": [ProjectBotScope],
            "project_bot_schedules": [ProjectBotSchedule],
            "column_bot_scopes": [ProjectColumnBotScope],
            "column_bot_schedules": [ProjectColumnBotSchedule],
        }
    )
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF2001)
    .get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project_automation(project_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    return JsonResponse(
        content={
            "project_bot_scopes": service.project.get_api_bot_scope_list(project),
            "project_bot_schedules": service.project.get_api_bot_schedule_list(project),
            "column_bot_scopes": service.project_column.get_api_bot_scopes_by_project(project),
            "column_bot_schedules": service.project_column.get_api_bot_schedule_list_by_project(project, None),
        }
    )


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/assigned-users",
    tags=["Board"],
    description="Get project assigned users.",
    responses=OpenApiSchema().suc({"users": [User]}).auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project_assigned_users(project_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    users = service.project.get_api_assigned_user_list(project)

    return JsonResponse(content={"users": users})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/member-candidates",
    tags=["Board"],
    description="Search project member invite candidates.",
    responses=OpenApiSchema().suc({"users": [User]}).auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def search_project_member_candidates(
    project_uid: str, query: str = "", user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    users = service.project.search_member_candidates(user, project_uid, query)
    if users is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    return JsonResponse(content={"users": users})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/columns",
    tags=["Board"],
    description="Get project columns.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "columns": [
                    (
                        ProjectColumn,
                        {"schema": {"count": "integer", "open_count": "integer", "incomplete_count": "integer"}},
                    )
                ]
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2001)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project_columns(
    project_uid: str, request: Request,
    user_or_bot: User | Bot = Auth.scope("all"), service: DomainService = DomainService.scope(),
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    resolved = service.card.resolve_visibility_context(project_uid, user_or_bot, channel)
    if resolved is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    project, context = resolved
    columns = service.project_column.get_api_list_by_project(project, context=context)
    return JsonResponse(content={"columns": columns})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/labels",
    tags=["Board"],
    description="Get project labels.",
    responses=OpenApiSchema().suc({"labels": [ProjectLabel]}).auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project_labels(project_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    labels = service.project_label.get_api_list_by_project(project)
    return JsonResponse(content={"labels": labels})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/unread-card-summaries",
    tags=["Board"],
    description="Get a bounded body-free page of the current reader's unread native cards.",
    responses=OpenApiSchema().suc({"items": "object[]", "next_cursor": ["string", None], "limit": "integer"})
    .auth().forbidden().err(400, ApiErrorCode.VA0000).err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_unread_card_summaries(
    project_uid: str, request: Request, user: User = Auth.scope("user"),
    limit: int = Query(default=50, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=512),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        result = service.card.get_unread_card_summaries(
            project_uid, user, limit=limit, cursor=cursor,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    response = JsonResponse(content=result)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/relation-summaries",
    tags=["Board"],
    description="Get a bounded, body-free page of the selected card's readable immediate relationships.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "card": {"uid": "string", "project_uid": "string", "title": "string"},
                "items": [
                    {
                        "relationship": (
                            CardRelationship,
                            {
                                "schema": {
                                    "parent_name": "string",
                                    "child_name": "string",
                                    "machine_semantic": ["string", None],
                                    "affects_readiness": "boolean",
                                    "is_system_default": "boolean",
                                    "is_active": "boolean",
                                }
                            },
                        ),
                        "target": {
                            "uid": "string",
                            "title": "string",
                            "project_uid": "string",
                            "project_title": "string",
                        },
                    }
                ],
                "next_cursor": ["string", None],
                "limit": "integer",
            }
        )
        .auth()
        .forbidden()
        .err(400, ApiErrorCode.VA0000)
        .err(404, ApiErrorCode.NF2001)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_card_relation_summaries(
    project_uid: str,
    card_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=25, max_length=25),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        result = service.card_relationship.get_api_relation_summaries(
            project_uid,
            card_uid,
            user_or_bot,
            limit=limit,
            cursor=cursor,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    response = JsonResponse(content=result)
    response.headers["Cache-Control"] = "private, no-store"
    return response

@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/cards/context",
    tags=["Board"],
    description="Find bounded project card context, including transcribed attachment text without embeddings.",
    responses=OpenApiSchema()
    .suc(
        {
            "cards": [
                {
                    "uid": "string",
                    "title": "string",
                    "description": {"content": "string"},
                    "project_column_name": "string",
                }
            ]
        }
    )
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF2001)
    .get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project_card_context(
    project_uid: str,
    request: Request,
    user: User = Auth.scope("user"),
    input_value: str = Query(min_length=1, max_length=1000),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    return JsonResponse(content={"cards": service.card.search_context_by_project(
        project, input_value, user=user,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/cards/archive",
    tags=["Board"],
    description="Search and page archived cards without loading them into the active board.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "cards": [(Card, {"schema": {"project_column_name": "string"}})],
                "total_count": "integer",
                "next_cursor": ["string", None],
                "limit": "integer",
            }
        )
        .auth()
        .forbidden()
        .err(400, ApiErrorCode.VA0000)
        .err(404, ApiErrorCode.NF2001)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_archived_project_cards(
    project_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    input_value: str | None = Query(default=None, min_length=1, max_length=1000),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    try:
        decoded = ArchivedCardCursor.decode(cursor) if cursor else None
        result = service.card.get_api_archived_page_by_project(
            project,
            limit,
            SafeDateTime.fromisoformat(decoded.archived_at) if decoded else None,
            decoded.card_uid if decoded else None,
            input_value,
            user_or_bot=user_or_bot,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    cards, total_count, next_fields = result
    next_cursor = ArchivedCardCursor(*next_fields).encode() if next_fields else None
    return JsonResponse(
        content={"cards": cards, "total_count": total_count, "next_cursor": next_cursor, "limit": limit}
    )


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/cards",
    tags=["Board"],
    description="Get project card summaries. Fetch card details for description and AI description.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "cards": [
                    (
                        Card,
                        {
                            "include_body": False,
                            "schema": {
                                "project_column_name": "string",
                                "count_comment": "integer",
                                "has_description": "boolean",
                                "completed": "boolean",
                                "is_check_card": "boolean",
                                "member_uids": "string[]",
                                "checklist_total_count": "integer",
                                "checklist_completed_count": "integer",
                                "relationships": [CardRelationship],
                                "labels": [ProjectLabel],
                                "creator?": {
                                    "uid": "string",
                                    "type": "Enum[user, bot]",
                                    "name": "string",
                                    "avatar": "string?",
                                    "created_at": "string",
                                },
                                "linked_resource?": {
                                    "type": "string",
                                    "uid": "string",
                                    "status": "Enum[available, forbidden, missing]",
                                    "title?": "string",
                                },
                            }
                        },
                    )
                ],
                "column_bot_scopes": [ProjectColumnBotScope],
                "column_bot_schedules": [ProjectColumnBotSchedule],
                "checklists": [Checklist],
                "global_relationships": [GlobalCardRelationshipType],
                "columns": [
                    (
                        ProjectColumn,
                        {"schema": {"count": "integer", "open_count": "integer", "incomplete_count": "integer"}},
                    )
                ],
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2001)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_project_cards(
    project_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
    include_automation: bool = True,
) -> JsonResponse:
    timings = [perf_counter()]
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    resolved = service.card.resolve_visibility_context(project_uid, user_or_bot, channel)
    if resolved is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    project, context = resolved
    global_relationships = service.app_setting.get_api_global_relationship_list()
    columns = service.project_column.get_api_list_by_project(project, context=context)
    archive_visible_since = SafeDateTime.now() - timedelta(days=project.archive_visible_days)
    timings.append(perf_counter())
    snapshot_timings: dict[str, float] = {}
    cards, raw_checklists = service.card.get_board_snapshot(
        project, user_or_bot, archive_visible_since, channel=channel, phase_timings=snapshot_timings
    )
    timings.append(perf_counter())
    checklists = [checklist.api_response() for checklist in raw_checklists]
    timings.append(perf_counter())
    column_bot_scopes = service.project_column.get_api_bot_scopes_by_project(project) if include_automation else []
    column_bot_schedules = (
        service.project_column.get_api_bot_schedule_list_by_project(project, columns) if include_automation else []
    )

    timings.append(perf_counter())
    column_names_by_uid = {col["uid"]: col["name"] for col in columns}
    for card in cards:
        card["project_column_name"] = column_names_by_uid.get(card["project_column_uid"], "")

    response = JsonResponse(
        content={
            "cards": cards,
            "checklists": checklists,
            "global_relationships": global_relationships,
            "columns": columns,
            "column_bot_scopes": column_bot_scopes,
            "column_bot_schedules": column_bot_schedules,
        }
    )

    timings.append(perf_counter())
    # Fixed names and durations only: never include board, actor or payload data.
    response.headers["Server-Timing"] = ", ".join(
        f"{name};dur={(end - start) * 1000:.3f}"
        for name, start, end in zip(
            ("board_context", "board_cards", "board_checklists", "board_bots", "board_serialize"),
            timings,
            timings[1:],
        )
    )
    if snapshot_timings:
        response.headers["Server-Timing"] += ", " + ", ".join(
            f"{name};dur={duration:.3f}" for name, duration in snapshot_timings.items()
        )
    return response


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/change-feed",
    tags=["Board"],
    description="Get a cursor-based board change feed for external orchestrators.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "entries": [
                    {
                        "activity_uid": "string",
                        "activity_type": "string",
                        "card_uid?": "string",
                        "project_uid": "string",
                        "actor_user_uid?": "string",
                        "actor_bot_uid?": "string",
                        "created_at": "string",
                    }
                ],
                "has_more": "boolean",
                "next_cursor?": "string",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2001)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_board_change_feed(
    project_uid: str,
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    """Return board changes for external consumption with cursor-based pagination."""

    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    result = service.card.get_change_feed(
        project_uid, limit=limit, before_activity_uid=cursor, user=user_or_bot,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)
    return JsonResponse(result)


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.BoardSettings, "{project_uid}", "members")
    )
)
@AppRouter.api.put(
    "/board/{project_uid}/assigned-users",
    tags=["Board"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def update_project_member(
    project_uid: str,
    form: InviteProjectMemberForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    direct_members = [service.user.get_by_id_like(uid) for uid in dict.fromkeys(form.member_uids)]
    if any(member is None or member.deleted_at is not None for member in direct_members):
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    result = service.project.update_assigned_users(
        user,
        project_uid,
        form.emails,
        direct_members=[member for member in direct_members if member is not None],
    )
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.BoardSettings, "{project_uid}", "members")
    )
)
@AppRouter.api.delete(
    "/board/{project_uid}/unassign/{assignee_uid}",
    tags=["Board"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2005).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def unassign_project_assignee(
    project_uid: str, assignee_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.project.unassign_assignee(user, project_uid, assignee_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2005)

    return JsonResponse()


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/is-assigned/{assignee_uid}",
    tags=["Board"],
    description="Check if the user is assigned to the project.",
    responses=OpenApiSchema().suc({"result": "bool"}).auth().forbidden().err(404, ApiErrorCode.NF1004).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def is_project_assignee(
    project_uid: str, assignee_uid: str, service: DomainService = DomainService.scope()
) -> JsonResponse:
    target = service.user.get_by_id_like(assignee_uid)
    if not target:
        raise ApiException.NotFound_404(ApiErrorCode.NF1004)

    result, _ = service.project.is_assigned(target, project_uid)
    return JsonResponse(content={"result": result})


@AppRouter.api.post(
    "/project/invite/details/{token}",
    tags=["Board"],
    responses=(
        OpenApiSchema().suc({"project": {"title": "string"}}).auth().forbidden().err(404, ApiErrorCode.NF2002).get()
    ),
)
@AuthFilter.add("user")
def get_invited_project_title(
    token: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    project = service.project_invitation.get_project_by_token(user, token)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2002)

    return JsonResponse(content={"project": {"title": project.title}})


@AppRouter.api.post(
    "/project/invite/accept",
    tags=["Board"],
    responses=OpenApiSchema().suc({"project_uid": "string"}).auth().forbidden().err(406, ApiErrorCode.NF2002).get(),
)
@AuthFilter.add("user")
def accept_project_invitation(
    form: ProjectInvitationForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.project_invitation.accept(user, form.invitation_token)
    if not result:
        raise ApiException.NotAcceptable_406(ApiErrorCode.NF2002)

    return JsonResponse(content={"project_uid": result})


@AppRouter.api.post(
    "/project/invite/decline",
    tags=["Board"],
    responses=OpenApiSchema().auth().forbidden().err(406, ApiErrorCode.NF2002).get(),
)
@AuthFilter.add("user")
def decline_project_invitation(
    form: ProjectInvitationForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.project_invitation.decline(user, form.invitation_token)
    if not result:
        raise ApiException.NotAcceptable_406(ApiErrorCode.NF2002)

    return JsonResponse()
