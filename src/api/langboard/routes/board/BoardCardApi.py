from dataclasses import replace
from datetime import datetime
from fastapi import Body, Request, status
from langboard_shared.core.db import EditorContentModel
from langboard_shared.core.exceptions.CardDeleteForbidden import CardDeleteForbidden
from langboard_shared.core.exceptions.RelationshipCycle import RelationshipCycle
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
    collaborative_rich,
    collaborative_text,
    create_editor_collaboration_document_id,
)
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.core.utils.Converter import convert_python_data
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardAttachment,
    CardBotScope,
    CardComment,
    CardRelationship,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    ProjectColumn,
    ProjectLabel,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.bases import ALL_GRANTED
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.CardAgentExecution import card_agent_execution
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.domain.services.DecisionGate import DecisionGateBlocked
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from langboard_shared.tasks.webhooks.ExecutionReadinessUow import current_execution
from ...card_workspace.application import get_card_bundle, validate_card_graph_patch
from ...card_workspace.application.execution_receipts import receipt_history
from ...card_workspace.domain import CardBundleInclude, CardGraphEdge, CardGraphNewCard, CommentPage, SectionPage
from ...card_workspace.infrastructure import NativeCardWorkspaceAdapter
from ...card_workspace.infrastructure.linked_wikis import visible_linked_wikis
from ..dashboard.DashboardForm import RecentCardsAvailabilityForm
from .CardAccess import require_visible_card
from .forms import (
    AssignUsersForm,
    CardifySelectionForm,
    ChangeCardDetailsForm,
    ChangeCardOrderForm,
    ChangeCardVisibilityForm,
    CopySelectionToWikiForm,
    CreateCardForm,
    PatchCardGraphForm,
    SetCardCompletedForm,
    UpdateCardLabelsForm,
    UpdateCardRelationshipsForm,
)


