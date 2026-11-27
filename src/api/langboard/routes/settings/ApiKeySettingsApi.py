from typing import cast
from fastapi import Depends, status
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema, PaginatedList
from langboard_shared.domain.models import ApiKeyRole, ApiKeySetting, User
from langboard_shared.domain.models.ApiKeyRole import ApiKeyRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from .Form import ApiKeysPagination, CreateApiKeyForm, DeleteSelectedApiKeysForm, UpdateApiKeyForm


def _validate_ownership(api_key: ApiKeySetting, user: User) -> None:
    """Validate that the user owns the API key or is an admin."""
    if not user.is_admin and api_key.user_id != user.id:
        raise ApiException.Forbidden_403(ApiErrorCode.AU1001)


@AppRouter.api.get(
    "/settings/api-keys",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema().suc(PaginatedList.api_schema((ApiKeySetting,))).auth().forbidden().get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Read], RoleFinder.api_key)
@AuthFilter.add("user")
def list_api_keys(
    user: User = Auth.scope("user"),
    pagination: ApiKeysPagination = Depends(),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.api_key.get_api_list_in_settings(
        user,
        refer_time=pagination.refer_time,
        page=pagination.page,
        limit=pagination.limit,
        only_count=pagination.only_count,
    )
    if pagination.only_count:
        count_new_records = cast(int, result)
        return JsonResponse(content=PaginatedList(count_new_records=count_new_records))

    api_keys, count_new_records = cast(tuple[list[ApiKeySetting], int], result)

    return JsonResponse(
        content=PaginatedList(
            records=[api_key.api_response() for api_key in api_keys],
            count_new_records=count_new_records,
        )
    )


@AppRouter.api.get(
    "/settings/api-keys/{key_uid}",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema().suc({"api_key": ApiKeySetting}).auth().forbidden().err(404, ApiErrorCode.NF3005).get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Read], RoleFinder.api_key)
@AuthFilter.add("user")
def get_api_key(
    key_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    api_key = service.api_key.get_by_id_like(key_uid)
    if not api_key:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    _validate_ownership(api_key, user)

    return JsonResponse(content={"api_key": api_key.api_response()})


@AppRouter.api.post(
    "/settings/api-keys",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema(201).auth().forbidden().err(400, ApiErrorCode.VA0000).get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Create], RoleFinder.api_key)
@AuthFilter.add("user")
def create_api_key(
    form: CreateApiKeyForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    ip_whitelist = []
    if form.ip_whitelist is not None:
        ip_whitelist = form.ip_whitelist.strip().replace(" ", "")
        if ip_whitelist:
            ip_whitelist = ip_whitelist.split(",")
        else:
            ip_whitelist = []

    result = service.api_key.create(user, form.name, ip_whitelist, form.is_active, form.expires_in_days)
    if not result:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    api_key, key_material = result

    return JsonResponse(
        content={"api_key": api_key.api_response(), "key": key_material}, status_code=status.HTTP_201_CREATED
    )


@AppRouter.api.put(
    "/settings/api-keys/{key_uid}",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3005).err(400, ApiErrorCode.VA3006).get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Update], RoleFinder.api_key)
@AuthFilter.add("user")
def update_api_key(
    key_uid: str,
    form: UpdateApiKeyForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    api_key = service.api_key.get_by_id_like(key_uid)
    if not api_key:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    _validate_ownership(api_key, user)

    if api_key.is_expired():
        raise ApiException.BadRequest_400(ApiErrorCode.VA3006)

    form_dict = form.model_dump()

    if "ip_whitelist" in form_dict:
        form_dict.pop("ip_whitelist")

    result = service.api_key.update(api_key, form_dict)

    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    if form.ip_whitelist is not None:
        ip_whitelist = form.ip_whitelist.strip().replace(" ", "")
        ip_whitelist = ip_whitelist.split(",") if ip_whitelist else []
        ip_result = service.api_key.update_ip_whitelist(api_key, ip_whitelist)
        if not ip_result:
            raise ApiException.BadRequest_400(ApiErrorCode.VA3006)

    return JsonResponse(content={"api_key": api_key.api_response()})


@AppRouter.api.put(
    "/settings/api-keys/{key_uid}/activate",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3005).err(400, ApiErrorCode.VA3006).get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Update], RoleFinder.api_key)
@AuthFilter.add("user")
def activate_api_key(
    key_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    api_key = service.api_key.get_by_id_like(key_uid)
    if not api_key:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    _validate_ownership(api_key, user)

    if api_key.is_expired():
        raise ApiException.BadRequest_400(ApiErrorCode.VA3006)

    result = service.api_key.activate(key_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    return JsonResponse()


@AppRouter.api.put(
    "/settings/api-keys/{key_uid}/deactivate",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3005).err(400, ApiErrorCode.VA3006).get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Update], RoleFinder.api_key)
@AuthFilter.add("user")
def deactivate_api_key(
    key_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    api_key = service.api_key.get_by_id_like(key_uid)
    if not api_key:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    _validate_ownership(api_key, user)

    if api_key.is_expired():
        raise ApiException.BadRequest_400(ApiErrorCode.VA3006)

    result = service.api_key.deactivate(key_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    return JsonResponse()


@AppRouter.api.delete(
    "/settings/api-keys/{key_uid}",
    tags=["AppSettings.ApiKey"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3005).get(),
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Delete], RoleFinder.api_key)
@AuthFilter.add("user")
def delete_api_key(
    key_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    api_key = service.api_key.get_by_id_like(key_uid)
    if not api_key:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    _validate_ownership(api_key, user)

    result = service.api_key.delete(key_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3005)

    return JsonResponse()


@AppRouter.api.delete(
    "/settings/api-keys", tags=["AppSettings.ApiKey"], responses=OpenApiSchema().auth().forbidden().get()
)
@RoleFilter.add(ApiKeyRole, [ApiKeyRoleAction.Delete], RoleFinder.api_key)
@AuthFilter.add("user")
def delete_selected_api_keys(
    form: DeleteSelectedApiKeysForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    if not user.is_admin:
        key_uids = []
        for key_uid in form.key_uids:
            api_key = service.api_key.get_by_id_like(key_uid)
            if api_key and api_key.user_id == user.id:
                key_uids.append(key_uid)
        form.key_uids = key_uids

    service.api_key.delete_selected(form.key_uids)
    return JsonResponse()
