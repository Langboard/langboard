"""Board avatars require current read and update authority at every boundary."""

from fastapi import File, Form, UploadFile
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.AvatarService import AvatarService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from ..account.AvatarApi import AvatarPreviewBody, AvatarRestoreBody, avatar_preview_response, avatar_response


@AppRouter.api.get("/board/{project_uid}/avatar", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def get_board_avatar(project_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    return avatar_response(lambda: AvatarService(service).get(user, project_uid))


@AppRouter.api.put("/board/{project_uid}/avatar", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def apply_board_avatar(
    project_uid: str,
    expected_revision: int = Form(..., ge=0),
    avatar: UploadFile = File(...),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return avatar_response(lambda: AvatarService(service).mutate(user, expected_revision, project_uid, upload=avatar))


@AppRouter.api.post("/board/{project_uid}/avatar/restore", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def restore_board_avatar(
    project_uid: str,
    body: AvatarRestoreBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return avatar_response(
        lambda: AvatarService(service).mutate(user, body.expected_revision, project_uid, restore=True)
    )


@AppRouter.api.get("/board/{project_uid}/avatar/image-profiles", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def board_avatar_profiles(
    project_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return avatar_response(lambda: AvatarService(service).profiles(user, project_uid))


@AppRouter.api.post("/board/{project_uid}/avatar/context", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def board_avatar_context(
    project_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return avatar_response(lambda: AvatarService(service).context(user, project_uid))


@AppRouter.api.post("/board/{project_uid}/avatar/preview", tags=["Board"])
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read, ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
async def preview_board_avatar(
    project_uid: str,
    body: AvatarPreviewBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return await avatar_preview_response(service, user, body, project_uid)
