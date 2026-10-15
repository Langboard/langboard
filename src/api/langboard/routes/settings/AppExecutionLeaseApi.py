"""Native runtime permits and private token recovery; no process dispatch."""

from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from pydantic import BaseModel, ConfigDict, Field


class RuntimeTokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_token: str = Field(pattern=r"^[0-9a-f]{64}$")


def _run(operation, *args, **kwargs):
    try:
        return JsonResponse(content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


class RuntimeActiveBody(RuntimeTokenBody):
    lease_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    expected_card_revision: int = Field(strict=True, ge=0)
    expected_binding_revision: str = Field(min_length=1, max_length=100)


class RuntimeActiveReadBody(RuntimeTokenBody):
    lease_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/active", tags=["App.Execution"]
)
def activate_runtime(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: RuntimeActiveBody,
    authorization: str = Header(default="", max_length=256),
):
    from langboard_shared.domain.services.AppExecutionActive import activate_app_runtime

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        activate_app_runtime,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
        SnowflakeID.from_short_code(body.lease_uid),
        body.runtime_token,
        body.expected_card_revision,
        body.expected_binding_revision,
    )


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/active-receipt",
    tags=["App.Execution"],
)
def read_active_runtime(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: RuntimeActiveReadBody,
    authorization: str = Header(default="", max_length=256),
):
    from langboard_shared.domain.services.AppExecutionActive import read_app_active_handoff

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        read_app_active_handoff,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
        SnowflakeID.from_short_code(body.lease_uid),
        body.runtime_token,
    )
