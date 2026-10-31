"""Native card and wiki files readable by the current board actor."""

from typing import Literal
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.security import Auth
from ...apps.BoardCloud import read_board_cloud


@AppRouter.api.get("/board/{project_uid}/cloud", tags=["Board"])
@AuthFilter.add("user")
def get_board_cloud(
    request: Request,
    project_uid: str,
    view: Literal["all", "recent", "files", "media", "links"] = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=4096),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return JsonResponse(
        headers={"Cache-Control": "no-store"},
        content=read_board_cloud(
            service,
            user,
            project_uid,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
            view=view,
            limit=limit,
            cursor=cursor,
        ),
    )
