"""Authenticated app receipt and source reads; credentials never become native sessions."""

from fastapi import Header, Query
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppExecutionGrant import (
    AppExecutionCredentialDenied,
)
from langboard_shared.domain.services.AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
)
from langboard_shared.domain.services.AppTransfers import (
    read_app_transfer,
    read_transfer_attachment,
    report_transfer,
)
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse


def _attachment_chunks(file):
    try:
        while chunk := file.read(64 * 1024):
            yield chunk
    finally:
        file.close()


class TransferReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=0)
    acknowledgment: str
    provider_outcome: str | None = None


def _call(authorization, operation, project_uid, transfer_uid, *args, **kwargs):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise ApiException.Unauthorized_401()
    try:
        result = operation(
            token,
            SnowflakeID.from_short_code(project_uid),
            SnowflakeID.from_short_code(transfer_uid),
            *args,
            **kwargs,
        )
    except AppExecutionCredentialDenied:
        raise ApiException.Unauthorized_401() from None
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppGovernanceConflict:
        raise ApiException.Conflict_409() from None
    except (TypeError, ValueError):
        raise ApiException.BadRequest_400() from None
    if isinstance(result, tuple):
        file, digest = result
        try:
            return StreamingResponse(
                _attachment_chunks(file),
                media_type="application/octet-stream",
                background=BackgroundTask(file.close),
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-SHA256": digest,
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except BaseException:
            file.close()
            raise
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.get(
    "/apps/v1/boards/{project_uid}/transfers/{transfer_uid}", tags=["App.Transfers"]
)
def app_transfer(
    project_uid: str,
    transfer_uid: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=16000, ge=1, le=16000),
    authorization: str = Header(default="", max_length=256),
):
    return _call(
        authorization,
        read_app_transfer,
        project_uid,
        transfer_uid,
        offset=offset,
        limit=limit,
    )


@AppRouter.api.get(
    "/apps/v1/boards/{project_uid}/transfers/{transfer_uid}/attachment",
    tags=["App.Transfers"],
)
def app_transfer_attachment(
    project_uid: str,
    transfer_uid: str,
    authorization: str = Header(default="", max_length=256),
):
    return _call(authorization, read_transfer_attachment, project_uid, transfer_uid)


@AppRouter.api.post(
    "/apps/v1/boards/{project_uid}/transfers/{transfer_uid}/receipt",
    tags=["App.Transfers"],
)
def app_transfer_receipt(
    project_uid: str,
    transfer_uid: str,
    body: TransferReportBody,
    authorization: str = Header(default="", max_length=256),
):
    return _call(
        authorization,
        report_transfer,
        project_uid,
        transfer_uid,
        body.expected_revision,
        body.acknowledgment,
        body.provider_outcome,
    )
