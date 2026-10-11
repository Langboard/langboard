"""Explicit human execution intent; no provider dispatch or runtime authorization."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.CardExecutionIntent import (
    read_execution_intent_context,
    read_execution_intent_receipt,
    request_card_execution_intent,
)
from langboard_shared.security import Auth
from pydantic import Field


@form_model
class ExecutionIntentForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,100}$")
    expected_generation: int = Field(strict=True, ge=0)
    expected_snapshot: dict = Field(max_length=9)


def _run(operation, *args):
    try:
        return JsonResponse(content=operation(*args), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/connections/{connection_uid}/execution-intent",
    tags=["AppSettings.ExecutionIntent"],
)
@AuthFilter.add("user")
def read_intent(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    user: User = Auth.scope("user"),
):
    return _run(
        read_execution_intent_context,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        app_key,
        SnowflakeID.from_short_code(connection_uid),
    )


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/connections/{connection_uid}/execution-intent",
    tags=["AppSettings.ExecutionIntent"],
)
@AuthFilter.add("user")
def request_intent(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: ExecutionIntentForm,
    user: User = Auth.scope("user"),
):
    return _run(
        request_card_execution_intent,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        app_key,
        SnowflakeID.from_short_code(connection_uid),
        form.idempotency_key,
        form.expected_generation,
        form.expected_snapshot,
    )


@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/connections/{connection_uid}/execution-intent/{request_uid}",
    tags=["AppSettings.ExecutionIntent"],
)
@AuthFilter.add("user")
def read_receipt(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    user: User = Auth.scope("user"),
):
    return _run(
        read_execution_intent_receipt,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        app_key,
        SnowflakeID.from_short_code(connection_uid),
        SnowflakeID.from_short_code(request_uid),
    )
