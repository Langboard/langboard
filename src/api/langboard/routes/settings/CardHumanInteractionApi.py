"""Authenticated human interaction responses; app tokens cannot approve."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppCardInteractions import (
    read_human_card_interaction,
    respond_to_app_card_interaction,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.security import Auth
from pydantic import Field


@form_model
class InteractionResponseForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,100}$")
    execution_generation: int | None = Field(default=None, strict=True, ge=1)
    values: dict = Field(max_length=20)


def _invoke(operation, *args):
    try:
        return JsonResponse(content=operation(*args), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/interactions/{interaction_uid}", tags=["App.Interactions"])
@AuthFilter.add("user")
def read_interaction(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    interaction_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    user: User = Auth.scope("user"),
):
    return _invoke(
        read_human_card_interaction,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
    )


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/interactions/{interaction_uid}/responses", tags=["App.Interactions"]
)
@AuthFilter.add("user")
def respond_interaction(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    interaction_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: InteractionResponseForm,
    user: User = Auth.scope("user"),
):
    return _invoke(
        respond_to_app_card_interaction,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
        form.values,
        form.expected_revision,
        form.idempotency_key,
        form.execution_generation,
    )
