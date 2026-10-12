"""App-card request storage and reception receipts; human answers use native user routes."""

from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppCardInteractions import (
    acknowledge_interaction_response,
    cancel_app_card_interaction,
    create_app_card_interaction,
    read_app_card_interaction,
    read_interaction_acknowledgment,
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


class InteractionAcknowledgmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    runtime_reference: str = Field(min_length=1, max_length=200)


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/interactions/{interaction_uid}/acknowledgments",
    tags=["App.Interactions"],
)
def acknowledge_interaction(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    interaction_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: InteractionAcknowledgmentBody,
    authorization: str = Header(default="", max_length=256),
):
    return _invoke(
        acknowledge_interaction_response,
        authorization,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
        SnowflakeID.from_short_code(body.event_uid),
        body.runtime_reference,
    )


@AppRouter.api.get(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/interactions/{interaction_uid}/acknowledgments",
    tags=["App.Interactions"],
)
def read_interaction_acknowledgment_route(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    interaction_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    authorization: str = Header(default="", max_length=256),
):
    return _invoke(
        read_interaction_acknowledgment,
        authorization,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
    )
