"""Native runtime permits and private token recovery; no process dispatch."""

from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppExecutionLeases import (
    authorize_app_runtime,
    check_app_runtime,
    recover_app_runtime,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from pydantic import BaseModel, ConfigDict, Field


class RuntimeTokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_token: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimeAdmissionBody(RuntimeTokenBody):
    acknowledgment_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")


class RuntimeCheckBody(RuntimeTokenBody):
    stopped: bool = Field(default=False, strict=True)


def _run(operation, *args, **kwargs):
    try:
        return JsonResponse(content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/runtime-permits",
    tags=["App.Execution"],
)
def authorize_runtime(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: RuntimeAdmissionBody,
    authorization: str = Header(default="", max_length=256),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        authorize_app_runtime,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
        SnowflakeID.from_short_code(body.acknowledgment_uid),
        body.runtime_token,
    )


@AppRouter.api.post("/apps/v1/runtime-permits/{lease_uid}/check", tags=["App.Execution"])
def check_runtime(lease_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], body: RuntimeCheckBody):
    return _run(check_app_runtime, SnowflakeID.from_short_code(lease_uid), body.runtime_token, stopped=body.stopped)


@AppRouter.api.post("/apps/v1/execution-requests/{request_uid}/runtime-permit-recovery", tags=["App.Execution"])
def recover_runtime(request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], body: RuntimeTokenBody):
    return _run(recover_app_runtime, SnowflakeID.from_short_code(request_uid), body.runtime_token)


class RuntimeStartBody(RuntimeTokenBody):
    lease_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    execution_reference: str = Field(min_length=1, max_length=200)


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/start-reports",
    tags=["App.Execution"],
)
def report_runtime_start(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: RuntimeStartBody,
    authorization: str = Header(default="", max_length=256),
):
    from langboard_shared.domain.services.AppExecutionStarts import report_app_execution_start

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        report_app_execution_start,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
        SnowflakeID.from_short_code(body.lease_uid),
        body.runtime_token,
        body.execution_reference,
    )


@AppRouter.api.post("/apps/v1/runtime-permits/{lease_uid}/report", tags=["App.Execution"])
def report_runtime(lease_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], body: RuntimeTokenBody):
    from langboard_shared.domain.services.AppExecutionStarts import read_app_runtime_report

    return _run(read_app_runtime_report, SnowflakeID.from_short_code(lease_uid), body.runtime_token)


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


class RuntimeReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(strict=True, ge=1)
    expected_card_revision: int = Field(strict=True, ge=0)
    expected_binding_revision: str = Field(min_length=1, max_length=100)
    evidence_reference: str = Field(min_length=1, max_length=200)


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/review", tags=["App.Execution"]
)
def request_review(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    body: RuntimeReviewBody,
    authorization: str = Header(default="", max_length=256),
):
    from langboard_shared.domain.services.AppExecutionReviews import request_execution_review

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        request_execution_review,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
        body.generation,
        body.expected_card_revision,
        body.expected_binding_revision,
        body.evidence_reference,
    )


@AppRouter.api.get(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/review", tags=["App.Execution"]
)
def read_review(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    authorization: str = Header(default="", max_length=256),
):
    from langboard_shared.domain.services.AppExecutionReviews import read_execution_review

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return _run(
        read_execution_review,
        token,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        SnowflakeID.from_short_code(request_uid),
    )
