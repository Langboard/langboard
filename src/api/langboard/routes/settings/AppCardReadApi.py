"""App-authenticated current content under explicit cards.read consent."""

from typing import Annotated
from fastapi import Header, Path, Query
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppCardRead import read_app_card
from langboard_shared.domain.services.AppExecutionGrant import AppExecutionCredentialDenied
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied


@AppRouter.api.get("/apps/v1/boards/{project_uid}/cards/{card_uid}", tags=["App.Cards"])
def read_owned_card(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    offset: int = Query(default=0, ge=0), limit: int = Query(default=16000, ge=1, le=16000),
    authorization: str = Header(default="", max_length=256),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    try:
        result = read_app_card(token, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(card_uid),
                               offset=offset, limit=limit)
    except AppExecutionCredentialDenied as exc:
        raise ApiException.Unauthorized_401() from exc
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
