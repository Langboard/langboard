"""Human-approved board resource selection, independent from provider verification."""

from typing import Annotated
from fastapi import Path, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.AppResourceSelection import list_resource_selections, save_resource_selection
from langboard_shared.security import Auth
from pydantic import Field


@form_model
class ResourceSelectionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    resource_type: str = Field(min_length=1, max_length=64)
    external_resource_id: str = Field(min_length=1, max_length=512)
    selected: bool = Field(strict=True)
    expected_revision: int = Field(strict=True, ge=0)
    expected_binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _run(operation, *args, **kwargs):
    try:
        return JsonResponse(content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc


@AppRouter.api.get(
    "/board/{project_uid}/apps/{app_key}/connections/{connection_uid}/resources", tags=["AppSettings.Resources"]
)
@AuthFilter.add("user")
def list_selections(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    after: str | None = Query(default=None, pattern=r"^[a-zA-Z0-9]{11}$"),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
):
    return _run(
        list_resource_selections,
        user,
        SnowflakeID.from_short_code(project_uid),
        app_key,
        SnowflakeID.from_short_code(connection_uid),
        after_id=SnowflakeID.from_short_code(after) if after else None,
        limit=limit,
    )


@AppRouter.api.put(
    "/board/{project_uid}/apps/{app_key}/connections/{connection_uid}/resources", tags=["AppSettings.Resources"]
)
@AuthFilter.add("user")
def save_selection(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    form: ResourceSelectionForm,
    user: User = Auth.scope("user"),
):
    return _run(
        save_resource_selection,
        user,
        SnowflakeID.from_short_code(project_uid),
        app_key,
        SnowflakeID.from_short_code(connection_uid),
        form.resource_type,
        form.external_resource_id,
        form.selected,
        form.expected_revision,
        form.expected_binding_revision,
        form.expected_app_revision,
    )
