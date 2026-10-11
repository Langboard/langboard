"""Native app request receipt compatible with AppExecution.read_request."""

from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppExecutionAcknowledgment import acknowledge_execution_request
from langboard_shared.domain.services.AppExecutionRequestRead import read_app_execution_request
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from pydantic import Field


@form_model
class ExecutionAcknowledgmentForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    event_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    runtime_reference: str = Field(min_length=1, max_length=200)


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/acknowledgments",
    tags=["App.Execution"],
)
def app_execution_acknowledgment(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: ExecutionAcknowledgmentForm,
    authorization: str = Header(default="", max_length=256),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    try:
        result = acknowledge_execution_request(
            token,
            SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid),
            SnowflakeID.from_short_code(request_uid),
            SnowflakeID.from_short_code(form.event_uid),
            form.runtime_reference,
        )
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except AppGovernanceConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.get(
    "/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}",
    tags=["App.Execution"],
)
def app_execution_request(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    authorization: str = Header(default="", max_length=256),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    try:
        result = read_app_execution_request(
            token,
            SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid),
            SnowflakeID.from_short_code(request_uid),
        )
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
