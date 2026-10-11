"""Human response acceptance, independent of delivery and runtime continuation."""

from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardInteractions import (
    AppCardInteractionConflict,
    list_card_interactions,
    respond_to_app_card_interaction,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppInteractionTransitions import apply_interaction_transition
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class HumanResponseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    execution_generation: int | None = Field(default=None, strict=True, ge=1)
    values: dict


class HumanTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    expected_card_revision: int = Field(strict=True, ge=0)


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/apps/interactions/{interaction_uid}/transition", tags=["Board.Card"]
)
@AuthFilter.add("user")
def transition_interaction(
    request: Request,
    project_uid: str,
    card_uid: str,
    interaction_uid: str,
    body: HumanTransitionBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    try:
        result = apply_interaction_transition(
            service,
            user,
            SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid),
            SnowflakeID.from_short_code(interaction_uid),
            SnowflakeID.from_short_code(body.response_uid),
            body.expected_card_revision,
            request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppCardInteractionConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/apps/interactions", tags=["Board.Card"])
@AuthFilter.add("user")
def list_interactions(
    request: Request,
    project_uid: str,
    card_uid: str,
    limit: int = Query(default=25, ge=1, le=25),
    cursor: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    try:
        result = list_card_interactions(
            service,
            user,
            SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid),
            limit=limit,
            before=SnowflakeID.from_short_code(cursor) if cursor else None,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/apps/interactions/{interaction_uid}/responses", tags=["Board.Card"]
)
@AuthFilter.add("user")
def respond_to_interaction(
    request: Request,
    project_uid: str,
    card_uid: str,
    interaction_uid: str,
    body: HumanResponseBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    try:
        result = respond_to_app_card_interaction(
            service,
            user,
            SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid),
            SnowflakeID.from_short_code(interaction_uid),
            body.expected_revision,
            body.idempotency_key,
            body.values,
            body.execution_generation,
            request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppCardInteractionConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None
