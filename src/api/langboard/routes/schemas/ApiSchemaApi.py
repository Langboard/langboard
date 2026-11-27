from fastapi import Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, ApiPermission, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import ApiComfortTool, SettingRole
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.helpers.ApiComfortToolHelper import create_api_comfort_tool_schema
from langboard_shared.security import RoleFinder
from pydantic import BaseModel


class CreateApiComfortToolForm(BaseModel):
    name: str = ""
    label: str
    description: str
    api_names: list[str]


class UpdateApiComfortToolForm(BaseModel):
    name: str
    label: str
    description: str
    api_names: list[str]


@AppRouter.api.get(
    "/schema/api", tags=["Schema"], responses=OpenApiSchema().suc({"apis": {"<name>": "<description>"}}).get()
)
def get_api_list():
    apis: dict[str, str] = {}
    for api_name, api_schema in AppRouter.api_routes.items():
        apis[api_name] = api_schema["description"]

    return JsonResponse(content={"apis": apis})


@AppRouter.api.get(
    "/schema/api/comfort",
    tags=["Schema"],
    responses=(OpenApiSchema().suc({"api_comfort_tools": [ApiComfortTool]}).auth().forbidden().get()),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.ApiComfortToolRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_api_comfort_tool_list_api(service: DomainService = DomainService.scope()):
    return JsonResponse(content={"api_comfort_tools": service.app_setting.get_api_comfort_tool_response_list()})


@AppRouter.api.post(
    "/schema/api/comfort",
    tags=["Schema"],
    responses=(
        OpenApiSchema().suc({"name": "string", "api_comfort_tools": [ApiComfortTool]}, 201).auth().forbidden().get()
    ),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.ApiComfortToolCreate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def create_api_comfort_tool_api(form: CreateApiComfortToolForm, service: DomainService = DomainService.scope()):
    api_names = list(dict.fromkeys(form.api_names))
    if not form.label.strip() or not form.description.strip() or not api_names:
        raise ApiException.BadRequest_400()
    if form.name.strip() and not ApiComfortTool.is_valid_name(form.name.strip()):
        raise ApiException.BadRequest_400()
    if form.name.strip() in AppRouter.api_routes:
        raise ApiException.BadRequest_400()

    if any(api_name not in AppRouter.api_routes for api_name in api_names):
        raise ApiException.BadRequest_400()

    try:
        comfort_tool = service.app_setting.create_api_comfort_tool(
            form.name,
            label=form.label,
            description=form.description,
            api_names=api_names,
        )
    except ValueError:
        raise ApiException.BadRequest_400()

    return JsonResponse(
        content={
            "name": comfort_tool.name,
            "api_comfort_tools": service.app_setting.get_api_comfort_tool_response_list(),
        },
        status_code=201,
    )


@AppRouter.api.put(
    "/schema/api/comfort/{comfort_tool_name}",
    tags=["Schema"],
    responses=OpenApiSchema().suc({"name": "string", "api_comfort_tools": [ApiComfortTool]}).auth().forbidden().get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.ApiComfortToolUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_api_comfort_tool_api(
    comfort_tool_name: str, form: UpdateApiComfortToolForm, service: DomainService = DomainService.scope()
):
    api_names = list(dict.fromkeys(form.api_names))
    if not form.label.strip() or not form.description.strip() or not api_names:
        raise ApiException.BadRequest_400()
    if form.name.strip() and not ApiComfortTool.is_valid_name(form.name.strip()):
        raise ApiException.BadRequest_400()
    if form.name.strip() in AppRouter.api_routes:
        raise ApiException.BadRequest_400()

    if not service.app_setting.get_api_comfort_tool(comfort_tool_name):
        raise ApiException.NotFound_404()

    if any(api_name not in AppRouter.api_routes for api_name in api_names):
        raise ApiException.BadRequest_400()

    try:
        result = service.app_setting.update_api_comfort_tool(
            comfort_tool_name,
            next_name=form.name,
            label=form.label,
            description=form.description,
            api_names=api_names,
        )
    except ValueError:
        raise ApiException.BadRequest_400()

    if not result:
        raise ApiException.NotFound_404()

    updated_comfort_tool_name = result[0]
    api_comfort_tools = service.app_setting.get_api_comfort_tool_response_list()
    return JsonResponse(content={"name": updated_comfort_tool_name, "api_comfort_tools": api_comfort_tools})


@AppRouter.api.delete(
    "/schema/api/comfort/{comfort_tool_name}",
    tags=["Schema"],
    responses=OpenApiSchema().suc({"api_comfort_tools": [ApiComfortTool]}).auth().forbidden().get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.ApiComfortToolDelete], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def delete_api_comfort_tool_api(comfort_tool_name: str, service: DomainService = DomainService.scope()):
    if not service.app_setting.delete_api_comfort_tool(comfort_tool_name):
        raise ApiException.NotFound_404()

    return JsonResponse(content={"api_comfort_tools": service.app_setting.get_api_comfort_tool_response_list()})


@AppRouter.api.get(
    "/schema/api/spec/{api_name}",
    tags=["Schema"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "schema": {
                    "name": "string",
                    "path": "string",
                    "path_params": "array[string]",
                    "method": "string",
                    "permission": "Enum[read, create, edit, delete]",
                    "content_type": "Enum[application/json, multipart/form-data]",
                    "description": "string",
                    "form?": "object",
                    "query?": "object",
                    "file_field?": "string",
                    "request_schema_source?": "string",
                }
            }
        )
        .get()
    ),
)
def get_api_schema(api_name: str, service: DomainService = DomainService.scope()):
    api_schema = AppRouter.api_routes.get(api_name)
    if api_schema:
        return JsonResponse(content={"schema": api_schema})
    comfort_tool_schema = _get_api_comfort_tool_schema(api_name, service)
    if comfort_tool_schema:
        return JsonResponse(content={"schema": comfort_tool_schema})
    raise ApiException.NotFound_404()


@AppRouter.api.get(
    "/schema/api/list",
    tags=["Schema"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "schemas": {
                    "<api_name>": {
                        "name": "string",
                        "path": "string",
                        "path_params": "array[string]",
                        "method": "string",
                        "permission": "Enum[read, create, edit, delete]",
                        "content_type": "Enum[application/json, multipart/form-data]",
                        "description": "string",
                        "form?": "object",
                        "query?": "object",
                        "file_field?": "string",
                        "request_schema_source?": "string",
                    }
                }
            }
        )
        .get()
    ),
)
def get_api_schema_list(
    api_names: str = Query(...), permissions: str | None = Query(None), service: DomainService = DomainService.scope()
):
    schemas = {}
    allowed_permissions: set[str] | None = None
    if permissions:
        allowed_permissions = set()
        for permission in permissions.split(","):
            try:
                allowed_permissions.add(ApiPermission(permission).value)
            except ValueError:
                continue
    api_name_list = _create_api_schema_name_list(api_names.split(","), service, allowed_permissions)

    for api_name in api_name_list:
        schema = AppRouter.api_routes.get(api_name)
        if not schema:
            schema = _get_api_comfort_tool_schema(api_name, service)
        if schema and (allowed_permissions is None or schema["permission"] in allowed_permissions):
            schemas[api_name] = schema

    return JsonResponse(content={"schemas": schemas})