@AppRouter.api.post(
    "/board/{project_uid}/cards/available",
    tags=["Board.Card"],
    responses=OpenApiSchema().suc({"card_uids": "string[]"}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_available_recent_cards(
    project_uid: str,
    form: RecentCardsAvailabilityForm,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return JsonResponse(content={"card_uids": service.card.get_existing_uids(
        project_uid, form.card_uids, user=user,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}",
    tags=["Board.Card"],
    description="Get card details. Optional sections is a bounded comma-separated selection; omission returns all legacy sections.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "card": (
                    Card,
                    {
                        "schema": {
                            "project_column_name": "string",
                            "count_comment": "integer",
                            "project_members": [User],
                            "labels": [ProjectLabel],
                            "member_uids": "string[]",
                            "relationships": [CardRelationship],
                            "current_auth_role_actions": [ALL_GRANTED, ProjectRoleAction],
                            "can_delete": "boolean",
                            "linked_resource?": {
                                "type": "string",
                                "uid": "string",
                                "status": "Enum[available, forbidden, missing]",
                                "title?": "string",
                                "preview?": "string",
                                "content?": EditorContentModel,
                            },
                        }
                    },
                ),
                "checklists": [
                    (
                        Checklist,
                        {
                            "schema": {
                                "checkitems": [
                                    (
                                        Checkitem,
                                        {
                                            "schema": {
                                                "card_uid": "string",
                                                "timer_started_at?": "string",
                                                "cardified_card?": "string",
                                                "user?": User,
                                            }
                                        },
                                    ),
                                ]
                            }
                        },
                    ),
                ],
                "attachments": [CardAttachment],
                "linked_wikis": [{"wiki_uid": "string", "title": "string"}],
                "global_relationships": [GlobalCardRelationshipType],
                "project_columns": [
                    (
                        ProjectColumn,
                        {"schema": {"count": "integer", "open_count": "integer", "incomplete_count": "integer"}},
                    )
                ],
                "project_labels": [ProjectLabel],
                "bot_scopes": [CardBotScope],
                "whispers_allowed": "boolean",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@AuthFilter.add()
def get_card_details(
    project_uid: str,
    card_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
    location_only: bool = False,
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    # This route alone accepts a historical project component. Resolve the
    # current card audience and current native Read grant before revealing where
    # it lives; the other card routes retain their exact project-scoped gates.
    raw_sections = request.query_params.get("sections")
    include_description = raw_sections is None or "description" in raw_sections.split(",")
    resolved = service.card.resolve_canonical_card(
        project_uid, card_uid, user_or_bot, channel,
        metadata_only=location_only or not include_description,
    )
    if resolved is None:
        error = ApiException.NotFound_404(ApiErrorCode.NF2003)
        error.headers = {"Cache-Control": "no-store"}
        raise error
    project, card, context = resolved
    if location_only or project.get_uid() != project_uid:
        return JsonResponse(content={"canonical_project_uid": project.get_uid(), "card_uid": card.get_uid()},
                            headers={"Cache-Control": "no-store"})
    # A bounded additive selector; no selector retains the complete legacy response.
    allowed = {
        "description",
        "checklists",
        "attachments",
        "linked_wikis",
        "global_relationships",
        "project_columns",
        "project_labels",
        "bot_scopes",
        "execution_receipts",
    }
    sections = allowed if raw_sections is None else set(raw_sections.split(","))
    # Execution identity is an explicit read, excluded even from legacy full detail.
    allowed = allowed | {"agent_execution"}
    if raw_sections is not None and (len(raw_sections) > 256 or not sections or not sections <= allowed):
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    api_card = service.card.get_details(
        project,
        card,
        user_or_bot,
        channel=channel,
        include_description="description" in sections,
    )
    if api_card is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    is_linked_resource = card.is_linked_resource
    can_set_scopes = isinstance(user_or_bot, Bot)
    if isinstance(user_or_bot, User):
        actions = service.project.get_user_role_actions_by_project(user_or_bot, project)
        api_card["current_auth_role_actions"] = actions
        can_set_scopes = ALL_GRANTED in actions or ProjectRoleAction.Update.value in actions
    api_card["can_change_visibility"] = (
        channel == CollaborationChannel.HumanUI
        and isinstance(user_or_bot, User)
        and replace(
            context,
            can_update_card=ALL_GRANTED in api_card.get("current_auth_role_actions", [])
            or ProjectRoleAction.CardUpdate.value in api_card.get("current_auth_role_actions", []),
        ).can_change_visibility(CardVisibility(card.visibility), CardVisibility.Shared, confirmed=True)
    )
    api_card["can_delete"] = service.card.can_delete(user_or_bot, card)
    content = {
        "card": api_card,
        "whispers_allowed": not is_linked_resource and context.can_use_card_whisper(CardVisibility(card.visibility)),
    }
    if "checklists" in sections:
        content["checklists"] = [] if is_linked_resource else service.checklist.get_api_list_by_card(card)
    if "attachments" in sections:
        content["attachments"] = [] if is_linked_resource else service.card_attachment.get_api_list_by_card(card)
    if "linked_wikis" in sections:
        content["linked_wikis"] = (
            [] if is_linked_resource else visible_linked_wikis(project, card, user_or_bot, service)
        )
    if "global_relationships" in sections:
        content["global_relationships"] = (
            [] if is_linked_resource else service.app_setting.get_api_global_relationship_list()
        )
    if "project_columns" in sections:
        content["project_columns"] = service.project_column.get_api_list_by_project(project.id, context=context)
    if "project_labels" in sections:
        content["project_labels"] = [] if is_linked_resource else service.project_label.get_api_list_by_project(project)
    if "bot_scopes" in sections:
        content["bot_scopes"] = (
            service.card.get_api_bot_scope_list(project, card) if can_set_scopes and not is_linked_resource else []
        )
    if "execution_receipts" in sections:
        content["execution_receipts"] = receipt_history(card.id)
    if "agent_execution" in sections:
        content["agent_execution"] = None if is_linked_resource else card_agent_execution(project.id, card.id)
    return JsonResponse(content=content)


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/context",
    tags=["Board.Card"],
    description="Get bounded card context for project chat.",
    responses=(OpenApiSchema().suc({"scope_context": "object"}).auth().forbidden().err(404, ApiErrorCode.NF2003).get()),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_card_context(
    project_uid: str,
    card_uid: str,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
    *,
    request: Request,
) -> JsonResponse:
    records = service.card.resolve_readable_card(project_uid, card_uid, user_or_bot, request.scope.get("collaboration_channel", CollaborationChannel.Api))
    if not records:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    _, card, _ = records

    adapter = NativeCardWorkspaceAdapter(user_or_bot, service, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
    context = get_card_bundle(
        adapter,
        project_uid,
        card_uid,
        CommentPage(),
        SectionPage(),
        [
            CardBundleInclude.Description,
            CardBundleInclude.People,
            CardBundleInclude.Classification,
            CardBundleInclude.Checklists,
            CardBundleInclude.Attachments,
            CardBundleInclude.Metadata,
        ],
    )
    payload = context.model_dump(mode="json")
    fence = current_execution(card.id, include_description=True)
    if fence is None or payload.get("card") is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    revision, is_ready, generation = fence.revision, fence.is_ready, fence.generation
    core_revision = payload["card"]["core"].get("updated_at")
    try:
        same_revision = datetime.fromisoformat(core_revision.replace("Z", "+00:00")) == revision
    except (AttributeError, ValueError):
        same_revision = False
    # READ COMMITTED across independent section reads is insufficient: a label
    # rename need not touch Card.updated_at. Accept task semantics only when
    # they agree with the same primary statement that reads readiness/generation.
    observed = adapter.task_content
    same_content = observed is not None and observed == {
        "title": fence.title,
        "description": fence.description,
        "labels": sorted(fence.labels),
        "assignee_ids": sorted(fence.assignee_ids),
    }
    linked = getattr(card, "is_linked_resource", False)
    if not same_revision or (linked and is_ready) or (not linked and not same_content):
        raise ApiException.Conflict_409()
    payload["card"]["execution"] = {"is_ready": is_ready, "generation": generation}
    payload["card"]["execution_receipts"] = receipt_history(card.id)
    return JsonResponse(content={"scope_context": payload})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/comments",
    tags=["Board.Card"],
    description="Get card comments.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "comments": [
                    (
                        CardComment,
                        {
                            "schema": {
                                "user?": User,
                                "bot?": Bot,
                                "reactions": {"<reaction type>": ["<user or bot uid>"]},
                            }
                        },
                    ),
                ]
            }
        )
        .auth()
        .forbidden()
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_card_comments(project_uid: str, card_uid: str, request: Request, user_or_bot: User | Bot = Auth.scope("all"), service: DomainService = DomainService.scope()) -> JsonResponse:
    resolved = service.card.resolve_readable_card(project_uid, card_uid, user_or_bot, request.scope.get("collaboration_channel", CollaborationChannel.Api))
    if resolved is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    comments = service.card_comment.get_api_list_by_card(resolved[1])
    return JsonResponse(content={"comments": comments})


@AppRouter.schema(form=CreateCardForm, permission=ApiPermission.Create)
@AppRouter.api.post(
    "/board/{project_uid}/card",
    tags=["Board.Card"],
    description="Create a card.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "card": (
                    Card,
                    {
                        "schema": {
                            "labels": [ProjectLabel],
                            "member_uids": "string[]",
                            "relationships": [CardRelationship],
                            "current_auth_role_actions": [ALL_GRANTED, ProjectRoleAction],
                        }
                    },
                )
            },
            201,
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2004)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardWrite], RoleFinder.project)
@AuthFilter.add()
def create_card(
    project_uid: str,
    form: CreateCardForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        result = service.card.create(
            user_or_bot,
            project_uid,
            form.project_column_uid,
            form.title,
            form.description,
            form.assign_users,
            next_action=form.next_action,
            priority=form.priority,
        )
    except PermissionError as exc:
        raise ApiException.Forbidden_403() from exc
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2004)
    _, api_card = result

    return JsonResponse(content={"card": api_card}, status_code=status.HTTP_201_CREATED)


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "title"), "title", "title"
    ),
    collaborative_rich(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "description"),
        "description",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "deadline"),
        "deadline_at",
        "value",
    ),
)
@AppRouter.schema(form=ChangeCardDetailsForm, permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/details",
    tags=["Board.Card"],
    description="Change card details.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "title?": "string",
                "deadline_at?": "string",
                "next_action?": "string?",
                "priority?": "P0 | P1 | P2 | P3",
                "description?": EditorContentModel,
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def change_card_details(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: ChangeCardDetailsForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    form_dict = {}
    for key in ChangeCardDetailsForm.model_fields:
        value = getattr(form, key)
        if key in {"next_action", "priority"}:
            if key in form.model_fields_set:
                form_dict[key] = value
            continue
        if value is None:
            continue
        elif key == "deadline_at":
            if value:
                value = SafeDateTime.fromisoformat(value)
                if value.tzinfo is None:
                    value = value.replace(tzinfo=SafeDateTime.now().astimezone().tzinfo)
            else:
                value = None
        form_dict[key] = value

    result = service.card.update(user_or_bot, project_uid, card_uid, form_dict)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    if result is True:
        response = {}
        for key in ChangeCardDetailsForm.model_fields:
            if key in {"next_action", "priority"}:
                if key in form.model_fields_set:
                    response[key] = getattr(form, key)
                continue
            if ["title", "description", "deadline_at"].count(key) == 0:
                continue
            value = getattr(form, key)
            if value is None and key != "deadline_at":
                continue
            response[key] = convert_python_data(value)
        return JsonResponse(content=response)

    return JsonResponse(content=result)


@AppRouter.schema(form=ChangeCardVisibilityForm, permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/visibility", tags=["Board.Card"],
    description="Change internal/shared visibility with explicit human disclosure confirmation.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add("user")
def change_card_visibility(
    project_uid: str, card_uid: str, request: Request, form: ChangeCardVisibilityForm,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user, service)
    try:
        result = service.card.change_visibility(
            user, project_uid, card_uid, CardVisibility(form.visibility),
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
            confirmed=form.confirmed, expected_visibility=form.expected_visibility,
        )
    except PermissionError as exc:
        raise ApiException.Forbidden_403() from exc
    except ValueError as exc:
        raise ApiException.Conflict_409() from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse(content=result)


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "members"),
        "assigned_users",
        "selected-member-uids",
    )
)
@AppRouter.schema(form=AssignUsersForm, permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/assigned-users",
    tags=["Board.Card"],
    description="Assign users to a card.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def update_card_assigned_users(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: AssignUsersForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    try:
        result = service.card.update_assigned_users(user_or_bot, project_uid, card_uid, form.assigned_users)
    except PermissionError as exc:
        raise ApiException.Forbidden_403() from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    return JsonResponse()


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/assigned-users/{assignee_uid}",
    tags=["Board.Card"],
    description="Add one active project member to a card without replacing its existing assignees.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2005).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def add_card_assignee(
    project_uid: str,
    card_uid: str,
    request: Request,
    assignee_uid: str,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    try:
        result = service.card.assign_member(user_or_bot, project_uid, card_uid, assignee_uid)
    except LookupError as exc:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003) from exc
    except PermissionError as exc:
        raise ApiException.Forbidden_403() from exc
    except ValueError as exc:
        raise ApiException.NotFound_404(ApiErrorCode.NF2005) from exc
    return JsonResponse(content=result)


