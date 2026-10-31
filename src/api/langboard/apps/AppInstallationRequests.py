"""Stage validated immutable packages without registering or activating apps."""

from hashlib import sha256
from io import BytesIO
from langboard_shared.core.db import DbSession
from langboard_shared.core.storage import FileModel, Storage, StorageName
from langboard_shared.domain.models import AppInstallationRequest
from langboard_shared.domain.services.AppInstallationRequests import authorize_submission, existing_request, identity
from langboard_shared.domain.services.AppPackage import validate_package
from .AppPackageInstallation import prepare_package_installation
from .OfficialAppPackages import download_official_package


def submit_request(actor, request_key, *, key=None, tag=None, body=None, expected_revision=None, project_uid=None):
    selector = (
        {"source": "manual", "sha256": sha256(body).hexdigest()}
        if body is not None
        else {"source": "official", "key": key, "tag": tag}
    )
    fingerprint = identity(request_key, selector, expected_revision, project_uid)
    with DbSession.atomic() as db:
        actor, project = authorize_submission(db, actor, project_uid)
        # A completed retry returns its original immutable artifact before I/O.
        previous = existing_request(db, actor, request_key, fingerprint)
        if previous:
            return previous.response()
    # Download and stage outside authority/policy locks. The final short unit
    # rechecks current authority and arbitrates the submitted retry identity.
    provenance = {"kind": "manual"}
    if body is None:
        body, provenance = download_official_package(key, tag)
    package = validate_package(body)
    stored = Storage.upload_named(BytesIO(body), StorageName.AppPackage, f"{package['sha256']}.zip", "app.zip")
    if stored is None:
        raise ValueError("Package storage unavailable")
    declaration = package["declaration"]
    with DbSession.atomic() as db:
        actor, project = authorize_submission(db, actor, project_uid)
        previous = existing_request(db, actor, request_key, fingerprint)
        if previous:
            return previous.response()
        row = AppInstallationRequest(
            requester_id=actor.id,
            request_key=request_key,
            project_id=project.id if project else None,
            fingerprint=fingerprint,
            expected_app_revision=expected_revision,
            artifact={
                "key": declaration["key"],
                "name": declaration["name"],
                "version": declaration["version"],
                "sha256": package["sha256"],
                "size": package["size"],
                "provenance": provenance,
                "storage": stored.model_dump(),
            },
        )
        db.insert(row)
        return row.response()


def prepare_requested_package(actor, artifact, expected_revision):
    body = Storage.get_file(FileModel(**artifact["storage"]))
    if body is None or sha256(body).hexdigest() != artifact["sha256"]:
        raise ValueError("Requested package is unavailable or changed")
    package = validate_package(body)
    if package["declaration"]["key"] != artifact["key"] or package["declaration"]["version"] != artifact["version"]:
        raise ValueError("Requested package identity changed")
    return prepare_package_installation(actor, body, provenance=artifact["provenance"])


def commit_requested_package(actor, prepared, expected_revision):
    from langboard_shared.domain.services.AppRegistry import save_definition

    declaration, metadata = prepared
    return save_definition(actor, declaration, expected_revision, package=metadata)
