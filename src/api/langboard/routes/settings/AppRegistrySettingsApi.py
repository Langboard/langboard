"""Instance administrator approval for separately hosted external applications."""

from typing import Any
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppRegistry import (
    AppRegistryConflict,
    AppRegistryDenied,
    disable_definition,
    list_definitions,
    save_definition,
)
from langboard_shared.security import Auth
from pydantic import Field
from sqlalchemy.exc import IntegrityError


@form_model
class SaveAppDefinitionForm(BaseFormModel):
    declaration: dict[str, Any]
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@form_model
class DisableAppDefinitionForm(BaseFormModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _run(operation, *args):
    try:
        return operation(*args)
    except AppRegistryDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except (AppRegistryConflict, IntegrityError) as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc


@form_model
class SaveAppCompositionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    module_keys: list[str] = Field(min_length=1, max_length=16)
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.get("/settings/apps/compositions", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def get_app_compositions(user: User = Auth.scope("user")) -> JsonResponse:
    from langboard_shared.domain.services.AppComposition import composition_catalog
    return JsonResponse(content=_run(composition_catalog, user))


@AppRouter.api.post("/settings/apps/compositions", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def approve_app_composition(form: SaveAppCompositionForm, user: User = Auth.scope("user")) -> JsonResponse:
    from langboard_shared.domain.services.AppComposition import save_composition
    return JsonResponse(content={"composition": _run(
        save_composition, user, form.key, form.name, form.description, form.module_keys, form.expected_revision)})


@AppRouter.api.post("/settings/apps/compositions/{key}/ungroup", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def ungroup_app_composition(key: str, form: DisableAppDefinitionForm, user: User = Auth.scope("user")) -> JsonResponse:
    from langboard_shared.domain.services.AppComposition import ungroup_composition
    result = _run(ungroup_composition, user, key, form.expected_revision)
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)
    return JsonResponse(content=result)


@AppRouter.api.get("/board/{project_uid}/settings/apps/compositions", tags=["Board.Apps"])
@AuthFilter.add("user")
def get_board_app_compositions(project_uid: str, user: User = Auth.scope("user")) -> JsonResponse:
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppComposition import board_compositions
    service = DomainService()
    try:
        return JsonResponse(content=_run(board_compositions, service.workflow_stage, user, project_uid))
    finally:
        service.close()


@form_model
class DisableBoardCompositionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revisions: dict[str, str | None] = Field(max_length=16)


@AppRouter.api.post("/board/{project_uid}/settings/apps/compositions/{key}/disable", tags=["Board.Apps"])
@AuthFilter.add("user")
def disable_board_app_composition(
    project_uid: str, key: str, form: DisableBoardCompositionForm, user: User = Auth.scope("user")
) -> JsonResponse:
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppComposition import disable_board_composition
    service = DomainService()
    try:
        return JsonResponse(content=_run(disable_board_composition, service.workflow_stage, user,
            project_uid, key, form.expected_revision, form.binding_revisions))
    finally:
        service.close()


@AppRouter.api.get("/settings/apps/registry", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def get_app_definitions(user: User = Auth.scope("user")) -> JsonResponse:
    return JsonResponse(content={"apps": _run(list_definitions, user)})


@AppRouter.api.post("/settings/apps/registry", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def approve_app_definition(form: SaveAppDefinitionForm, user: User = Auth.scope("user")) -> JsonResponse:
    return JsonResponse(content={"app": _run(save_definition, user, form.declaration, form.expected_revision)})


@AppRouter.api.post("/settings/apps/registry/{app_key}/disable", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def disable_app_definition(app_key: str, form: DisableAppDefinitionForm, user: User = Auth.scope("user")) -> JsonResponse:
    result = _run(disable_definition, user, app_key, form.expected_revision)
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)
    return JsonResponse(content={"app": result})

@form_model
class BoardAppConsentForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: list[str] = Field(max_length=32)


@AppRouter.api.put("/board/{project_uid}/settings/apps/{app_key}/consent", tags=["Board.Apps"])
@AuthFilter.add("user")
def save_board_app_consent(
    project_uid: str, app_key: str, form: BoardAppConsentForm, user: User = Auth.scope("user")
) -> JsonResponse:
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppRegistry import set_board_consent

    service = DomainService()
    try:
        result = _run(
            set_board_consent,
            service.workflow_stage,
            user,
            project_uid,
            app_key,
            form.app_revision,
            form.binding_uid,
            form.expected_revision,
            form.capabilities,
        )
        return JsonResponse(content=result)
    finally:
        service.close()


@form_model
class PrepareBoardAppConsentForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.post("/board/{project_uid}/settings/apps/{app_key}/consent", tags=["Board.Apps"])
@AuthFilter.add("user")
def prepare_board_app_consent(
    project_uid: str, app_key: str, form: PrepareBoardAppConsentForm, user: User = Auth.scope("user")
) -> JsonResponse:
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.AppRegistry import prepare_board_consent
    service = DomainService()
    try:
        return JsonResponse(content=_run(
            prepare_board_consent, service.workflow_stage, user, project_uid, app_key, form.app_revision))
    finally:
        service.close()
