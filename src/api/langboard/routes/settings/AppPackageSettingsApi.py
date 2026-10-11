"""Official release download and manual ZIP installation use one approval path."""
import json
import re
from hashlib import sha256
from io import BytesIO
from mimetypes import guess_type
from zipfile import ZipFile
from fastapi import File, Form, UploadFile
from langboard_shared.core.db import DbSession
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, BaseFormModel, JsonResponse, form_model
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppPackage import MAX_PACKAGE_BYTES
from langboard_shared.domain.services.AppRegistry import _admin, list_definitions
from langboard_shared.Env import Env
from langboard_shared.security import Auth
from pydantic import Field
from starlette.responses import Response
from ...apps.AppPackageInstallation import install_package, package_for_asset
from ...apps.OfficialAppPackages import OFFICIAL_APPS, download_official_package
from .AppRegistrySettingsApi import _run


@AppRouter.api.get("/settings/apps/packages", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
def get_packages(user: User = Auth.scope("user")) -> JsonResponse:
    installed = _run(list_definitions, user)
    return JsonResponse(content={"installed": installed, "official": [
        {"key": item.key, "repository": item.repository, "asset_name": item.asset_name, "name": item.name, "description": item.description, "icon_url": f"https://github.com/{item.repository}/releases/latest/download/icon-light.svg", "icon_dark_url": f"https://github.com/{item.repository}/releases/latest/download/icon-dark.svg"} for item in OFFICIAL_APPS]})


@AppRouter.api.post("/settings/apps/packages/upload", tags=["AppSettings.AppRegistry"])
@AuthFilter.add("admin")
async def upload_package(
    file: UploadFile = File(...), expected_revision: str | None = Form(None), user: User = Auth.scope("user")
) -> JsonResponse:
    if expected_revision is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    body = await file.read(MAX_PACKAGE_BYTES + 1)
    return JsonResponse(content={"app": _run(install_package, user, body, expected_revision)})


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
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not asset.startswith("panel/"):
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
    return Response(payload, media_type=guess_type(asset)[0] or "application/octet-stream", headers={
        "Access-Control-Allow-Origin": "*", "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"sandbox allow-scripts; default-src 'none'; script-src 'self' {host}; style-src 'self' {host}; img-src 'self' data:; font-src 'self' {host}; connect-src 'none'; frame-ancestors {host}; base-uri 'none'; form-action 'none'",
    })