@AppRouter.schema(form=ChangeCardOrderForm, permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/order",
    tags=["Board.Card"],
    description="Change card order or move to another project column.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def change_card_order_or_move_column(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: ChangeCardOrderForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    try:
        result = service.card.change_order(
            user_or_bot, project_uid, card_uid, form.order, form.parent_uid,
            move_descendants=form.move_descendants, confirmation_revision=form.confirmation_revision,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    except DecisionGateBlocked as exc:
        return JsonResponse(content=exc.payload(), status_code=409)
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    return JsonResponse(content=result if isinstance(result, dict) else {})


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "labels"),
        "labels",
        "selected-label-uids",
    )
)
@AppRouter.schema(form=UpdateCardLabelsForm, permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/labels",
    tags=["Board.Card"],
    description="Update assigned labels to a card.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def update_card_labels(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: UpdateCardLabelsForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    result = service.card.update_labels(user_or_bot, project_uid, card_uid, form.labels)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    return JsonResponse()


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-parents"),
        "relationships",
        "selected-relationships",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-children"),
        "relationships",
        "selected-relationships",
    ),
)
@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/relationships", tags=["Board.Card"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_card_relationships(
    project_uid: str, card_uid: str, request: Request,
    user_or_bot: User | Bot = Auth.scope("all"), service: DomainService = DomainService.scope(),
) -> JsonResponse:
    resolved = service.card.resolve_readable_card(
        project_uid, card_uid, user_or_bot,
        request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )
    if resolved is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse(content={"relationships": service.card_relationship.get_api_list_by_card(
        resolved[1], context=resolved[2],
    )})


