"""Official release download and manual ZIP installation use one approval path."""

import json
import re
from hashlib import sha256
from io import BytesIO
from mimetypes import guess_type
from zipfile import ZipFile
from fastapi import File, Form, Query, UploadFile
from langboard_shared.core.db import DbSession
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppCatalog import list_catalog
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppPackage import MAX_PACKAGE_BYTES
from langboard_shared.domain.services.AppRegistry import _admin, list_definitions
from langboard_shared.Env import Env
from langboard_shared.security import Auth
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from ...apps.AppPackageInstallation import install_package, package_for_asset
from ...apps.OfficialAppPackages import OFFICIAL_APPS, download_official_package
from .AppRegistrySettingsApi import _run


@AppRouter.api.get("/apps/catalog", tags=["Apps"])
@AuthFilter.add("user")
def get_catalog(
    project_uid: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    after: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}(?:~(?:composition|module))?$"),
    limit: int = Query(default=25, ge=1, le=50),
    key: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$"),
    composition_key: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$"),
    include_compositions: bool = Query(default=False),
    user: User = Auth.scope("user"),
) -> JsonResponse:
    try:
        catalog = _run(
            list_catalog,
            user,
            project_uid=project_uid,
            after_key=after,
            limit=limit,
            app_key=key,
            composition_key=composition_key,
            include_compositions=include_compositions,
            featured_keys=tuple(item.key for item in OFFICIAL_APPS),
        )
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc
    featured = catalog.pop("featured")
    groups = [item for item in featured if item.get("kind") == "composition"]
    grouped_keys = {key for item in groups for key in item["module_keys"]}
    modules = {item["key"]: item for item in featured if item.get("kind") != "composition"}
    # Static suggestions retain exact module install identities; grouped installed
    # members appear once through their explicit composition.
    catalog["official"] = (
        []
        if catalog["effective_mode"] == "disabled"
        else [
            *groups,
            *[
                {
                    "key": item.key,
                    "name": item.name,
                    "description": item.description,
                    "repository": item.repository,
                    "source": "official",
                    **modules.get(item.key, {}),
                }
                for item in OFFICIAL_APPS
                if item.key not in grouped_keys
            ],
        ]
    )
    return JsonResponse(content=catalog, headers={"Cache-Control": "no-store"})


