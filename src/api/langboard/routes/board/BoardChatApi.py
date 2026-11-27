from fastapi import Depends, status
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
from langboard_shared.domain.models import (
    ChatHistory,
    ChatSession,
    ChatTemplate,
    Project,
    ProjectChatSession,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.publishers import ProjectPublisher
from langboard_shared.security import Auth, RoleFinder
from .forms import ChatHistoryPagination, CreateChatTemplate, UpdateChatTemplate, UpdateProjectChatSessionForm


@AppRouter.api.get(
    "/board/{project_uid}/chat/sessions",
    tags=["Board.Chat"],
    responses=OpenApiSchema().suc({"sessions": [ChatSession]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_project_chat_sessions(
    project_uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    sessions = service.chat.get_api_session_list(user, ProjectChatSession, project_uid)

    return JsonResponse(content={"sessions": sessions})


@AppRouter.api.get(
    "/board/{project_uid}/chat/session/{session_uid}",
    tags=["Board.Chat"],
    responses=OpenApiSchema().suc({"histories": [ChatHistory]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_project_chat_histories(
    project_uid: str,
    session_uid: str,
    query: ChatHistoryPagination = Depends(),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    result = service.chat.get_session_by_filterable(ProjectChatSession, session_uid, project_uid)
    if not result or result[0].user_id != user.id:
        return JsonResponse(content={"histories": []})
    chat_session, session = result

    histories = service.chat.get_api_history_list(user, chat_session, ProjectChatSession, session, query)

    return JsonResponse(content={"histories": histories})


@AppRouter.api.put(
    "/board/{project_uid}/chat/session/{session_uid}",
    tags=["Board.Chat"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2021).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def update_project_chat_session(
    project_uid: str,
    session_uid: str,
    form: UpdateProjectChatSessionForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    session = service.chat.get_session_by_filterable(ProjectChatSession, session_uid, project_uid)
    if not session or session[0].user_id != user.id:
        raise ApiException.NotFound_404(ApiErrorCode.NF2021)
    chat_session, _ = session

    service.chat.update_session(
        chat_session,
        title=form.title,
        api_permission_level=form.api_permission_level,
    )

    return JsonResponse()


@AppRouter.api.delete(
    "/board/{project_uid}/chat/session/{session_uid}",
    tags=["Board.Chat"],
    responses=OpenApiSchema().auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def delete_project_chat_session(
    project_uid: str,
    session_uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    session = service.chat.get_session_by_filterable(ProjectChatSession, session_uid, project_uid)
    if not session or session[0].user_id != user.id:
        return JsonResponse(content={"histories": []})

    chat_session, _ = session

    service.chat.delete_session(chat_session)

    return JsonResponse()


@AppRouter.api.get(
    "/board/{project_uid}/chat/templates",
    tags=["Board.Chat"],
    responses=OpenApiSchema().suc({"templates": [ChatTemplate]}).auth().forbidden().get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_chat_templates(project_uid: str, service: DomainService = DomainService.scope()) -> JsonResponse:
    templates = service.chat.get_api_template_list(Project.__tablename__, project_uid)

    return JsonResponse(content={"templates": templates})


@AppRouter.api.post(
    "/board/{project_uid}/chat/template",
    tags=["Board.Chat"],
    responses=OpenApiSchema(201).suc({"template": ChatTemplate}).auth().forbidden().err(404, ApiErrorCode.NF2001).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def create_chat_template(
    project_uid: str, form: CreateChatTemplate, service: DomainService = DomainService.scope()
) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2001)

    template = service.chat.create_template(project, form.name, form.template)

    ProjectPublisher.chat_template_created(project, {"template": template.api_response()})

    return JsonResponse(content={"template": template.api_response()}, status_code=status.HTTP_201_CREATED)


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.BoardSettings, "{project_uid}", "chat-template-{template_uid}"
        ),
        "name",
        "name",
    ),
    collaborative_text(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.BoardSettings, "{project_uid}", "chat-template-{template_uid}"
        ),
        "template",
        "template",
    ),
)
@AppRouter.api.put(
    "/board/{project_uid}/chat/template/{template_uid}",
    tags=["Board.Chat"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2018).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def update_chat_template(
    project_uid: str,
    template_uid: str,
    form: UpdateChatTemplate,
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2018)

    result = service.chat.update_template(template_uid, form.name, form.template)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2018)

    if result is True:
        return JsonResponse()

    template, model = result
    ProjectPublisher.chat_template_updated(project, template, model)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.BoardSettings, "{project_uid}", "chat-template-{template_uid}"
        )
    )
)
@AppRouter.api.delete(
    "/board/{project_uid}/chat/template/{template_uid}",
    tags=["Board.Chat"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2018).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Update], RoleFinder.project)
@AuthFilter.add("user")
def delete_chat_template(
    project_uid: str, template_uid: str, service: DomainService = DomainService.scope()
) -> JsonResponse:
    project = service.project.get_by_id_like(project_uid)
    if not project:
        raise ApiException.NotFound_404(ApiErrorCode.NF2018)

    result = service.chat.delete_template(template_uid)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2018)

    ProjectPublisher.chat_template_deleted(project, template_uid)

    return JsonResponse()
