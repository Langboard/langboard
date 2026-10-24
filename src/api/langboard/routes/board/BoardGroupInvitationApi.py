"""Explicit additive invitations from the permission-gated canonical directory."""

from typing import Annotated
from fastapi import Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.GroupDirectoryService import (
    GroupDirectoryConflict,
    GroupDirectoryDenied,
    GroupDirectoryMissing,
)
from langboard_shared.security import Auth
from sqlalchemy.exc import OperationalError
from .forms.GroupInvitation import GroupInvitationForm


def _result(operation):
    try:
        return JsonResponse(content=operation(), headers={"Cache-Control": "no-store"})
    except GroupDirectoryDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except GroupDirectoryMissing as exc:
        raise ApiException.NotFound_404() from exc
    except GroupDirectoryConflict as exc:
        raise ApiException.Conflict_409() from exc
    except OperationalError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        sqlite_busy = getattr(exc.orig, "sqlite_errorcode", None) in (5, 6)
        if code in ("55P03", "57014", "40P01") or sqlite_busy:
            raise ApiException.Conflict_409() from exc
        raise
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.get("/board/{project_uid}/members/group-preview", tags=["Board"])
@AuthFilter.add("user")
def preview_group_invitation(
    project_uid: str,
    group_uid: Annotated[str, Query(pattern=r"^[0-9A-Za-z]{11}$")],
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(lambda: service.project.group_invitation(user.id, project_uid, group_uid))


@AppRouter.api.post("/board/{project_uid}/members/group-add", tags=["Board"])
@AuthFilter.add("user")
def apply_group_invitation(
    project_uid: str,
    form: GroupInvitationForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    if not form.confirmed:
        raise ApiException.BadRequest_400()
    return _result(
        lambda: service.project.group_invitation(
            user.id,
            project_uid,
            form.group_uid,
            snapshot=form.snapshot,
            request_id=str(form.request_id),
            confirmed_count=form.confirmed_count,
        )
    )
