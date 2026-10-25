"""Native project-owned Cloud uploads."""

from typing import Annotated
from fastapi import File, Form, Request, UploadFile, status
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder


@AppRouter.api.post("/board/{project_uid}/cloud/attachment", tags=["Board.Cloud"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def upload_project_attachment(
    project_uid: str,
    request: Request,
    attachment: UploadFile = File(),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
    request_uid: Annotated[str | None, Form(max_length=36)] = None,
    duplicate_action: Annotated[str | None, Form(max_length=7)] = None,
    confirmation_token: Annotated[str | None, Form(max_length=64)] = None,
    replacement_uid: Annotated[str | None, Form(max_length=64)] = None,
    replacement_source: Annotated[str | None, Form(max_length=7)] = None,
    folder_id: Annotated[str, Form(max_length=16)] = "inbox",
) -> JsonResponse:
    try:
        result = service.project_attachment.upload(
            user,
            project_uid,
            attachment,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
            request_uid=request_uid,
            duplicate_action=duplicate_action,
            confirmation_token=confirmation_token,
            replacement_uid=replacement_uid,
            replacement_source=replacement_source,
            folder_id=folder_id,
        )
    except PermissionError:
        raise ApiException.Forbidden_403(ApiErrorCode.PE2002) from None
    except ValueError:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from None
    except RuntimeError:
        raise ApiException.InternalServerError_500(ApiErrorCode.OP1002) from None
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    response_status = (
        status.HTTP_409_CONFLICT
        if result.get("confirmation_required")
        else status.HTTP_200_OK
        if result.get("cancelled") or result.get("replayed") or duplicate_action == "replace"
        else status.HTTP_201_CREATED
    )
    return JsonResponse(
        content=result,
        status_code=response_status,
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization, Cookie, X-Api-Key, X-Api-Token"},
    )
