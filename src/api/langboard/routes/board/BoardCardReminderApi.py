"""Permission-scoped card reminder REST routes."""

from uuid import UUID
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from pydantic import BaseModel, ConfigDict, Field
from ...card_workspace.application.reminders import (
    ReminderConflict,
    ReminderNotFound,
    ReminderRecipientOverrides,
    create_card_reminder_rule,
    list_card_reminder_rules,
    update_card_reminder_rule,
)


BASE_PATH = "/board/{project_uid}/card/{card_uid}/reminders"
TAGS = ["Board.Card"]
NO_STORE = {"Cache-Control": "no-store"}


class CreateReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ReminderSchedule
    request_uid: UUID
    shared: bool = False
    recipient_overrides: ReminderRecipientOverrides | None = None


class UpdateReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ReminderSchedule
    expected_revision: int = Field(ge=1)
    enabled: bool
    recipient_overrides: ReminderRecipientOverrides | None = None


@AppRouter.api.get(BASE_PATH, tags=TAGS)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def list_card_reminders(
    request: Request,
    project_uid: str,
    card_uid: str,
    cursor: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    try:
        content = list_card_reminder_rules(service, user, project_uid, card_uid, channel, cursor=cursor, limit=limit)
    except ReminderNotFound:
        raise ApiException.NotFound_404() from None
    return JsonResponse(content=content, headers=NO_STORE)


@AppRouter.schema(form=CreateReminderBody, permission=ApiPermission.Edit)
@AppRouter.api.post(BASE_PATH, tags=TAGS)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def create_card_reminder(
    request: Request,
    project_uid: str,
    card_uid: str,
    body: CreateReminderBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    try:
        item, replayed = create_card_reminder_rule(
            service,
            user,
            project_uid,
            card_uid,
            channel,
            schedule=body.schedule,
            shared=body.shared,
            request_uid=body.request_uid,
            recipient_overrides=body.recipient_overrides,
        )
    except ReminderNotFound:
        raise ApiException.NotFound_404() from None
    except PermissionError:
        raise ApiException.Forbidden_403() from None
    except ReminderConflict:
        raise ApiException.Conflict_409() from None
    except (ValueError, KeyError, OverflowError):
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content={"item": item}, headers=NO_STORE, status_code=200 if replayed else 201)


@AppRouter.schema(form=UpdateReminderBody, permission=ApiPermission.Edit)
@AppRouter.api.put(BASE_PATH + "/{rule_uid}", tags=TAGS)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def update_card_reminder(
    request: Request,
    project_uid: str,
    card_uid: str,
    rule_uid: str,
    body: UpdateReminderBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    try:
        item = update_card_reminder_rule(
            service,
            user,
            project_uid,
            card_uid,
            rule_uid,
            channel,
            schedule=body.schedule,
            expected_revision=body.expected_revision,
            enabled=body.enabled,
            recipient_overrides=body.recipient_overrides,
        )
    except ReminderNotFound:
        raise ApiException.NotFound_404() from None
    except PermissionError:
        raise ApiException.Forbidden_403() from None
    except ReminderConflict:
        raise ApiException.Conflict_409() from None
    except (ValueError, KeyError, OverflowError):
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content={"item": item}, headers=NO_STORE)
