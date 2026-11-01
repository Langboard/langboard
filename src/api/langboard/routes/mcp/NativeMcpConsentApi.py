"""Human-session management of incoming native OAuth board consent."""

from typing import Annotated, Literal
from urllib.parse import urlsplit
from fastapi import Path, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.NativeMcpConsent import (
    list_native_connections,
    read_native_board_consent,
    save_native_board_consent,
)
from langboard_shared.Env import Env
from langboard_shared.security import Auth
from pydantic import Field
from ..settings.AppOperation import run_consent_operation as _run


Uid = Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")]


@form_model
class NativeBoardConsentBody(BaseFormModel):
    model_config = {"extra": "forbid"}
    scopes: list[Literal["read", "write"]] = Field(max_length=2)
    expected_revision: int | None = Field(strict=True, ge=1)


def server_descriptor():
    base = Env.MCP_OAUTH_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    enabled = bool(
        Env.MCP_OAUTH_ENABLED
        and parsed.scheme == "https"
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )
    resource = f"{base}/stream" if enabled else None
    return {"name": "langboard", "enabled": enabled, "transport_url": resource, "resource": resource}


def _human(request):
    if request.scope.get("collaboration_channel") != CollaborationChannel.HumanUI:
        raise AppGovernanceDenied()


@AppRouter.api.get("/mcp/connections", tags=["MCP"])
@AuthFilter.add("user")
def get_native_mcp_connections(request: Request, user: User = Auth.scope("user")):
    _run(_human, request)
    return _run(lambda: {"server": server_descriptor(), "connections": list_native_connections(user)})


@AppRouter.api.get("/mcp/connections/{connection_uid}/boards/{project_uid}", tags=["MCP"])
@AuthFilter.add("user")
def get_native_mcp_board_consent(
    request: Request, connection_uid: Uid, project_uid: Uid, user: User = Auth.scope("user")
):
    _run(_human, request)
    return _run(
        read_native_board_consent,
        user,
        SnowflakeID.from_short_code(connection_uid),
        SnowflakeID.from_short_code(project_uid),
    )


@AppRouter.api.put("/mcp/connections/{connection_uid}/boards/{project_uid}", tags=["MCP"])
@AuthFilter.add("user")
def put_native_mcp_board_consent(
    request: Request,
    connection_uid: Uid,
    project_uid: Uid,
    form: NativeBoardConsentBody,
    user: User = Auth.scope("user"),
):
    _run(_human, request)
    return _run(
        save_native_board_consent,
        user,
        SnowflakeID.from_short_code(connection_uid),
        SnowflakeID.from_short_code(project_uid),
        form.scopes,
        form.expected_revision,
    )
