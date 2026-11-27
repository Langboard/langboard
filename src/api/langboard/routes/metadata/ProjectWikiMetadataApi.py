from fastapi import Depends
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import (
    ApiErrorCode,
    ApiException,
    ApiPermission,
    AppRouter,
    EEditorCollaborationType,
    JsonResponse,
    SocketTopic,
    collaborative_block,
    collaborative_edit,
    collaborative_text,
    create_editor_collaboration_document_id,
)
from langboard_shared.domain.models import Bot, Project, ProjectRole, ProjectWiki, ProjectWikiMetadata, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import MetadataPublisher
from langboard_shared.security import Auth, RoleFinder
from .MetadataForm import MetadataDeleteForm, MetadataForm, MetadataGetModel
from .MetadataHelper import create_metadata_api_schema


@AppRouter.schema(permission=ApiPermission.Read)
@AppRouter.api.get(
    "/metadata/project/{project_uid}/wiki/{wiki_uid}",
    tags=["Metadata"],
    description="Get wiki metadata.",
    responses=(create_metadata_api_schema("list").err(403, ApiErrorCode.PE2005).err(404, ApiErrorCode.NF2008).get()),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_wiki_metadata(
    project_uid: str,
    wiki_uid: str,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    params = InfraHelper.get_records_with_foreign_by_params((Project, project_uid), (ProjectWiki, wiki_uid))
    if not params:
        raise ApiException.NotFound_404(ApiErrorCode.NF2008)
    _, wiki = params

    if isinstance(user_or_bot, User) and not service.project_wiki.is_assigned(user_or_bot, wiki):
        raise ApiException.Forbidden_403(ApiErrorCode.PE2005)

    metadata = service.metadata.get_all_as_api(ProjectWikiMetadata, wiki, as_dict=True)
    return JsonResponse(content={"metadata": metadata})


@AppRouter.schema(query=MetadataGetModel, permission=ApiPermission.Read)
@AppRouter.api.get(
    "/metadata/project/{project_uid}/wiki/{wiki_uid}/key",
    tags=["Metadata"],
    description="Get wiki metadata by key.",
    responses=(create_metadata_api_schema("key").err(403, ApiErrorCode.PE2005).err(404, ApiErrorCode.NF2008).get()),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def get_wiki_metadata_by_key(
    project_uid: str,
    wiki_uid: str,
    get_query: MetadataGetModel = Depends(),
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    params = InfraHelper.get_records_with_foreign_by_params((Project, project_uid), (ProjectWiki, wiki_uid))
    if not params:
        raise ApiException.NotFound_404(ApiErrorCode.NF2008)
    _, wiki = params

    if isinstance(user_or_bot, User) and not service.project_wiki.is_assigned(user_or_bot, wiki):
        raise ApiException.Forbidden_403(ApiErrorCode.PE2005)

    metadata = service.metadata.get_by_key_as_api(ProjectWikiMetadata, wiki, get_query.key)
    value = metadata.get("value", None) if metadata else None
    return JsonResponse(content={get_query.key: value})


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Wiki, "{wiki_uid}", "metadata-{old_key}"),
        "key",
        "key",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(EEditorCollaborationType.Wiki, "{wiki_uid}", "metadata-{old_key}"),
        "value",
        "value",
    ),
)
@AppRouter.schema(form=MetadataForm, permission=ApiPermission.Edit)
@AppRouter.api.post(
    "/metadata/project/{project_uid}/wiki/{wiki_uid}",
    tags=["Metadata"],
    description="Save wiki metadata.",
    responses=(create_metadata_api_schema().err(403, ApiErrorCode.PE2005).err(404, ApiErrorCode.NF2017).get()),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def save_wiki_metadata(
    project_uid: str,
    wiki_uid: str,
    form: MetadataForm,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    params = InfraHelper.get_records_with_foreign_by_params((Project, project_uid), (ProjectWiki, wiki_uid))
    if not params:
        raise ApiException.NotFound_404(ApiErrorCode.NF2017)
    _, wiki = params

    if isinstance(user_or_bot, User) and not service.project_wiki.is_assigned(user_or_bot, wiki):
        raise ApiException.Forbidden_403(ApiErrorCode.PE2005)

    metadata = service.metadata.save(ProjectWikiMetadata, wiki, form.key, form.value, form.old_key)
    if metadata is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2017)

    MetadataPublisher.updated_metadata(SocketTopic.BoardWikiPrivate, wiki_uid, form.key, form.value, form.old_key)
    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(EEditorCollaborationType.Wiki, "{wiki_uid}", "metadata-{keys}")
    )
)
@AppRouter.schema(form=MetadataDeleteForm, permission=ApiPermission.Delete)
@AppRouter.api.delete(
    "/metadata/project/{project_uid}/wiki/{wiki_uid}",
    tags=["Metadata"],
    description="Delete wiki metadata.",
    responses=(create_metadata_api_schema().err(403, ApiErrorCode.PE2005).err(404, ApiErrorCode.NF2008).get()),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add()
def delete_wiki_metadata(
    form: MetadataDeleteForm,
    project_uid: str,
    wiki_uid: str,
    user_or_bot: User | Bot = Auth.scope("all"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    params = InfraHelper.get_records_with_foreign_by_params((Project, project_uid), (ProjectWiki, wiki_uid))
    if not params:
        raise ApiException.NotFound_404(ApiErrorCode.NF2008)
    _, wiki = params

    if isinstance(user_or_bot, User) and not service.project_wiki.is_assigned(user_or_bot, wiki):
        raise ApiException.Forbidden_403(ApiErrorCode.PE2005)

    service.metadata.delete(ProjectWikiMetadata, wiki, form.keys)

    MetadataPublisher.deleted_metadata(SocketTopic.BoardWikiPrivate, wiki_uid, form.keys)
    return JsonResponse()
