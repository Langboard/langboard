from datetime import timedelta
from time import perf_counter
from fastapi import Depends, Query, Request, status
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import Card, Checkitem, Project, ProjectColumn, User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.ProjectTemplateService import (
    ProjectCreationConflict,
    ProjectCreationUnavailable,
)
from langboard_shared.security import Auth
from .DashboardForm import DashboardPagination, DashboardProjectCreateForm, RecentCardPairsAvailabilityForm


@AppRouter.api.get(
    "/dashboard/user/projects/starred",
    tags=["Dashboard"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "projects": [
                    (
                        Project,
                        {
                            "schema": {
                                "starred": "bool",
                                "last_viewed_at": "string",
                                "view_count": "integer",
                                "last_activity_at": "string?",
                                "related_to_current_user": "bool",
                                "related_activity_at": "string?",
                            }
                        },
                    )
                ]
            }
        )
        .auth()
        .forbidden()
        .get()
    ),
)
@AuthFilter.add("user")
def get_starred_projects(
    request: Request, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    projects = service.project.get_api_starred_project_list(
        user, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api)
    )

    return JsonResponse(content={"projects": projects})


@AppRouter.api.get(
    "/dashboard/projects",
    tags=["Dashboard"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "projects": [
                    (
                        Project,
                        {
                            "schema": {
                                "starred": "bool",
                                "last_viewed_at": "string",
                                "view_count": "integer",
                                "last_activity_at": "string?",
                                "related_to_current_user": "bool",
                                "related_activity_at": "string?",
                            }
                        },
                    ),
                ],
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
        .get()
    ),
)
@AuthFilter.add("user")
def get_projects(
    request: Request, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    timings = [perf_counter()]
    contexts = service.card.resolve_work_visibility_contexts(
        user, request.scope.get("collaboration_channel", CollaborationChannel.Api)
    )
    projects, project_ids = service.project.get_api_list(user, contexts=contexts)
    timings.append(perf_counter())
    columns = service.project_column.get_api_list_by_project(project_ids, contexts=contexts)
    timings.append(perf_counter())
    response = JsonResponse(content={"projects": projects, "columns": columns})
    timings.append(perf_counter())
    response.headers["Server-Timing"] = ", ".join(
        f"{name};dur={(end - start) * 1000:.3f}"
        for name, start, end in zip(
            ("dashboard_projects", "dashboard_columns", "dashboard_serialize"), timings, timings[1:]
        )
    )
    return response


@AppRouter.api.post(
    "/dashboard/projects/new",
    tags=["Dashboard"],
    responses=OpenApiSchema().suc({"project_uid": "string"}, 201).auth().forbidden().get(),
)
@AuthFilter.add("user")
def create_project(
    form: DashboardProjectCreateForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    try:
        project, _, _ = service.project_template.create_project(
            user,
            form.title,
            form.description,
            form.project_type,
            form.template_name,
            request_uuid=str(form.request_uuid) if form.request_uuid else None,
        )
    except ProjectCreationConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.VA0000) from exc
    except ProjectCreationUnavailable as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.VA0000) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    return JsonResponse(content={"project_uid": project.get_uid()}, status_code=status.HTTP_201_CREATED)


