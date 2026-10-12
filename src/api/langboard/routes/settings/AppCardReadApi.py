"""Connection-scoped current app-owned card content."""

from typing import Annotated
from fastapi import Header, Path, Query
from langboard_shared.core.routing import ApiException, AppRouter
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppCardRead import read_app_card
from .AppExecutionLeaseApi import _run


@AppRouter.api.get("/apps/v1/boards/{project_uid}/cards/{card_uid}", tags=["App.Cards"])
def read_card(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=16000)] = 16000,
    authorization: str = Header(default="", max_length=256),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        read_app_card,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        offset=offset,
        limit=limit,
    )