@AppRouter.api.get("/settings/apps/packages", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def get_packages(user: User = Auth.scope("user")) -> JsonResponse:
    installed = _run(list_definitions, user)
    return JsonResponse(
        content={
            "installed": installed,
            "official": [
                {
                    "key": item.key,
                    "repository": item.repository,
                    "asset_name": item.asset_name,
                    "name": item.name,
                    "description": item.description,
                    "icon_url": f"https://github.com/{item.repository}/releases/latest/download/icon-light.svg",
                    "icon_dark_url": f"https://github.com/{item.repository}/releases/latest/download/icon-dark.svg",
                }
                for item in OFFICIAL_APPS
            ],
        }
    )


@AppRouter.api.post("/settings/apps/packages/upload", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
async def upload_package(
    file: UploadFile = File(...), expected_revision: str | None = Form(None), user: User = Auth.scope("user")
) -> JsonResponse:
    if expected_revision is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    body = await file.read(MAX_PACKAGE_BYTES + 1)
    return JsonResponse(content={"app": await run_in_threadpool(_run, install_package, user, body, expected_revision)})


@form_model
class InstallOfficialForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    tag: str | None = Field(default=None, pattern=r"^v?[0-9]+\.[0-9]+\.[0-9]+$")
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.post("/settings/apps/packages/official/{key}/install", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def install_official(key: str, form: InstallOfficialForm, user: User = Auth.scope("user")) -> JsonResponse:
    def operation():
        with DbSession.atomic() as db:
            _admin(db, user)
        body, provenance = download_official_package(key, form.tag)
        return install_package(user, body, form.expected_revision, provenance=provenance)

    return JsonResponse(content={"app": _run(operation)})


@AppRouter.api.get("/apps/packages/{key}/{digest}/{asset:path}", tags=["AppPanels"])
def get_package_asset(key: str, digest: str, asset: str) -> Response:
    if (
        not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not asset.startswith("panel/")
    ):
        raise ApiException.NotFound_404()
    package = package_for_asset(key, digest)
    if package is None:
        raise ApiException.NotFound_404()
    body = Storage.get_file(FileModel(**package["storage"]))
    if body is None or sha256(body).hexdigest() != digest:
        raise ApiException.NotFound_404()
    with ZipFile(BytesIO(body)) as archive:
        try:
            entry = archive.getinfo(asset)
            if entry.is_dir():
                raise KeyError()
            payload = archive.read(entry)
        except KeyError as exc:
            raise ApiException.NotFound_404() from exc
    host = Env.PUBLIC_UI_URL.rstrip("/")
    if asset == "panel/host.mjs":
        payload = f"export const hostOrigin = {json.dumps(host)};".encode()
    return Response(
        payload,
        media_type=guess_type(asset)[0] or "application/octet-stream",
        headers={
            "Access-Control-Allow-Origin": "*",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"sandbox allow-scripts; default-src 'none'; script-src 'self' {host}; style-src 'self' {host}; img-src 'self' data:; font-src 'self' {host}; connect-src 'none'; frame-ancestors {host}; base-uri 'none'; form-action 'none'",
        },
    )


@form_model
class RequestOfficialPackageForm(InstallOfficialForm):
    request_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_uid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")


@form_model
class ReviewInstallationRequestForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    decision: str = Field(pattern=r"^(approve|reject)$")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(default="", max_length=1000)


def _request_run(operation, *args, **kwargs):
    try:
        return _run(operation, *args, **kwargs)
    except AppGovernanceDenied as exc:
        raise ApiException.Forbidden_403(ApiErrorCode.PE1001) from exc


@AppRouter.api.get("/apps/installation-requests", tags=["Apps"])
@AuthFilter.add("user")
def get_installation_requests(
    after: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
) -> JsonResponse:
    from langboard_shared.domain.services.AppInstallationRequests import list_requests

    return JsonResponse(
        content=_request_run(list_requests, user, after=after, limit=limit), headers={"Cache-Control": "no-store"}
    )


@AppRouter.api.get("/apps/installation-requests/{uid}", tags=["Apps"])
@AuthFilter.add("user")
def get_installation_request(uid: str, user: User = Auth.scope("user")) -> JsonResponse:
    from langboard_shared.domain.services.AppInstallationRequests import get_request

    return JsonResponse(
        content={"request": _request_run(get_request, user, uid)}, headers={"Cache-Control": "no-store"}
    )


@AppRouter.api.post("/apps/installation-requests/official/{key}", tags=["Apps"])
@AuthFilter.add("user")
def request_official_package(
    key: str, form: RequestOfficialPackageForm, user: User = Auth.scope("user")
) -> JsonResponse:
    from ...apps.AppInstallationRequests import submit_request

    result = _request_run(
        submit_request,
        user,
        form.request_key,
        key=key,
        tag=form.tag,
        expected_revision=form.expected_revision,
        project_uid=form.project_uid,
    )
    return JsonResponse(content={"request": result}, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/apps/installation-requests/upload", tags=["Apps"])
@AuthFilter.add("user")
async def request_manual_package(
    file: UploadFile = File(...),
    request_key: str = Form(...),
    expected_revision: str | None = Form(None),
    project_uid: str | None = Form(None),
    user: User = Auth.scope("user"),
) -> JsonResponse:
    from ...apps.AppInstallationRequests import submit_request

    body = await file.read(MAX_PACKAGE_BYTES + 1)
    return JsonResponse(
        content={
            "request": await run_in_threadpool(
                _request_run,
                submit_request,
                user,
                request_key,
                body=body,
                expected_revision=expected_revision,
                project_uid=project_uid,
            )
        },
        headers={"Cache-Control": "no-store"},
    )


@AppRouter.api.post("/settings/apps/installation-requests/{uid}/review", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def review_installation_request(
    uid: str, form: ReviewInstallationRequestForm, user: User = Auth.scope("user")
) -> JsonResponse:
    from langboard_shared.domain.services.AppInstallationRequests import review_request
    from ...apps.AppInstallationRequests import commit_requested_package, prepare_requested_package

    result = _request_run(
        review_request,
        user,
        uid,
        form.decision,
        form.expected_revision,
        form.reason,
        commit_requested_package,
        prepare_requested_package,
    )
    return JsonResponse(content={"request": result}, headers={"Cache-Control": "no-store"})
