"""App-owned archive outcome reports remain distinct from webhook delivery."""

from fastapi import Header
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppCardArchive import read_archive_outcome, report_archive_outcome
from langboard_shared.domain.services.AppExecutionGrant import AppExecutionCredentialDenied
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from pydantic import BaseModel, ConfigDict, Field


class ArchiveOutcomeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=0)
    outcome: dict


@AppRouter.api.get("/apps/v1/boards/{project_uid}/archives/{archive_uid}", tags=["App.Cards"])
def archive_receipt(project_uid: str, archive_uid: str, authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise ApiException.Unauthorized_401()
    try:
        result = read_archive_outcome(token, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(archive_uid))
    except AppExecutionCredentialDenied:
        raise ApiException.Unauthorized_401() from None
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppGovernanceConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/apps/v1/boards/{project_uid}/archives/{archive_uid}/outcome", tags=["App.Cards"])
def archive_outcome(project_uid: str, archive_uid: str, body: ArchiveOutcomeBody,
                    authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise ApiException.Unauthorized_401()
    try:
        result = report_archive_outcome(token, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(archive_uid),
                                        body.expected_revision, body.outcome)
    except AppExecutionCredentialDenied:
        raise ApiException.Unauthorized_401() from None
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppGovernanceConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