@AppRouter.api.put(
    "/dashboard/projects/{project_uid}/star",
    tags=["Dashboard"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@AuthFilter.add("user")
def toggle_star_project(
    project_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.project.toggle_star(user, project_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    return JsonResponse()


@AppRouter.api.get(
    "/dashboard/cards",
    tags=["Dashboard"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "cards": [
                    (
                        Card,
                        {
                            "schema": {
                                "project_column_name": "string",
                                "linked_resource?": {
                                    "type": "string",
                                    "uid": "string",
                                    "status": "string",
                                    "title?": "string",
                                },
                            }
                        },
                    )
                ],
                "projects": [Project],
            }
        )
        .auth()
        .forbidden()
        .get()
    ),
)
@AuthFilter.add("user")
def get_card_list(
    request: Request,
    pagination: DashboardPagination = Depends(),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    cards, projects = service.card.get_dashboard_list(user, pagination, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))

    return JsonResponse(content={"cards": cards, "projects": projects})


@AppRouter.api.get(
    "/dashboard/work/my",
    tags=["Dashboard"],
    responses=OpenApiSchema()
    .suc(
        {
            "cards": [
                {
                    "uid": "string",
                    "title": "string",
                    "project_uid": "string",
                    "project_title": "string",
                    "project_column_name": "string",
                    "deadline_at": "string?",
                    "updated_at": "string",
                    "reasons": ["string"],
                }
            ]
        }
    )
    .auth()
    .forbidden()
    .get(),
)
@AuthFilter.add("user")
def get_my_work(
    request: Request,
    project_uid: str | None = None,
    limit: int = Query(default=50, ge=1, le=50),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    contexts = service.card.resolve_work_visibility_contexts(user, channel)
    projects = [{"uid": SnowflakeID(project_id).to_short_code()} for project_id in contexts]
    if project_uid is not None:
        projects = [project for project in projects if project["uid"] == project_uid]
        if not projects:
            raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    if not projects:
        return JsonResponse(content={"cards": []})

    cards = service.card.get_my_work_cards(
        user,
        projects,
        {"assigned", "mentioned", "due_soon", "overdue", "created"},
        service.notification.get_mentioned_card_ids(user),
        SafeDateTime.now() + timedelta(days=7),
        "updated_at",
        None,
        None,
        limit,
        channel=channel,
        contexts=contexts,
    )
    return JsonResponse(content={"cards": cards})


@AppRouter.api.get(
    "/dashboard/work/assigned",
    tags=["Dashboard"],
    description="Read current user's assigned cards with the same cursor contract as native MCP list_my_work.",
    responses=OpenApiSchema().suc({"items": "object[]", "next_cursor": "string?"}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def get_assigned_work(
    request: Request,
    project_uid: str | None = None,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=25),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        result = service.card.list_assigned_work(user, project_uid, cursor, limit, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    return JsonResponse(content=result)


@AppRouter.api.get(
    "/dashboard/work/active",
    tags=["Dashboard"],
    responses=OpenApiSchema()
    .suc(
        {
            "active_work": [
                {
                    "checkitem": (
                        Checkitem,
                        {
                            "schema": {
                                "card_uid": "string",
                                "timer_started_at": "string",
                            }
                        },
                    ),
                    "card": Card,
                    "project": Project,
                }
            ]
        }
    )
    .auth()
    .forbidden()
    .get(),
)
@AuthFilter.add("user")
def get_active_work(
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return JsonResponse(content={"active_work": service.checkitem.get_active_work(user)})


@AppRouter.api.get(
    "/dashboard/tracking",
    tags=["Dashboard"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "checkitems": [
                    (
                        Checkitem,
                        {
                            "schema": {
                                "card_uid": "string",
                                "initial_timer_started_at": "string",
                                "timer_started_at": "string",
                            }
                        },
                    ),
                ],
                "cards": [Card],
                "projects": [Project],
            }
        )
        .auth()
        .forbidden()
        .get()
    ),
)
@AuthFilter.add("user")
def track_checkitems(
    pagination: DashboardPagination = Depends(),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    checkitems, cards, projects = service.checkitem.get_tracking_list(user, pagination)

    return JsonResponse(content={"checkitems": checkitems, "cards": cards, "projects": projects})


@AppRouter.api.post(
    "/dashboard/cards/available",
    tags=["Dashboard"],
    responses=OpenApiSchema().suc({"cards": [{"project_uid": "string", "card_uid": "string"}]}).auth().get(),
)
@AuthFilter.add("user")
def get_available_recent_card_pairs(
    form: RecentCardPairsAvailabilityForm,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    pairs = service.card.get_existing_pairs(
        [(card.project_uid, card.card_uid) for card in form.cards], user=user,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )
    return JsonResponse(content={"cards": [{"project_uid": project, "card_uid": card} for project, card in pairs]})
