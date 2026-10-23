"""Installation-wide group directory; membership never implies a read grant."""

from typing import Annotated
from fastapi import Path, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.domain.models import SettingRole, User
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.GroupDirectoryService import (
    GroupDirectoryConflict,
    GroupDirectoryDenied,
    GroupDirectoryMissing,
)
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from pydantic import BaseModel, ConfigDict, Field, field_validator


@AppRouter.api.get("/settings/groups", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_group_directory(
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    query: Annotated[str, Query(max_length=200)] = "",
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return JsonResponse(
        content=service.group_directory.list_groups(actor_id=user.id, page=page, limit=limit, query=query)
    )


@AppRouter.api.get("/settings/groups/{group_uid}/members", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_group_members(
    group_uid: Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")],
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    query: Annotated[str, Query(max_length=200)] = "",
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.group_directory.list_members(group_uid, actor_id=user.id, page=page, limit=limit, query=query)
    if result is None:
        raise ApiException.NotFound_404()
    return JsonResponse(content=result)


UID = Annotated[str, Field(strict=True, pattern=r"^[0-9A-Za-z]{11}$")]
GroupUID = Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")]


class GroupCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(strict=True, min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Group name must not be blank")
        return value.strip()


class GroupRevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)


class GroupRenameBody(GroupCreateBody, GroupRevisionBody):
    pass


class GroupMemberBody(GroupRevisionBody):
    user_uids: list[UID] = Field(min_length=1, max_length=100)


def _result(operation, **arguments):
    try:
        return JsonResponse(content=operation(**arguments), headers={"Cache-Control": "no-store"})
    except GroupDirectoryDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except GroupDirectoryMissing as exc:
        raise ApiException.NotFound_404() from exc
    except GroupDirectoryConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.post("/settings/groups", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupManage], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def create_group(
    body: GroupCreateBody, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
):
    return _result(service.group_directory.mutate, actor_id=user.id, action="create", **body.model_dump())


@AppRouter.api.put("/settings/groups/{group_uid}", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupManage], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def rename_group(
    group_uid: GroupUID,
    body: GroupRenameBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        service.group_directory.mutate, actor_id=user.id, action="rename", group_uid=group_uid, **body.model_dump()
    )


@AppRouter.api.delete("/settings/groups/{group_uid}", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupManage], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def delete_group(
    group_uid: GroupUID,
    body: GroupRevisionBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        service.group_directory.mutate, actor_id=user.id, action="delete", group_uid=group_uid, **body.model_dump()
    )


@AppRouter.api.post("/settings/groups/{group_uid}/members", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupManage], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def add_group_members(
    group_uid: GroupUID,
    body: GroupMemberBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        service.group_directory.mutate, actor_id=user.id, action="add_members", group_uid=group_uid, **body.model_dump()
    )


@AppRouter.api.delete("/settings/groups/{group_uid}/members", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupManage], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def remove_group_members(
    group_uid: GroupUID,
    body: GroupMemberBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        service.group_directory.mutate,
        actor_id=user.id,
        action="remove_members",
        group_uid=group_uid,
        **body.model_dump(),
    )


@AppRouter.api.get("/settings/groups/candidates", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupManage], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def group_candidates(
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    query: Annotated[str, Query(max_length=200)] = "",
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(service.group_directory.candidates, actor_id=user.id, page=page, limit=limit, query=query)


@AppRouter.api.get("/settings/groups/{group_uid}/history", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def group_history(
    group_uid: GroupUID,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(service.group_directory.history, actor_id=user.id, group_uid=group_uid, page=page, limit=limit)
