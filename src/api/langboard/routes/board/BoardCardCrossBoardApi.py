"""Native LINK operation; clone and transfer await their separate implementations."""

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


@AppRouter.schema(permission=ApiPermission.Create)
@AppRouter.api.post(
    "/board/{project_uid}/card/cross-board",
    tags=["Board.Card"],
    description="Create or return a source-authorized card mirror. Only link is supported.",
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
    if form.operation != "link":
        raise ApiException.BadRequest_400()
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
