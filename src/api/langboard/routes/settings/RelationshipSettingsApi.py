from fastapi import status
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import (
    ApiErrorCode,
    ApiException,
    AppRouter,
    EEditorCollaborationType,
    JsonResponse,
    collaborative_block,
    collaborative_edit,
    collaborative_text,
    create_editor_collaboration_document_id,
)
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.domain.models import GlobalCardRelationshipType, SettingRole
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import RoleFinder
from .Form import (
    CreateGlobalRelationshipTypeForm,
    DeleteSelectedGlobalRelationshipTypesForm,
    ImportGlobalRelationshipTypesForm,
    UpdateGlobalRelationshipTypeForm,
)


@AppRouter.api.get(
    "/settings/global-relationships",
    tags=["AppSettings.Bot"],
    responses=OpenApiSchema().suc({"global_relationships": [GlobalCardRelationshipType]}).auth().forbidden().get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.GlobalRelationshipRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_global_relationships_in_settings(service: DomainService = DomainService.scope()) -> JsonResponse:
    global_relationships = service.app_setting.get_api_global_relationship_list()

    return JsonResponse(content={"global_relationships": global_relationships})


@AppRouter.api.post(
    "/settings/global-relationship",
    tags=["AppSettings.GlobalRelationship"],
    responses=OpenApiSchema().suc({"global_relationship": GlobalCardRelationshipType}, 201).auth().forbidden().get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.GlobalRelationshipCreate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def create_global_relationship(
    form: CreateGlobalRelationshipTypeForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    global_relationship = service.app_setting.create_global_relationship(
        form.parent_name, form.child_name, form.description
    )

    return JsonResponse(
        content={"global_relationship": global_relationship.api_response()}, status_code=status.HTTP_201_CREATED
    )


@AppRouter.api.post(
    "/settings/import-global-relationships",
    tags=["AppSettings.GlobalRelationship"],
    responses=(
        OpenApiSchema().suc({"global_relationships": [GlobalCardRelationshipType]}, 201).auth().forbidden().get()
    ),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.GlobalRelationshipCreate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def import_global_relationships(
    form: ImportGlobalRelationshipTypesForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    global_relationships = service.app_setting.import_global_relationship(
        [(rel.parent_name, rel.child_name, rel.description) for rel in form.relationships]
    )

    return JsonResponse(
        content={"global_relationships": [gr.api_response() for gr in global_relationships]},
        status_code=status.HTTP_201_CREATED,
    )


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{global_relationship_uid}", "global-relationship"
        ),
        "parent_name",
        "parent_name",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{global_relationship_uid}", "global-relationship"
        ),
        "child_name",
        "child_name",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{global_relationship_uid}", "global-relationship"
        ),
        "description",
        "description",
    ),
)
@AppRouter.api.put(
    "/settings/global-relationship/{global_relationship_uid}",
    tags=["AppSettings.GlobalRelationship"],
    responses=(
        OpenApiSchema()
        .suc(
            {
                "parent_name?": "string",
                "child_name?": "string",
                "description?": "string",
            }
        )
        .auth()
        .forbidden()
        .err(404, ApiErrorCode.NF3003)
        .get()
    ),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.GlobalRelationshipUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_global_relationship(
    global_relationship_uid: str, form: UpdateGlobalRelationshipTypeForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    form_dict = form.model_dump()

    result = service.app_setting.update_global_relationship(global_relationship_uid, form_dict)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)

    if isinstance(result, bool):
        return JsonResponse()

    _, model = result

    return JsonResponse(content={**model})


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{global_relationship_uid}", "global-relationship"
        )
    )
)
@AppRouter.api.delete(
    "/settings/global-relationship/{global_relationship_uid}",
    tags=["AppSettings.GlobalRelationship"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3003).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.GlobalRelationshipDelete], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def delete_global_relationship(
    global_relationship_uid: str, service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.app_setting.delete_global_relationship(global_relationship_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.AppSettings, "{relationship_type_uids}", "global-relationship"
        )
    )
)
@AppRouter.api.delete(
    "/settings/global-relationship",
    tags=["AppSettings.GlobalRelationship"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF3003).get(),
)
@RoleFilter.add(SettingRole, [SettingRoleAction.GlobalRelationshipDelete], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def delete_selected_global_relationship(
    form: DeleteSelectedGlobalRelationshipTypesForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    result = service.app_setting.delete_selected_global_relationships(form.relationship_type_uids)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)

    return JsonResponse()
