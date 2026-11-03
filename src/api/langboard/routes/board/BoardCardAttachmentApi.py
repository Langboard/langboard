from typing import Annotated
from fastapi import File, Form, Request, UploadFile, status
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
from langboard_shared.core.routing.Exception import MissingException
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import CardAttachment, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder, RoleSecurity
from .CardAccess import require_card_child, require_visible_card
from .forms import ChangeAttachmentNameForm, ChangeChildOrderForm
from .forms.Attachment import ProcessAttachmentDocumentForm


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/attachment",
    tags=["Board.Card.Attachment"],
    responses={
        **(
            OpenApiSchema()
            .suc((CardAttachment, {"schema": {"user": User}}), 201)
            .auth()
            .forbidden()
            .err(404, ApiErrorCode.NF2003)
            .err(500, ApiErrorCode.OP1002)
            .get()
        ),
        200: {"description": "Attachment replaced, request replayed, or upload cancelled."},
        409: {
            "description": "Duplicate choice required; response includes current conflicts, request_uid, and confirmation_token."
        },
    },
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def upload_card_attachment(
    project_uid: str,
    card_uid: str,
    request: Request,
    attachment: UploadFile = File(),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
    duplicate_action: Annotated[str | None, Form(max_length=7)] = None,
    confirmation_token: Annotated[str | None, Form(max_length=64)] = None,
    replacement_uid: Annotated[str | None, Form(max_length=64)] = None,
    request_uid: Annotated[str | None, Form(max_length=36)] = None,
) -> JsonResponse:
    require_visible_card(project_uid, card_uid, request, user, service)
    if not attachment:
        raise MissingException("body", "attachment")

    try:
        result = service.card_attachment.upload(
            user,
            project_uid,
            card_uid,
            attachment,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
            duplicate_action=duplicate_action,
            confirmation_token=confirmation_token,
            replacement_uid=replacement_uid,
            request_uid=request_uid,
        )
    except PermissionError:
        raise ApiException.Forbidden_403(ApiErrorCode.PE2002) from None
    except ValueError as error:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000, str(error)) from None
    except RuntimeError:
        raise ApiException.InternalServerError_500(ApiErrorCode.OP1002) from None
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2003)
    response_status = (
        status.HTTP_409_CONFLICT
        if result.get("confirmation_required")
        else status.HTTP_200_OK
        if result.get("cancelled") or result.get("replayed") or duplicate_action == "replace"
        else status.HTTP_201_CREATED
    )
    return JsonResponse(content=result, status_code=response_status)


@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/attachment/{attachment_uid}/order",
    tags=["Board.Card.Attachment"],
    responses=OpenApiSchema().auth().forbidden().err(404, ApiErrorCode.NF2009).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add("user")
def change_attachment_order(
    project_uid: str,
    card_uid: str,
    request: Request,
    attachment_uid: str,
    form: ChangeChildOrderForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    card = require_visible_card(project_uid, card_uid, request, user, service)
    require_card_child(card, CardAttachment, attachment_uid)
    result = service.card_attachment.change_order(project_uid, card_uid, attachment_uid, form.order, user=user)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)

    return JsonResponse()


@collaborative_edit(
    collaborative_text(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.Card, "{card_uid}", "attachment-{attachment_uid}"
        ),
        "attachment_name",
        "name",
    )
)
@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/attachment/{attachment_uid}/name",
    tags=["Board.Card.Attachment"],
    responses=OpenApiSchema().auth().forbidden().err(403, ApiErrorCode.PE2002).err(404, ApiErrorCode.NF2009).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def change_card_attachment_name(
    project_uid: str,
    card_uid: str,
    request: Request,
    attachment_uid: str,
    form: ChangeAttachmentNameForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    card = require_visible_card(project_uid, card_uid, request, user, service)
    require_card_child(card, CardAttachment, attachment_uid)
    card_attachment = service.card_attachment.get_by_id_like(attachment_uid)
    if not card_attachment:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)

    if card_attachment.user_id != user.id and not user.is_admin:
        role_filter = RoleSecurity(ProjectRole)
        if not role_filter.is_authorized(
            user.id, {"project_uid": project_uid}, [ProjectRoleAction.CardUpdate.value], RoleFinder.project
        ):
            raise ApiException.Forbidden_403(ApiErrorCode.PE2002)

    result = service.card_attachment.change_name(user, project_uid, card_uid, card_attachment, form.attachment_name)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)

    return JsonResponse()


@collaborative_edit(
    collaborative_block(
        create_editor_collaboration_document_id(
            EEditorCollaborationType.Card, "{card_uid}", "attachment-{attachment_uid}"
        )
    )
)
@AppRouter.api.delete(
    "/board/{project_uid}/card/{card_uid}/attachment/{attachment_uid}",
    tags=["Board.Card.Attachment"],
    responses=OpenApiSchema().auth().forbidden().err(403, ApiErrorCode.PE2002).err(404, ApiErrorCode.NF2009).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def delete_card_attachment(
    project_uid: str,
    card_uid: str,
    request: Request,
    attachment_uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    card = require_visible_card(project_uid, card_uid, request, user, service)
    require_card_child(card, CardAttachment, attachment_uid)
    card_attachment = service.card_attachment.get_by_id_like(attachment_uid)
    if not card_attachment:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)

    if card_attachment.user_id != user.id and not user.is_admin:
        role_filter = RoleSecurity(ProjectRole)
        if not role_filter.is_authorized(
            user.id, {"project_uid": project_uid}, [ProjectRoleAction.CardUpdate.value], RoleFinder.project
        ):
            raise ApiException.Forbidden_403(ApiErrorCode.PE2002)

    result = service.card_attachment.delete(user, project_uid, card_uid, card_attachment)
    if not result:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)

    return JsonResponse()


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/attachment/{attachment_uid}/document-processing",
    tags=["Board.Card.Attachment"],
    responses=OpenApiSchema().auth().forbidden().err(400, ApiErrorCode.VA0000).err(404, ApiErrorCode.NF2009).get(),
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.CardUpdate], RoleFinder.project)
@AuthFilter.add("user")
def process_card_attachment_document(
    project_uid: str,
    card_uid: str,
    request: Request,
    attachment_uid: str,
    form: ProcessAttachmentDocumentForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    card = require_visible_card(project_uid, card_uid, request, user, service)
    require_card_child(card, CardAttachment, attachment_uid)
    try:
        if form.mode == "keyword":
            result = service.card_attachment.request_document_keyword_index(
                project_uid, card_uid, attachment_uid, user=user
            )
        elif form.mode == "embedding":
            result = service.card_attachment.request_document_embedding(
                project_uid, card_uid, attachment_uid, user=user
            )
        else:
            result = service.card_attachment.request_document_processing(
                project_uid, card_uid, attachment_uid, user=user, reprocess=form.reprocess
            )
    except ValueError as error:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from error
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)
    return JsonResponse(content={"status": result})
