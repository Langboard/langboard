"""App-card request storage, without response acceptance or runtime continuation."""

from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppCardInteractions import (
    cancel_app_card_interaction,
    create_app_card_interaction,
    read_app_card_interaction,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from pydantic import BaseModel, ConfigDict, Field


class InteractionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: dict


def _invoke(operation, authorization, *args):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    try:
        return JsonResponse(content=operation(token, *args), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.post("/apps/v1/boards/{project_uid}/cards/{card_uid}/interactions", tags=["App.Interactions"])
def create_interaction(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: InteractionBody,
    authorization: str = Header(default="", max_length=256),
):
    return _invoke(
        create_app_card_interaction,
        authorization,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        body.request,
    )


@AppRouter.api.get(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/interactions/{interaction_uid}", tags=["App.Interactions"]
)
def read_interaction(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    interaction_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    authorization: str = Header(default="", max_length=256),
):
    return _invoke(
        read_app_card_interaction,
        authorization,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
    )


class CancelInteractionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/interactions/{interaction_uid}/cancel", tags=["App.Interactions"]
)
def cancel_interaction(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    interaction_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: CancelInteractionBody,
    authorization: str = Header(default="", max_length=256),
):
    return _invoke(
        cancel_app_card_interaction,
        authorization,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
        body.expected_revision,
    )
