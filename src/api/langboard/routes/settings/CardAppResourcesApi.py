"""Human-selected card resources, isolated from app execution authority."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.CardAppGovernance import CardAppOwnershipConflict
from langboard_shared.domain.services.CardAppResources import read_card_app_resources, set_card_app_resources
from langboard_shared.security import Auth
from pydantic import Field


@form_model
class CardAppResourcesForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    resource_uids: list[str] = Field(max_length=20)
    expected_revision: int | None = Field(strict=True, ge=1)
    expected_ownership_revision: int | None = Field(default=None, strict=True, ge=1)
    expected_identity_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_app_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_binding_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _run(operation, *args, **kwargs):
    try:
        return JsonResponse(content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except (AppGovernanceConflict, CardAppOwnershipConflict) as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/connections/{connection_uid}/resources",
    tags=["AppSettings.CardResources"],
)
@AuthFilter.add("user")
def read_resources(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    user: User = Auth.scope("user"),
):
    return _run(
        read_card_app_resources,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(connection_uid),
        app_key=app_key,
    )


@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/connections/{connection_uid}/resources",
    tags=["AppSettings.CardResources"],
)
@AuthFilter.add("user")
def save_resources(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: CardAppResourcesForm,
    user: User = Auth.scope("user"),
):
    return _run(
        set_card_app_resources,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(connection_uid),
        form.resource_uids,
        form.expected_revision,
        app_key=app_key,
        expected_ownership_revision=form.expected_ownership_revision,
        expected_identity_revision=form.expected_identity_revision,
        expected_app_revision=form.expected_app_revision,
        expected_binding_revision=form.expected_binding_revision,
    )
