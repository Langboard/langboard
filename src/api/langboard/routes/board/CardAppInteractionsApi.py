"""Human response acceptance, independent of delivery and runtime continuation."""

from fastapi import Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardInteractions import (
    AppCardInteractionConflict,
    respond_to_app_card_interaction,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class HumanResponseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    execution_generation: int | None = Field(default=None, strict=True, ge=1)
    values: dict


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
