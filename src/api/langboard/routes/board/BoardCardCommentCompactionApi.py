"""Native comment compaction; API and MCP callers share the domain command."""

from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardCommentCompactionService import CommentCompactionError
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from pydantic import BaseModel, ConfigDict, Field


class CommentCompactionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after: str | None = Field(default=None, max_length=64)
    retry: bool = Field(default=False, strict=True)
    expected_request_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def compaction_response(result):
    return JsonResponse(content=result, status_code=202 if result["status"] in {"pending", "running"} else 200,
                        headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/search-metadata", tags=["Board.Card.Comment"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_card_search_metadata(
    project_uid: str, card_uid: str, request: Request,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
):
    try:
        metadata = service.card_comment_compaction.get_search_metadata(user, project_uid, card_uid,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
        return JsonResponse(content=metadata, headers={"Cache-Control": "no-store"})
    except CommentCompactionError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})


@AppRouter.schema(form=CommentCompactionBody, permission=ApiPermission.Edit)
@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/comment-compactions", tags=["Board.Card.Comment"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add("user")
async def compact_card_comments(
    project_uid: str, card_uid: str, request: Request, body: CommentCompactionBody,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
):
    try:
        result = await service.card_comment_compaction.generate(user, project_uid, card_uid,
            after=body.after, retry=body.retry, expected_request_key=body.expected_request_key,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
        return compaction_response(result)
    except CommentCompactionError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/comment-compactions", tags=["Board.Card.Comment"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def list_card_comment_compactions(
    project_uid: str, card_uid: str, request: Request, before: str | None = Query(default=None, max_length=32),
    request_key: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
):
    try:
        result = service.card_comment_compaction.get_page(user, project_uid, card_uid, before=before, request_key_filter=request_key,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except CommentCompactionError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/comment-compactions/{compaction_uid}", tags=["Board.Card.Comment"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_card_comment_compaction(
    project_uid: str, card_uid: str, compaction_uid: str, request: Request,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
):
    try:
        result = service.card_comment_compaction.get(user, project_uid, card_uid, compaction_uid,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
        return compaction_response(result)
    except CommentCompactionError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/comment-compactions/{compaction_uid}/cancel", tags=["Board.Card.Comment"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add("user")
def cancel_card_comment_compaction(
    project_uid: str, card_uid: str, compaction_uid: str, request: Request,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
):
    try:
        return compaction_response(service.card_comment_compaction.cancel(user, project_uid, card_uid, compaction_uid,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api)))
    except CommentCompactionError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/comment-compactions/{compaction_uid}/evidence", tags=["Board.Card.Comment"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_card_comment_compaction_evidence(
    project_uid: str, card_uid: str, compaction_uid: str, request: Request, before: str | None = Query(default=None, max_length=64),
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
):
    try:
        result = service.card_comment_compaction.get_evidence(user, project_uid, card_uid, compaction_uid, before=before,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except CommentCompactionError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})
