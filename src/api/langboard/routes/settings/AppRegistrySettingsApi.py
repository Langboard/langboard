"""Instance approval of generic external app declarations and updates."""

from typing import Annotated, Any
from fastapi import Path, Query
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
    model_config = {"extra": "forbid"}
    declaration: dict[str, Any]
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@form_model
class DisableAppDefinitionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _run(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except AppRegistryDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except (AppRegistryConflict, IntegrityError) as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc


@AppRouter.api.get("/settings/apps/registry", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def get_registry(
    after: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$"),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
) -> JsonResponse:
    return JsonResponse(
        content=_run(list_definitions, user, after_key=after, limit=limit), headers={"Cache-Control": "no-store"}
    )


@AppRouter.api.post("/settings/apps/registry", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def approve_definition(form: SaveAppDefinitionForm, user: User = Auth.scope("user")) -> JsonResponse:
    return JsonResponse(
        content=_run(save_definition, user, form.declaration, form.expected_revision),
        headers={"Cache-Control": "no-store"},
    )


@AppRouter.api.post("/settings/apps/registry/{key}/disable", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def disable_app(
    key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    form: DisableAppDefinitionForm,
    user: User = Auth.scope("user"),
) -> JsonResponse:
    result = _run(disable_definition, user, key, form.expected_revision)
    if result is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF3003)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
