"""Attach evidence to a card using the current owning app connection."""

import re
from pathlib import Path
from uuid import uuid4
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.storage import Storage, StorageName
from ...core.types import SnowflakeID
from ...helpers import InfraHelper
from ...tasks.activities.utils import ActivityHistoryHelper, ActivityTaskHelper
from ..models import Card, CardAttachment, Project, ProjectActivity, User
from ..models.ProjectActivity import ProjectActivityType
from .AppCardScope import require_app_card_scope
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionGrant import AppExecutionCredentialDenied
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .CardAttachmentUploadOperations import _fingerprint, _key
from .DomainService import DomainService


APP_ATTACHMENT_CAPABILITY = "attachments.write"


def _validate_file_key(file_key: str) -> None:
    if not isinstance(file_key, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", file_key) is None:
        raise ValueError("Invalid attachment file key")


def _scope(db, token, project_id, card_id, *, readback=False):
    lock_policy_mutation(db)
    if not readback and DbEngine.get_main_engine().dialect.name == "sqlite":
        # Reserve the writer before checking the immutable receipt.
        db.exec(update(Card).where(Card.id == card_id).values(id=Card.id))
    try:
        principal = authenticate_connection_credential(token)
    except AppGovernanceDenied as exc:
        raise AppExecutionCredentialDenied() from exc
    card, *_ = require_app_card_scope(
        db,
        principal,
        project_id,
        card_id,
        capability=APP_ATTACHMENT_CAPABILITY,
        role_action="card_update",
        allow_archived=readback,
    )
    return principal, card


def _read_receipt(db, card, connection_uid, file_key):
    history = ProjectActivity.activity_history["app_attachment_receipt"]
    row = db.exec(
        SqlBuilder.select.table(ProjectActivity)
        .where(
            ProjectActivity.project_id == card.project_id,
            ProjectActivity.card_id == card.id,
            ProjectActivity.activity_type == ProjectActivityType.CardAttachmentUploaded,
            history["connection_uid"].as_string() == connection_uid,
            history["file_key"].as_string() == file_key,
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    receipt = row.activity_history["app_attachment_receipt"]
    attachment = _current(db, CardAttachment, InfraHelper.convert_id(receipt["attachment_uid"]))
    available = (
        attachment is not None
        and attachment.card_id == card.id
        and attachment.replaced_by_id is None
        and attachment.content_sha256 == receipt["sha256"]
        and attachment.image_revision == receipt["revision"]
    )
    return {**receipt, "available": available}


def read_app_card_attachment_receipt(token, project_id, card_id, file_key):
    _validate_file_key(file_key)
    with DbSession.atomic() as db:
        principal, card = _scope(db, token, project_id, card_id, readback=True)
        receipt = _read_receipt(db, card, SnowflakeID(principal.connection_id).to_short_code(), file_key)
        return {"found": receipt is not None, "receipt": receipt}


def upload_app_card_attachment(token, project_id, card_id, file_key, sha256, byte_size, upload):
    _validate_file_key(file_key)
    if DbSession.has_active_transaction():
        raise ValueError("Attachment upload must own its storage transaction")
    filename, digest, size = _fingerprint(upload)
    if not 1 <= len(filename) <= 255 or any(ord(char) < 32 for char in filename):
        raise ValueError("Invalid attachment filename")
    if sha256 != digest or type(byte_size) is not int or byte_size <= 0 or byte_size != size:
        raise ValueError("Attachment bytes do not match the declared SHA-256 and size")
    stored = None
    committed = False
    try:
        with DbSession.atomic() as db:
            principal, card = _scope(db, token, project_id, card_id)
            connection_uid = SnowflakeID(principal.connection_id).to_short_code()
            receipt = _read_receipt(db, card, connection_uid, file_key)
            if receipt is not None:
                if (receipt["filename"], receipt["sha256"], receipt["byte_size"]) != (filename, digest, size):
                    raise AppGovernanceConflict()
                return {"created": False, "receipt": receipt}
            project = _current(db, Project, project_id)
            actor = _current(db, User, principal.owner_id)
            service = DomainService().card_attachment
            stored = Storage.upload_named(
                upload.file, StorageName.CardAttachment, "ref_" + uuid4().hex + Path(filename).suffix, filename
            )
            if stored is None:
                raise RuntimeError("Attachment storage unavailable")
            if service.resolve_file_owner(*_key(stored)) is not None:
                stored = None
                raise RuntimeError("Attachment storage did not allocate an independent object")
            attachment = service.create(actor, project, card, stored, dispatch_effects=False, content_sha256=digest)
            if attachment is None:
                raise RuntimeError("Attachment creation failed")
            receipt = {
                "schema_version": 1,
                "app_key": principal.app_key,
                "connection_uid": connection_uid,
                "card_uid": card.get_uid(),
                "attachment_uid": attachment.get_uid(),
                "file_key": file_key,
                "filename": filename,
                "sha256": digest,
                "byte_size": size,
                "revision": attachment.image_revision,
            }
            helper = ActivityTaskHelper(ProjectActivity)
            helper.record(
                actor,
                {
                    **helper.create_project_default_history(project, card=card),
                    "attachment": ActivityHistoryHelper.create_card_attachment_history(attachment),
                    "app_attachment_receipt": receipt,
                },
                email_fanout_pending=False,
                **helper.get_card_activity_params(ProjectActivityType.CardAttachmentUploaded, project, card),
            )
            service.dispatch_created(
                actor,
                project,
                card,
                attachment,
                include_bot=False,
                include_document_processing=False,
                include_activity=False,
            )
        committed = True
        return {"created": True, "receipt": {**receipt, "available": True}}
    finally:
        if stored is not None and not committed:
            Storage.delete(stored)