def _create_api_schema_name_list(
    api_name_list: list[str], service: DomainService, allowed_permissions: set[str] | None = None
) -> list[str]:
    requested_api_names = set(api_name_list)
    schema_name_list = list(api_name_list)
    hidden_base_api_names: set[str] = set()

    for comfort_tool_name, comfort_tool in service.app_setting.get_api_comfort_tool_list().items():
        comfort_tool_schema = _get_api_comfort_tool_schema(comfort_tool_name, service)
        if not comfort_tool_schema:
            continue

        is_read_only_tool = comfort_tool_schema["permission"] == ApiPermission.Read.value
        is_allowed = allowed_permissions is None or ApiPermission.Read.value in allowed_permissions
        has_related_base_api = any(api_name in requested_api_names for api_name in comfort_tool["api_names"])

        if comfort_tool_name in requested_api_names:
            hidden_base_api_names.update(comfort_tool["api_names"])
            continue

        if all(api_name in requested_api_names for api_name in comfort_tool["api_names"]):
            schema_name_list.append(comfort_tool_name)
            hidden_base_api_names.update(comfort_tool["api_names"])
            continue

        if is_read_only_tool and is_allowed and has_related_base_api:
            schema_name_list.append(comfort_tool_name)
            hidden_base_api_names.update(comfort_tool["api_names"])

    filtered_schema_name_list: list[str] = []
    for api_name in schema_name_list:
        is_base_api = api_name in AppRouter.api_routes
        if is_base_api and api_name in hidden_base_api_names:
            continue
        filtered_schema_name_list.append(api_name)

    return filtered_schema_name_list


def _get_api_comfort_tool_schema(api_name: str, service: DomainService):
    comfort_tool = service.app_setting.get_api_comfort_tool_list().get(api_name)
    if not comfort_tool:
        return None
    return create_api_comfort_tool_schema(api_name, comfort_tool, AppRouter.api_routes)
