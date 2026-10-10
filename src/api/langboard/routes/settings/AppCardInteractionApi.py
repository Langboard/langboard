"""App-card request storage, without response acceptance or runtime continuation."""

from fastapi import Header
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppCardInteractions import (
    AppCardInteractionConflict,
    create_app_card_interaction,
    read_app_card_interaction,
)
from langboard_shared.domain.services.AppExecutionGrant import AppExecutionCredentialDenied
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from pydantic import BaseModel, ConfigDict


class InteractionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: dict


def _invoke(operation, authorization, *args):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise ApiException.Unauthorized_401()
    try:
        return JsonResponse(content=operation(token, *args), headers={"Cache-Control": "no-store"})
    except AppExecutionCredentialDenied as exc:
        raise ApiException.Unauthorized_401() from exc
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppCardInteractionConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.post("/apps/v1/boards/{project_uid}/cards/{card_uid}/interactions", tags=["App.Interactions"])
def create_interaction(
    project_uid: str, card_uid: str, body: InteractionBody, authorization: str = Header(default="", max_length=256)
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
    project_uid: str, card_uid: str, interaction_uid: str, authorization: str = Header(default="", max_length=256)
):
    return _invoke(
        read_app_card_interaction,
        authorization,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(interaction_uid),
    )
