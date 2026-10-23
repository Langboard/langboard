"""Installation-wide group directory; membership never implies a read grant."""

from typing import Annotated
from fastapi import Path, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.domain.models import SettingRole
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder


@AppRouter.api.get("/settings/groups", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_group_directory(
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    query: Annotated[str, Query(max_length=200)] = "",
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return JsonResponse(content=service.group_directory.list_groups(page=page, limit=limit, query=query))


@AppRouter.api.get("/settings/groups/{group_uid}/members", tags=["AppSettings.Group"])
@RoleFilter.add(SettingRole, [SettingRoleAction.GroupRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_group_members(
    group_uid: Annotated[str, Path(pattern=r"^[0-9A-Za-z]{11}$")],
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    query: Annotated[str, Query(max_length=200)] = "",
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.group_directory.list_members(group_uid, page=page, limit=limit, query=query)
    if result is None:
        raise ApiException.NotFound_404()
    return JsonResponse(content=result)
