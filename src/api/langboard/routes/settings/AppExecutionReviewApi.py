"""App-authenticated explicit Review request; never automatic completion."""
from typing import Annotated
from fastapi import Header, Path
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppExecutionGrant import AppExecutionCredentialDenied
from langboard_shared.domain.services.AppExecutionRequests import AppExecutionRequestConflict
from langboard_shared.domain.services.AppExecutionReviews import read_execution_review, request_execution_review
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from pydantic import BaseModel, ConfigDict, Field


class ExecutionReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(strict=True, ge=1)
    expected_card_revision: int = Field(strict=True, ge=0)
    expected_binding_revision: str = Field(strict=True, min_length=1, max_length=100)
    evidence_reference: str = Field(strict=True, min_length=1, max_length=200)


@AppRouter.api.get("/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/review", tags=["App.Execution"])
def execution_review_read(project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
                          card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
                          request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
                          authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiException.Unauthorized_401()
    try:
        result = read_execution_review(token, SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid), SnowflakeID.from_short_code(request_uid))
    except AppExecutionCredentialDenied as exc:
        raise ApiException.Unauthorized_401() from exc
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except (AppExecutionRequestConflict, AppGovernanceConflict) as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}/review", tags=["App.Execution"])
def execution_review(project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
                     card_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
                     request_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], body: ExecutionReviewBody,
                     authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiException.Unauthorized_401()
    try:
        result = request_execution_review(token, SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(card_uid), SnowflakeID.from_short_code(request_uid),
            body.generation, body.expected_card_revision, body.expected_binding_revision, body.evidence_reference)
    except AppExecutionCredentialDenied as exc:
        raise ApiException.Unauthorized_401() from exc
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except (AppExecutionRequestConflict, AppGovernanceConflict) as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
