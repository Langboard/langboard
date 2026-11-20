"""Native panel read boundary; provider I/O never holds a board transaction."""

from typing import Annotated
import httpx
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.factory.SecretReferenceService import SecretReferenceUnavailable
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from ...apps.ConnectionReads import read_connection
from ...apps.MetadataTransport import MetadataUnavailable


class ConnectionReadForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    connection_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    read_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    input: dict[str, JsonValue]


def _error(code, status, upstream_status=None):
    error = {"code": code}
    if upstream_status is not None:
        error["status"] = upstream_status
    return JsonResponse(content={"ok": False, "error": error}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/panel/connections/read", tags=["Board.Apps"])
@AuthFilter.add("user")
def read_panel_connection(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    form: ConnectionReadForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    try:
        result = read_connection(service, user, project_uid, app_key, form.app_revision,
                                 form.binding_revision, form.connection_uid, form.read_key, form.input)
    except (AppGovernanceDenied, SecretReferenceUnavailable):
        return _error("forbidden", 403)
    except AppGovernanceConflict:
        return _error("stale_binding", 409)
    except ValueError:
        return _error("invalid_request", 400)
    except MetadataUnavailable as exc:
        # A provider rejection is not a host session failure or retry instruction.
        return _error("upstream_error", 200, exc.status_code)
    except httpx.HTTPError:
        return _error("unavailable", 503)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
