"""App Bearer resource discovery, compatible with the independent SDK."""

from typing import Annotated
from fastapi import Header, Path, Query
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppResourceRead import list_app_resources


@AppRouter.api.get("/apps/v1/boards/{project_uid}/resources", tags=["App.Resources"])
def app_resources(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    authorization: str = Header(default="", max_length=256),
    after: str | None = Query(default=None, pattern=r"^[a-zA-Z0-9]{11}$"),
    limit: int = Query(default=25, ge=1, le=50),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    try:
        result = list_app_resources(
            token,
            SnowflakeID.from_short_code(project_uid),
            after_id=SnowflakeID.from_short_code(after) if after else None,
            limit=limit,
        )
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
