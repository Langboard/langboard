"""Authenticated Devtron setup and explicit verification of selected resources."""

from typing import Annotated
import httpx
from fastapi import Path, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict
from langboard_shared.domain.services.factory.SecretReferenceService import SecretReferenceUnavailable
from langboard_shared.helpers import InfraHelper
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from ...apps import DevtronConnection as devtron
from ...apps.MetadataTransport import MetadataUnavailable


class AppRevisionForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConnectionForm(AppRevisionForm):
    instance_url: str = Field(min_length=1, max_length=2048)
    credential_reference: str = Field(pattern=r"^secret://ref/[A-Za-z0-9]{1,11}$")
    organization_uid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")
    deployment_scope: dict[str, StrictInt | StrictStr] | None = None


class DiscoveryForm(AppRevisionForm):
    search: str = Field(default="", max_length=200)
    offset: int = Field(default=0, strict=True, ge=0, le=1_000_000_000)
    size: int = Field(default=50, strict=True, ge=1, le=250)


class ResourceVerificationForm(AppRevisionForm):
    binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    access_revision: int = Field(strict=True, ge=0)
    helm_app_id: str | None = Field(default=None, min_length=1, max_length=200)


def _error(code, status, provider_status=None):
    error = {"code": code}
    if provider_status is not None:
        error["status"] = provider_status
    return JsonResponse(content={"ok": False, "error": error}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _response(command, *args, **kwargs):
    try:
        result = command(*args, **kwargs)
    except (AppGovernanceDenied, SecretReferenceUnavailable):
        return _error("forbidden", 403)
    except AppRegistryConflict:
        return _error("stale_binding", 409)
    except ValueError:
        return _error("invalid_request", 400)
    except MetadataUnavailable as exc:
        return _error("upstream_error", 200, exc.status_code)
    except httpx.HTTPError:
        return _error("unavailable", 503)
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/board/{project_uid}/settings/apps/devtron-panel/connections", tags=["Board.Settings"])
@AuthFilter.add("user")
def create_devtron_connection(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: ConnectionForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    organization_id = InfraHelper.convert_id(form.organization_uid) if form.organization_uid is not None else None
    return _response(devtron.register_connection, service, user, project_uid, form.app_revision,
                     form.instance_url, form.credential_reference, organization_id,
                     deployment_scope=form.deployment_scope)


@AppRouter.api.post(
    "/board/{project_uid}/settings/apps/devtron-panel/connections/{connection_uid}/discover", tags=["Board.Settings"]
)
@AuthFilter.add("user")
def discover_devtron_applications(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: DiscoveryForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _response(devtron.discover_applications, service, user, project_uid, form.app_revision, connection_uid,
                     search=form.search, offset=form.offset, size=form.size)


@AppRouter.api.post(
    "/board/{project_uid}/settings/apps/devtron-panel/connections/{connection_uid}/resources/{resource_uid}/verify",
    tags=["Board.Settings"],
)
@AuthFilter.add("user")
def verify_devtron_resource(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    resource_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: ResourceVerificationForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _response(devtron.verify_selected_resource, service, user, project_uid, form.app_revision,
                     form.binding_revision, connection_uid, resource_uid, form.access_revision,
                     helm_app_id=form.helm_app_id)


@AppRouter.api.get("/board/{project_uid}/settings/apps/devtron-panel/connections", tags=["Board.Settings"])
@AuthFilter.add("user")
def get_devtron_connections(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    organization_uid: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    after: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    connection_uid: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
):
    return _response(devtron.list_connections, user, project_uid,
                     organization_id=InfraHelper.convert_id(organization_uid) if organization_uid else None,
                     after=after, limit=limit, connection_uid=connection_uid)


class ConnectionRevisionForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.post(
    "/board/{project_uid}/settings/apps/devtron-panel/connections/{connection_uid}/disconnect", tags=["Board.Settings"]
)
@AuthFilter.add("user")
def disconnect_devtron_connection(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: ConnectionRevisionForm,
    user: User = Auth.scope("user"),
):
    return _response(devtron.disconnect_connection, user, project_uid, connection_uid, form.expected_revision)


@AppRouter.api.post("/board/{project_uid}/settings/apps/devtron-panel/secret-input", tags=["Board.Settings"])
@AuthFilter.add("user")
def request_devtron_secret_input(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: AppRevisionForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    from uuid import uuid4
    from langboard_shared.core.db import DbSession
    from ...secrets.SecretInput import begin_input

    def begin():
        with DbSession.atomic() as db:
            devtron._scope(db, user, project_uid, form.app_revision, None)
        return begin_input(service, user, "personal", "me", "devtron/api-" + uuid4().hex)

    return _response(begin)


@AppRouter.api.get("/board/{project_uid}/settings/apps/devtron-panel/secret-input/{input_uid}", tags=["Board.Settings"])
@AuthFilter.add("user")
def get_devtron_secret_input(
    project_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    input_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{43}$")],
    app_revision: str = Query(pattern=r"^[0-9a-f]{64}$"),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    from langboard_shared.core.db import DbSession
    from ...secrets.SecretInput import input_status

    def status():
        with DbSession.atomic() as db:
            devtron._scope(db, user, project_uid, app_revision, None)
        return input_status(service, user, input_uid)

    return _response(status)