@AppRouter.schema(form=UpdateCardRelationshipsForm, permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/relationships",
    tags=["Board.Card"],
    description="Update card relationships.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def update_card_relationships(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: UpdateCardRelationshipsForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    try:
        result = service.card_relationship.update(
            user_or_bot, project_uid, card_uid, form.is_parent, form.relationships
        )
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except RelationshipCycle as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    return JsonResponse(content={"relationships": result})


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-parents")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-children")
    ),
)
@AppRouter.schema(form=PatchCardGraphForm, permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/relationships/patch",
    tags=["Board.Card"],
    description="Atomically add or remove card relationships against the current project graph.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def patch_card_relationships(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: PatchCardGraphForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    patch = validate_card_graph_patch(
        project_uid,
        card_uid,
        [CardGraphNewCard(item.client_ref, item.title, item.description) for item in form.new_cards],
        [CardGraphEdge(item.parent_ref, item.child_ref, item.relationship_type_uid) for item in form.add_edges],
        form.remove_relationship_uids,
    )
    try:
        result = service.card_relationship.apply_graph_patch(user_or_bot, *patch)
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except RelationshipCycle as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if result is None:
        raise ValueError("Anchor card not found in project")
    return JsonResponse(content=result)


@collaborative_edit(
    collaborative_block(create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "title")),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "description")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "deadline")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "members")
    ),
    collaborative_block(create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "labels")),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-parents")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-children")
    ),
)
@AppRouter.schema(form=SetCardCompletedForm, permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/completion",
    tags=["Board.Card"],
    description="Set a check card's completion state, including a deadline card without user checklists.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "completed": "boolean",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def set_card_completed(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: SetCardCompletedForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    result = service.card.set_card_completed(user_or_bot, project_uid, card_uid, form.completed)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse({"completed": form.completed})


@AppRouter.schema(form=CardifySelectionForm, permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/cardify",
    tags=["Board.Card"],
    description="Extract the selected body fragment into a child card with a [[link]] back.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "child_card_uid": "string",
                "child_card_title": "string",
                "link_markdown": "string",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def cardify_selection(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: CardifySelectionForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    result = service.card.cardify_selection(user_or_bot, project_uid, card_uid, form.selected_markdown)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse(result)


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/convert-checkboxes",
    tags=["Board.Card"],
    description="Convert markdown checkboxes in the card body into a native checklist.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "checklist_uid?": "string",
                "item_count": "integer",
                "checked_count?": "integer",
                "remaining_markdown?": "string",
                "message?": "string",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def convert_card_checkboxes(
    project_uid: str,
    card_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    result = service.card.convert_description_checkboxes(user_or_bot, project_uid, card_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse(result)


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/comment-counts",
    tags=["Board.Card"],
    description="Get comment counts grouped by description section anchor.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "counts": [
                    {
                        "anchor": "string",
                        "count": "integer",
                    }
                ]
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_card_comment_counts(
    project_uid: str,
    card_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    counts = service.card.get_section_comment_counts(project_uid, card_uid)
    if counts is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse({"counts": counts})


@AppRouter.schema(form=CopySelectionToWikiForm, permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/copy-selection-to-wiki",
    tags=["Board.Card"],
    description="Copy the selected body fragment into a new project wiki.",
    responses=(
        OpenApiSchema()
        .suc(
            {
                "wiki_uid": "string",
                "wiki_title": "string",
                "card_body_unchanged": "boolean",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF2003)
        .get()
    ),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def copy_selection_to_wiki(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: CopySelectionToWikiForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    result = service.card.copy_selection_to_wiki(
        user_or_bot, project_uid, card_uid, form.selected_markdown, form.wiki_title
    )
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    return JsonResponse(result)


@AppRouter.schema(permission=ApiPermission.Delete)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/archive",
    tags=["Board.Card"],
    description="Archive a card.",
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add()
def archive_card(
    project_uid: str,
    card_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    project = service.project.get_by_id_like(project_uid)
    if project is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    result = service.card.archive(user_or_bot, project, card_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "title")),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "description")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "deadline")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "members")
    ),
    collaborative_block(create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "labels")),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-parents")
    ),
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Card, "{card_uid}", "relationships-children")
    ),
)
@AppRouter.schema(permission=ApiPermission.Delete)
@AppRouter.api.delete(
    "/board/{project_uid}/card/{card_uid}",
    tags=["Board.Card"],
    description="Delete an archived card when the signed-in actor is its original author or an administrator.",
    responses=OpenApiSchema().auth().forbidden().err(403, ApiErrorCode.PE2006).err(404, ApiErrorCode.NF2003).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardDelete], RoleFinder.project)
