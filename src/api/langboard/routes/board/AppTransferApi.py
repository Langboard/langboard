"""Human transfer review, stable-request reconciliation and explicit delivery retry."""

from fastapi import Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
)
from langboard_shared.domain.services.AppTransfers import (
    create_transfer,
    list_sender_transfers,
    read_sender_transfer,
    retry_transfer,
    review_transfer,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class TransferIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_revision: int = Field(strict=True, ge=0)
    resource_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    attachment_uid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")
    attachment_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class TransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=36, max_length=36)
    connection_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    intent: TransferIntent
    expected_authority: dict


class TransferRetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_retry_revision: int = Field(strict=True, ge=0)


def _call(operation, *args, **kwargs):
    try:
        return JsonResponse(
            content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"}
        )
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except AppGovernanceConflict:
        raise ApiException.Conflict_409() from None
    except (ValueError, TypeError):
        raise ApiException.BadRequest_400() from None


@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/transfer-review",
    tags=["Board.Apps"],
)
@AuthFilter.add("user")
def transfer_review(
    project_uid: str,
    card_uid: str,
    app_key: str,
    attachment_uid: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    after: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    user: User = Auth.scope("user"),
):
    return _call(
        review_transfer,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        app_key,
        SnowflakeID.from_short_code(attachment_uid) if attachment_uid else None,
        after_id=SnowflakeID.from_short_code(after) if after else None,
    )


@AppRouter.api.post(
    "/board/{project_uid}/card/{card_uid}/apps/{app_key}/transfers", tags=["Board.Apps"]
)
@AuthFilter.add("user")
def transfer_create(
    project_uid: str,
    card_uid: str,
    app_key: str,
    body: TransferBody,
    user: User = Auth.scope("user"),
):
    return _call(
        create_transfer,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(card_uid),
        app_key,
        body.client_request_id,
        SnowflakeID.from_short_code(body.connection_uid),
        body.intent.model_dump(),
        body.expected_authority,
    )


@AppRouter.api.get(
    "/board/{project_uid}/app-transfers/by-request/{client_request_id}",
    tags=["Board.Apps"],
)
@AuthFilter.add("user")
def sender_transfer(
    project_uid: str, client_request_id: str, user: User = Auth.scope("user")
):
    return _call(
        read_sender_transfer,
        user,
        SnowflakeID.from_short_code(project_uid),
        client_request_id,
    )


@AppRouter.api.post(
    "/board/{project_uid}/app-transfers/by-request/{client_request_id}/retry",
    tags=["Board.Apps"],
)
@AuthFilter.add("user")
def sender_transfer_retry(
    project_uid: str,
    client_request_id: str,
    body: TransferRetryBody,
    user: User = Auth.scope("user"),
):
    return _call(
        retry_transfer,
        user,
        SnowflakeID.from_short_code(project_uid),
        client_request_id,
        body.expected_retry_revision,
    )


@AppRouter.api.get("/board/{project_uid}/apps/{app_key}/transfers", tags=["Board.Apps"])
@AuthFilter.add("user")
def sender_transfers(
    project_uid: str,
    app_key: str,
    after: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Auth.scope("user"),
):
    return _call(
        list_sender_transfers,
        user,
        SnowflakeID.from_short_code(project_uid),
        app_key,
        after_id=SnowflakeID.from_short_code(after) if after else None,
        limit=limit,
    )
