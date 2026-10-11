"""Native inbound service onboarding; no provider credentials or upstream verification."""

from typing import Annotated
from fastapi import Path, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppConnectionManagement import (
    create_inbound_connection,
    disconnect_inbound_connection,
    list_inbound_connections,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict
from langboard_shared.security import Auth
from pydantic import Field


@form_model
class InboundConnectionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    organization_uid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")


def _run(operation, *args, **kwargs):
    try:
        return JsonResponse(content=operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    except AppRegistryConflict as exc:
        raise ApiException.Conflict_409(ApiErrorCode.EX3004) from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from exc


@AppRouter.api.post("/settings/apps/registry/{app_key}/inbound-connections", tags=["AppSettings.Connections"])
@AuthFilter.add("user")
def create_connection(
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    form: InboundConnectionForm,
    user: User = Auth.scope("user"),
) -> JsonResponse:
    return _run(
        create_inbound_connection,
        user,
        app_key,
        form.app_revision,
        SnowflakeID.from_short_code(form.organization_uid) if form.organization_uid else None,
    )


@form_model
class DisconnectInboundForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.get("/settings/apps/registry/{app_key}/inbound-connections", tags=["AppSettings.Connections"])
@AuthFilter.add("user")
def list_connections(
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    user: User = Auth.scope("user"),
    organization_uid: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    after: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    limit: int = Query(default=25, ge=1, le=50),
) -> JsonResponse:
    return _run(
        list_inbound_connections,
        user,
        app_key,
        organization_id=SnowflakeID.from_short_code(organization_uid) if organization_uid else None,
        after_id=SnowflakeID.from_short_code(after) if after else None,
        limit=limit,
    )


@AppRouter.api.post("/settings/apps/inbound-connections/{connection_uid}/disconnect", tags=["AppSettings.Connections"])
@AuthFilter.add("user")
def disconnect_connection(
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: DisconnectInboundForm,
    user: User = Auth.scope("user"),
) -> JsonResponse:
    return _run(
        disconnect_inbound_connection, user, SnowflakeID.from_short_code(connection_uid), form.expected_revision
    )
