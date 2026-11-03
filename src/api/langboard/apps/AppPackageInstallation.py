"""Central package installation; board connection consent remains separate."""

from io import BytesIO
from urllib.parse import urlsplit
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.domain.models import AppDefinition
from langboard_shared.domain.services.AppPackage import validate_package
from langboard_shared.domain.services.AppRegistry import _admin, save_definition
from langboard_shared.Env import Env


def prepare_package_installation(actor, body, *, provenance=None):
    with DbSession.atomic() as db:
        _admin(db, actor)
    package = validate_package(body)
    declaration = package["declaration"]
    origin = Env.API_URL.rstrip("/")
    parsed = urlsplit(origin)
    host = urlsplit(Env.PUBLIC_UI_URL.rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or host.scheme != "https"
        or not host.hostname
        or host.username
        or host.password
        or host.path
        or host.query
        or host.fragment
    ):
        raise ValueError("Installed panels require a public HTTPS API URL")
    declaration["panel"]["url"] = (
        f"{origin}/apps/packages/{declaration['key']}/{package['sha256']}/{package['panel_entry']}"
    )
    if declaration.get("trust") is not None:
        declaration["trust"]["data_origins"] = sorted({*declaration["trust"]["data_origins"], origin})
    stored = Storage.upload_named(BytesIO(body), StorageName.AppPackage, f"{package['sha256']}.zip", "app.zip")
    if stored is None:
        raise ValueError("Package storage unavailable")
    # A failed/racing approval retains an immutable harmless object; no overwrite or code execution.
    metadata = {
        "kind": "manual",
        "sha256": package["sha256"],
        "size": package["size"],
        "panel_entry": package["panel_entry"],
        "store": package["store"],
        **(provenance or {}),
        "storage": stored.model_dump(),
    }
    return declaration, metadata


def install_package(actor, body, expected_revision=None, *, provenance=None):
    declaration, metadata = prepare_package_installation(actor, body, provenance=provenance)
    return save_definition(actor, declaration, expected_revision, package=metadata)


def package_for_asset(key, digest):
    with DbSession.use(readonly=False) as db:
        row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key)).first()
        if row is None or not row.is_enabled or not row.package or row.package["sha256"] != digest:
            return None
        return row.package
