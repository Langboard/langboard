"""Native attachment byte reuse behind independent logical FileModel locators.

Allocation joins the caller's write transaction. The database serializes each
content key through publication; no provider call grants attachment authority.
Immutable bytes survive rollback and releases, permitting verified recovery
after provider success followed by lost database publication. Physical garbage
collection is deliberately outside this contract.
"""

from enum import Enum
from hashlib import sha256
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4
from sqlalchemy import Text, cast, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.storage.FileModel import FileModel
from ...core.types import SnowflakeID
from ...core.utils.Encryptor import Encryptor
from ...Env import Env
from ..models.AttachmentStorageObject import AttachmentStorageObject, AttachmentStorageReference


class _PhysicalNamespace(Enum):
    Objects = "attachment_objects"


PRIVATE_NAMESPACE = _PhysicalNamespace.Objects.value
ATTACHMENT_NAMES = {"card_attachment", "project_attachment", "wiki"}
_CHUNK_SIZE = 64 * 1024


def _locator(model, storage_type, storage_name, filename):
    return (
        model.storage_type == storage_type,
        model.storage_name == storage_name,
        model.filename == filename,
    )


def _select_reference(db, storage_type, storage_name, filename, *, lock=False):
    query = SqlBuilder.select.table(AttachmentStorageReference).where(
        *_locator(AttachmentStorageReference, storage_type, storage_name, filename)
    ).execution_options(populate_existing=True)
    return db.exec(query.with_for_update() if lock else query).first()


def _file(reference):
    return FileModel(
        storage_type=reference.storage_type,
        storage_name=reference.storage_name,
        filename=reference.filename,
        original_filename=reference.original_filename,
        path=f"/file/{Encryptor.encrypt(reference.storage_type, "storage_type")}/{reference.storage_name}/{reference.filename}",
    )


def _physical_filename(content_hash, size):
    return f"{content_hash}-{size}"


class _FingerprintDestination:
    def __init__(self, size):
        self.digest = sha256()
        self.size = 0
        self.limit = size

    def write(self, chunk):
        self.size += len(chunk)
        if self.size > self.limit:
            raise ValueError("Stored attachment exceeds its immutable size")
        self.digest.update(chunk)
        return len(chunk)


def _verified(storage, content_hash, size):
    destination = _FingerprintDestination(size)
    try:
        present = storage.download(PRIVATE_NAMESPACE, _physical_filename(content_hash, size), destination)
    except (OSError, ValueError):
        return False
    return present and destination.size == size and destination.digest.hexdigest() == content_hash


def _publish(db, object_id, token):
    """Compare-and-set prevents a superseded allocator from publishing references."""
    if db.exec(
        update(AttachmentStorageObject)
        .where(
            AttachmentStorageObject.id == object_id,
            AttachmentStorageObject.allocation_token == token,
            AttachmentStorageObject.ready.is_(False),
        )
        .values(ready=True)
    ) != 1:
        raise RuntimeError("Attachment allocation lost publication authority")