@AuthFilter.add()
def delete_card(
    project_uid: str,
    card_uid: str,
    request: Request,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    try:
        result = service.card.delete(user_or_bot, project_uid, card_uid)
    except CardDeleteForbidden as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE2006) from exc
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)

    return JsonResponse()


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/seen",
    tags=["Board.Card"],
    description="Mark the card's latest change as seen for the current user.",
    responses=OpenApiSchema()
    .suc({"card_uid": "string", "seen_change_seq": "integer"})
    .auth()
    .forbidden()
    .err(404, ApiErrorCode.NF2004)
    .get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def mark_card_seen(
    project_uid: str,
    card_uid: str,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.card.mark_card_seen(user, card_uid, project_uid, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2004)

    return JsonResponse(content=result)


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/read-state", tags=["Board.Card"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_card_read_state(
    project_uid: str, card_uid: str, request: Request,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.card.get_card_read_state(project_uid, card_uid, user=user, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2004)
    return JsonResponse(content=result)


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/unread", tags=["Board.Card"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def mark_card_unread(
    project_uid: str, card_uid: str, request: Request,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.card.set_card_read_state(user, project_uid, card_uid, False, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2004)
    return JsonResponse(content=result)


@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/content-blocks",
    tags=["Board.Card"],
    description="Atomically replace every structured content block of a card (editor full-save).",
    responses=OpenApiSchema().suc({"blocks": "object[]"}).auth().forbidden().err(404, ApiErrorCode.NF2004).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add("all")
def replace_card_content_blocks(
    project_uid: str,
    card_uid: str,
    request: Request,
    blocks: list[dict] = Body(embed=True),
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user_or_bot, service)
    result = service.card_content_block.replace_blocks(user_or_bot, project_uid, card_uid, blocks)
    return JsonResponse(content={"blocks": result})
