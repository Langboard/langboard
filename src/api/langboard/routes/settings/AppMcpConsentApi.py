"""User-authorized connection consent, never app Bearer self-approval."""

from typing import Annotated, Literal
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppMcpConnectionConsent import (
    read_mcp_connection_consent,
    save_mcp_connection_consent,
)
from langboard_shared.security import Auth
from pydantic import Field
from .AppOperation import run_consent_operation as _run


@form_model
class McpConsentBody(BaseFormModel):
    model_config = {"extra": "forbid"}
    scopes: list[Literal["read", "write", "automation"]] = Field(max_length=3)
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    expected_identity_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.get("/settings/apps/connections/{connection_uid}/mcp-consent", tags=["AppSettings.MCP"])
@AuthFilter.add("user")
def get_mcp_consent(
    connection_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")], user: User = Auth.scope("user")
):
    return _run(read_mcp_connection_consent, user, SnowflakeID.from_short_code(connection_uid))


@AppRouter.api.put("/settings/apps/connections/{connection_uid}/mcp-consent", tags=["AppSettings.MCP"])
@AuthFilter.add("user")
def put_mcp_consent(
    connection_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    form: McpConsentBody,
    user: User = Auth.scope("user"),
):
    return _run(
        save_mcp_connection_consent,
        user,
        SnowflakeID.from_short_code(connection_uid),
        form.scopes,
        form.expected_revision,
        form.expected_identity_revision,
        form.expected_app_revision,
    )
