"""Human board approval; app identities cannot approve their own grants."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppBoardConsent import prepare_board_consent, save_board_consent
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.security import Auth
from pydantic import Field


@form_model
class BoardConsentForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    capabilities: list[str] = Field(max_length=32)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _run(operation, *args):
    try:
        return JsonResponse(content=operation(*args), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/consent/prepare", tags=["AppSettings.BoardConsent"])
@AuthFilter.add("user")
def prepare_consent(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    user: User = Auth.scope("user"),
):
    return _run(prepare_board_consent, user, SnowflakeID.from_short_code(project_uid), app_key)


@AppRouter.api.put("/board/{project_uid}/apps/{app_key}/consent", tags=["AppSettings.BoardConsent"])
@AuthFilter.add("user")
def save_consent(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    form: BoardConsentForm,
    user: User = Auth.scope("user"),
):
    return _run(
        save_board_consent,
        user,
        SnowflakeID.from_short_code(project_uid),
        app_key,
        form.capabilities,
        form.expected_revision,
        form.expected_app_revision,
    )
