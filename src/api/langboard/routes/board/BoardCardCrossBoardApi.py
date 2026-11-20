"""Native source-authorized cross-board link, clone and same-identity move operations."""

from fastapi import Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from pydantic import BaseModel


class CrossBoardCardForm(BaseModel):
    source_project_uid: str
    source_card_uid: str
    destination_column_uid: str
    operation: str = "link"
    request_uid: str | None = None
    confirmation_token: str | None = None


@AppRouter.schema(permission=ApiPermission.Create)
@AppRouter.api.post(
    "/board/{project_uid}/card/cross-board",
    tags=["Board.Card"],
    description="Link, independently clone, or move a source-authorized card with current audience consent.",
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardWrite], RoleFinder.project)
@AuthFilter.add("user")
def cross_board_card(
    project_uid: str,
    form: CrossBoardCardForm,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    if form.operation not in {"link", "clone", "move"}:
        raise ApiException.BadRequest_400()
    if form.operation in {"clone", "move"}:
        if not form.request_uid:
            raise ApiException.BadRequest_400()
        try:
            operation = (service.card.move_cross_board_card if form.operation == "move"
                         else service.card.clone_cross_board_card)
            result = operation(
                user,
                form.source_project_uid,
                form.source_card_uid,
                project_uid,
                form.destination_column_uid,
                request_uid=form.request_uid,
                confirmation_token=form.confirmation_token,
                channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
            )
        except (TypeError, ValueError):
            raise ApiException.BadRequest_400() from None
        if result is None:
            raise ApiException.NotFound_404(ApiErrorCode.NF2003)
        return JsonResponse(
            content=result,
            status_code=409 if result.get("confirmation_required") else (
                200 if form.operation == "move" else 201 if result["created"] else 200
            ),
        )
    try:
        result = service.card.link_cross_board_card(
            user,
            form.source_project_uid,
            form.source_card_uid,
            project_uid,
            form.destination_column_uid,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    except (TypeError, ValueError):
        raise ApiException.NotFound_404(ApiErrorCode.NF2003) from None
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    _, card, created = result
    return JsonResponse(content={"card": card, "created": created}, status_code=201 if created else 200)