def upload(storage, source, storage_name, stored_filename, original_filename):
    if (
        not stored_filename
        or Path(stored_filename).name != stored_filename
        or "\\" in stored_filename
        or stored_filename in {".", ".."}
        or not original_filename
    ):
        return None
    # Freeze bytes in a bounded spool before hashing or provider I/O. The caller
    # cannot change the stream between fingerprint and immutable publication.
    with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
        digest, size = sha256(), 0
        while chunk := source.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > Env.MAX_FILE_SIZE_MB * 1024 * 1024:
                raise ValueError("Attachment exceeds the configured upload limit")
            digest.update(chunk)
            spool.write(chunk)
        content_hash = digest.hexdigest()
        with DbSession.atomic() as db:
            # INSERT arbitrates absent rows as well as existing rows on both
            # PostgreSQL and SQLite. Its lock lasts through provider publication.
            dialect = DbEngine.get_main_engine().dialect.name
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            elif dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert
            else:
                raise RuntimeError("Attachment reuse requires native SQLite or PostgreSQL")
            candidate = AttachmentStorageObject(
                storage_type=storage.storage_type,
                content_sha256=content_hash,
                size_bytes=size,
                allocation_token=uuid4().hex,
            )
            for _ in range(32):
                candidate.id = SnowflakeID()
                db.exec(
                    insert(AttachmentStorageObject.__table__)
                    .values(
                        {
                            column.name: getattr(candidate, column.name)
                            for column in AttachmentStorageObject.__table__.columns
                        }
                    )
                    .on_conflict_do_nothing()
                )
                physical = db.exec(
                    SqlBuilder.select.table(AttachmentStorageObject)
                    .where(
                        AttachmentStorageObject.storage_type == storage.storage_type,
                        AttachmentStorageObject.content_sha256 == content_hash,
                        AttachmentStorageObject.size_bytes == size,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).first()
                if physical is not None:
                    break
                # No matching content means the suppressed conflict was an ID
                # collision. Preserve the winner and skip its sequence range.
                SnowflakeID.advance_after_collision(candidate.id)
            else:
                raise RuntimeError("Attachment object identity allocation exhausted")
            reference = _select_reference(db, storage.storage_type, storage_name.value, stored_filename, lock=True)
            if reference is not None:
                if reference.object_id != physical.id or reference.released:
                    raise ValueError("Attachment locator already belongs to another operation")
                return _file(reference)
            # A caller-selected key must not shadow an existing unmanaged native
            # locator, even when its bytes happen to match. No legacy adoption or
            # rewrite is authorized by an ordinary upload.
            legacy = _FingerprintDestination(0)
            try:
                legacy_present = storage.download(storage_name.value, stored_filename, legacy)
            except (OSError, ValueError):
                legacy_present = legacy.size > 0
            if legacy_present or legacy.size:
                raise ValueError("Attachment locator belongs to an unmanaged legacy object")
            if not physical.ready:
                token = uuid4().hex
                physical.allocation_token = token
                db.update(physical)
                if not _verified(storage, content_hash, size):
                    spool.seek(0)
                    uploaded = storage.upload_immutable(
                        spool, original_filename, _PhysicalNamespace.Objects, _physical_filename(content_hash, size)
                    )
                    if uploaded is None:
                        return None
                    # Do not trust a selected provider's return value or partial
                    # writes; verify the published immutable bytes before READY.
                    if not _verified(storage, content_hash, size):
                        raise RuntimeError("Attachment physical publication failed verification")
                _publish(db, physical.id, token)
            elif not _verified(storage, content_hash, size):
                # A committed object must never be repaired by overwriting bytes
                # under another owner's immutable key. Failed verification
                # prevents this acquire from publishing a new logical owner.
                raise RuntimeError("Ready attachment physical object failed verification")
            reference = AttachmentStorageReference(
                object_id=physical.id,
                storage_type=storage.storage_type,
                storage_name=storage_name.value,
                filename=stored_filename,
                original_filename=Path(original_filename).name,
            )
            db.insert(reference)
            return _file(reference)


def resolve(storage_type, storage_name, filename):
    """Return (managed, private locator). Missing/released references fail closed."""
    if storage_name == PRIVATE_NAMESPACE:
        return True, None
    if storage_name not in ATTACHMENT_NAMES:
        return False, None
    with DbSession.use(readonly=False) as db:
        reference = _select_reference(db, storage_type, storage_name, filename)
        if reference is None:
            # New generated logical keys are never legacy provider objects.
            return filename.startswith("ref_"), None
        if reference.released:
            return True, None
        physical = db.exec(
            SqlBuilder.select.table(AttachmentStorageObject).where(
                AttachmentStorageObject.id == reference.object_id, AttachmentStorageObject.ready.is_(True)
            )
        ).first()
        return True, (PRIVATE_NAMESPACE, _physical_filename(physical.content_sha256, physical.size_bytes)) if physical else None


def release(file):
    """Idempotently release a logical reference; shared bytes are never deleted."""
    if file.storage_name == PRIVATE_NAMESPACE:
        return True, False
    if file.storage_name not in ATTACHMENT_NAMES:
        return False, False
    with DbSession.atomic() as db:
        reference = _select_reference(db, file.storage_type, file.storage_name, file.filename, lock=True)
        if reference is None:
            return file.filename.startswith("ref_"), True
        if _native_reference_retained(db, file):
            return True, True
        if not reference.released:
            reference.released = True
            db.update(reference)
        return True, True


def _native_reference_retained(db, file):
    """Compensation cannot release bytes still held by native source snapshots.

    Historical soft-deleted provenance is an owner too. This check deliberately
    does not grant delivery: native current-owner ACL still governs every read.
    """
    from ..models import CardAttachment, ProjectAttachment, ProjectWikiAttachment

    model = {
        "card_attachment": CardAttachment,
        "project_attachment": ProjectAttachment,
        "wiki": ProjectWikiAttachment,
    }[file.storage_name]
    engine = DbEngine.get_main_engine()
    # Narrow native fixtures and standalone storage users need no attachment
    # tables. A schema/query error still propagates; it never permits deletion.
    if engine.dialect.name == "sqlite":
        present = db.exec(
            select(literal_column("name")).select_from(text("sqlite_master")).where(
                literal_column("type") == "table", literal_column("name") == model.__tablename__
            )
        ).first()
    else:
        present = db.exec(select(func.to_regclass(model.__tablename__))).first()[0]
    if not present:
        return False
    column = model.column("file")
    if engine.dialect.name == "postgresql":
        decoded = cast(cast(column, JSONB).op("#>>")(cast([], ARRAY(Text))), JSONB)

        def value(key):
            return decoded.op("->>")(literal_column(f"'{key}'"))
    else:

        def value(key):
            return func.json_extract(func.json_extract(column, literal_column("'$'")), literal_column(f"'$.{key}'"))

    return db.exec(
        SqlBuilder.select.table(model, with_deleted=True)
        .where(
            value("storage_type") == file.storage_type,
            value("storage_name") == file.storage_name,
            value("filename") == file.filename,
        )
        .limit(1)
    ).first() is not None
