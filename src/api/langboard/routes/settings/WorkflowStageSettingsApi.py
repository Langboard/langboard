from typing import Annotated
from fastapi import Path, Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import SettingRole, User
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.WorkflowStageService import WorkflowStageEditConflict
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from sqlalchemy.exc import IntegrityError
from .Form import DeactivateWorkflowStageForm, SaveWorkflowStageForm


@AppRouter.api.get("/settings/workflow-stages", tags=["AppSettings.WorkflowStage"])
@RoleFilter.add(SettingRole, [SettingRoleAction.WorkflowStageRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_workflow_stages(service: DomainService = DomainService.scope()) -> JsonResponse:
    return JsonResponse(content={"stages": service.workflow_stage.get_api_list()})


@AppRouter.api.get("/settings/workflow-stages/bot-options", tags=["AppSettings.WorkflowStage"])
@RoleFilter.add(SettingRole, [SettingRoleAction.WorkflowStageRead, SettingRoleAction.BotRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_workflow_bot_options(
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return JsonResponse(content=service.workflow_stage.get_bot_options(user), headers={"Cache-Control": "no-store"})


@AppRouter.api.get("/settings/workflow-stages/{stage_uid}/history", tags=["AppSettings.WorkflowStage"])
@RoleFilter.add(SettingRole, [SettingRoleAction.WorkflowStageRead], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def get_workflow_effect_history(
    request: Request,
    stage_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    before: Annotated[str | None, Query(pattern=r"^[a-zA-Z0-9]{11}$")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        result = service.workflow_stage.get_effect_history(user, stage_uid, before=before, limit=limit,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api))
    except (TypeError, ValueError) as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


def _save(form: SaveWorkflowStageForm, service: DomainService, uid: str | None = None, *, actor: User | None = None) -> JsonResponse:
    if uid and form.expected_revision is None:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    try:
        stage = service.workflow_stage.save(form.model_dump(), uid, actor=actor)
    except WorkflowStageEditConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    except (ValueError, IntegrityError) as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    if not stage:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)
    return JsonResponse(content={"stage": stage.api_response()}, status_code=200 if uid else 201)


@AppRouter.api.post("/settings/workflow-stages", tags=["AppSettings.WorkflowStage"])
@RoleFilter.add(SettingRole, [SettingRoleAction.WorkflowStageCreate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def create_workflow_stage(form: SaveWorkflowStageForm, service: DomainService = DomainService.scope(), user: User = Auth.scope("user")) -> JsonResponse:
    return _save(form, service, actor=user)


@AppRouter.api.put("/settings/workflow-stages/{stage_uid}", tags=["AppSettings.WorkflowStage"])
@RoleFilter.add(SettingRole, [SettingRoleAction.WorkflowStageUpdate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def update_workflow_stage(
    stage_uid: str, form: SaveWorkflowStageForm, service: DomainService = DomainService.scope(), user: User = Auth.scope("user")
) -> JsonResponse:
    return _save(form, service, stage_uid, actor=user)


@AppRouter.api.post("/settings/workflow-stages/{stage_uid}/deactivate", tags=["AppSettings.WorkflowStage"])
@RoleFilter.add(SettingRole, [SettingRoleAction.WorkflowStageDeactivate], RoleFinder.setting, allowed_all_admin=False)
@AuthFilter.add("admin")
def deactivate_workflow_stage(
    stage_uid: str, form: DeactivateWorkflowStageForm, service: DomainService = DomainService.scope()
) -> JsonResponse:
    try:
        stage = service.workflow_stage.deactivate(stage_uid, form.expected_revision)
    except WorkflowStageEditConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    if not stage:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)
    return JsonResponse(content={"stage": stage.api_response()})
