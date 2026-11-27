from fastapi import status
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import (
    ApiErrorCode,
    ApiException,
    ApiPermission,
    AppRouter,
    EEditorCollaborationType,
    JsonResponse,
    collaborative_block,
    collaborative_edit,
    collaborative_text,
    create_editor_collaboration_document_id,
)
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import McpRole, McpToolGroup, User
from langboard_shared.domain.models.McpRole import McpRoleAction
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from .Form import CreateMcpToolGroupForm, DeleteSelectedMcpToolGroupsForm, UpdateMcpToolGroupForm


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/settings/mcp/groups/admin",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().suc({"tool_groups": [McpToolGroup]}).auth().forbidden().get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Read], RoleFinder.mcp)
@AuthFilter.add("user")
def list_mcp_tool_groups(
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    tool_groups = service.mcp_tool_group.get_api_list_by_user(user.id)
    return JsonResponse(content={"tool_groups": tool_groups})


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/settings/mcp/groups/global",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().suc({"tool_groups": [McpToolGroup]}).auth().forbidden().get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Read], RoleFinder.mcp)
@AuthFilter.add("user")
def list_global_mcp_tool_groups(service: DomainService = DomainService.scope()) -> JsonResponse:
    tool_groups = service.mcp_tool_group.get_api_global_list()
    return JsonResponse(content={"tool_groups": tool_groups})


@AppRouter.schema(permission=ApiPermission.Create)
@AppRouter.api.post(
    "/settings/mcp/group",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().suc({"tool_group": McpToolGroup}, 201).auth().forbidden().get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Create], RoleFinder.mcp)
@AuthFilter.add("user")
def create_mcp_tool_group(
    form: CreateMcpToolGroupForm, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    tool_group = service.mcp_tool_group.create(
        None if form.is_global else user, form.name, form.description, form.tools, form.activate
    )
    if not tool_group:
        raise ApiException.Conflict_409(ApiErrorCode.EX3003)

    return JsonResponse(content={"tool_group": tool_group.api_response()}, status_code=status.HTTP_201_CREATED)


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/settings/mcp/group/{group_uid}/details",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().suc({"tool_group": McpToolGroup}).auth().forbidden().err(404, ApiErrorCode.NF3006).get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Read], RoleFinder.mcp)
@AuthFilter.add("user")
def get_mcp_tool_group_details(
    group_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    tool_group = service.mcp_tool_group.get_by_id_like(group_uid)
    if not tool_group:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    if tool_group.user_id and tool_group.user_id != user.id:
        raise ApiException.Forbidden_403(ApiErrorCode.AU1001)

    return JsonResponse(content={"tool_group": tool_group.api_response()})


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{group_uid}", "mcp-tool-group"),
        "name",
        "name",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{group_uid}", "mcp-tool-group"),
        "description",
        "description",
    ),
)
@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/settings/mcp/group/{group_uid}",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().suc({"tool_group": McpToolGroup}).auth().forbidden().err(404, ApiErrorCode.NF3006).get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Update], RoleFinder.mcp)
@AuthFilter.add("user")
def update_mcp_tool_group(
    group_uid: str,
    form: UpdateMcpToolGroupForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    tool_group = service.mcp_tool_group.get_by_id_like(group_uid)
    if not tool_group:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    if tool_group.user_id and tool_group.user_id != user.id:
        raise ApiException.Forbidden_403(ApiErrorCode.AU1001)

    form_dict = form.model_dump()

    result = service.mcp_tool_group.update(user, tool_group, form_dict)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    return JsonResponse(content={"tool_group": tool_group.api_response()})


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/settings/mcp/group/{group_uid}/activate",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().auth().forbidden().err(403, ApiErrorCode.AU1001).err(404, ApiErrorCode.NF3006).get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Update], RoleFinder.mcp)
@AuthFilter.add("user")
def activate_mcp_tool_group(
    group_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    tool_group = service.mcp_tool_group.get_by_id_like(group_uid)
    if not tool_group:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    if tool_group.user_id and tool_group.user_id != user.id:
        raise ApiException.Forbidden_403(ApiErrorCode.AU1001)

    success = service.mcp_tool_group.activate(group_uid)
    if not success:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    return JsonResponse({"activated_at": tool_group.activated_at})


@AppRouter.schema(permission=ApiPermission.Edit)
@AppRouter.api.put(
    "/settings/mcp/group/{group_uid}/deactivate",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().auth().forbidden().err(403, ApiErrorCode.AU1001).err(404, ApiErrorCode.NF3006).get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Update], RoleFinder.mcp)
@AuthFilter.add("user")
def deactivate_mcp_tool_group(
    group_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    tool_group = service.mcp_tool_group.get_by_id_like(group_uid)
    if not tool_group:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    if tool_group.user_id and tool_group.user_id != user.id:
        raise ApiException.Forbidden_403(ApiErrorCode.AU1001)

    success = service.mcp_tool_group.deactivate(group_uid)
    if not success:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{group_uid}", "mcp-tool-group")
    )
)
@AppRouter.schema(permission=ApiPermission.Delete)
@AppRouter.api.delete(
    "/settings/mcp/group/{group_uid}",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().auth().forbidden().err(403, ApiErrorCode.AU1001).err(404, ApiErrorCode.NF3006).get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Delete], RoleFinder.mcp)
@AuthFilter.add("user")
def delete_mcp_tool_group(
    group_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    tool_group = service.mcp_tool_group.get_by_id_like(group_uid)
    if not tool_group:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    if tool_group.user_id and tool_group.user_id != user.id:
        raise ApiException.Forbidden_403(ApiErrorCode.AU1001)

    result = service.mcp_tool_group.delete(tool_group)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.AppSettings, "{group_uids}", "mcp-tool-group")
    )
)
@AppRouter.schema(permission=ApiPermission.Delete)
@AppRouter.api.delete(
    "/settings/mcp/groups",
    tags=["AppSettings.MCP"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3006).get(),
)
@RoleFilter.add(McpRole, [McpRoleAction.Delete], RoleFinder.mcp)
@AuthFilter.add("user")
def delete_selected_mcp_tool_groups(
    form: DeleteSelectedMcpToolGroupsForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.mcp_tool_group.delete_selected(user, form.group_uids)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3006)

    return JsonResponse()
