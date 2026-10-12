"""App-authenticated declaration discovery; no remote tool invocation."""

from fastapi import Header
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.AppExecutionGrant import AppExecutionCredentialDenied
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppMcpCatalog import read_app_mcp_catalog


@AppRouter.api.get("/apps/v1/boards/{project_uid}/cards/{card_uid}/mcp-tools", tags=["App.MCP"])
def app_mcp_catalog(project_uid: str, card_uid: str, authorization: str = Header(default="", max_length=256)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise ApiException.Unauthorized_401()
    try:
        result = read_app_mcp_catalog(token, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(card_uid))
    except AppExecutionCredentialDenied as exc:
        raise ApiException.Unauthorized_401() from exc
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
