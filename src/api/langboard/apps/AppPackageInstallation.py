"""Central package installation; board connection consent remains separate."""
from io import BytesIO
from urllib.parse import urlsplit
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.domain.models import AppDefinition
from langboard_shared.domain.services.AppPackage import validate_package
from langboard_shared.domain.services.AppRegistry import _admin, save_definition
from langboard_shared.Env import Env


def install_package(actor, body, expected_revision=None, *, provenance=None):
    with DbSession.atomic() as db:
        _admin(db, actor)
    package = validate_package(body)
    declaration = package["declaration"]
    origin = Env.API_URL.rstrip("/")
    if urlsplit(origin).scheme != "https":
        raise ValueError("Installed panels require a public HTTPS API URL")
    declaration["panel"]["url"] = f"{origin}/apps/packages/{declaration['key']}/{package['sha256']}/{package['panel_entry']}"
    if declaration.get("trust") is not None:
        declaration["trust"]["data_origins"] = sorted({*declaration["trust"]["data_origins"], origin})
    stored = Storage.upload_named(BytesIO(body), StorageName.AppPackage, f"{package['sha256']}.zip", "app.zip")
    if stored is None:
        raise ValueError("Package storage unavailable")
    # A failed/racing approval retains an immutable harmless object; no overwrite or code execution.
    metadata = {"kind": "manual", "sha256": package["sha256"], "size": package["size"],
                "panel_entry": package["panel_entry"], "store": package["store"], **(provenance or {}), "storage": stored.model_dump()}
    return save_definition(actor, declaration, expected_revision, package=metadata)


def package_for_asset(key, digest):
    with DbSession.use(readonly=False) as db:
        row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key)).first()
        if row is None or not row.is_enabled or not row.package or row.package["sha256"] != digest:
            return None
        return row.package
