"""Board owners explicitly grant or revoke a compactor's independent authority."""

from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiPermission, AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services.CompactorBoardAuthority import (
    CompactorAuthorityError,
    get_compactor_board_grant,
    set_compactor_board_grant,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class CompactorGrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_read: bool = Field(strict=True)
    can_compact: bool = Field(strict=True)
    can_archive: bool = Field(strict=True)


def _response(command, *args, **kwargs):
    try:
        return JsonResponse(content=command(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except CompactorAuthorityError as error:
        return JsonResponse(content={"code": error.code}, status_code=error.status,
                            headers={"Cache-Control": "no-store"})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get("/board/{project_uid}/compactor/{profile_uid}/grant", tags=["Board.Compactor"])
@AuthFilter.add("user")
def read_compactor_grant(project_uid: str, profile_uid: str, user: User = Auth.scope("user")):
    return _response(get_compactor_board_grant, user, project_uid, profile_uid)


@AppRouter.schema(form=CompactorGrantBody, permission=ApiPermission.Edit)
@AppRouter.api.put("/board/{project_uid}/compactor/{profile_uid}/grant", tags=["Board.Compactor"])
@AuthFilter.add("user")
def update_compactor_grant(project_uid: str, profile_uid: str, body: CompactorGrantBody,
                           user: User = Auth.scope("user")):
    return _response(set_compactor_board_grant, user, project_uid, profile_uid, **body.model_dump())
