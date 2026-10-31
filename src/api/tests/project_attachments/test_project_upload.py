"""Project uploads preserve current authority, immutable retry results and private bytes."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import UploadFile
from langboard.routes.file import FileApi
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import ApiException
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
    CardAttachment,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    EmployeeMembershipPolicy,
    InternalBot,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectAttachmentDocument,
    ProjectAttachmentUploadReceipt,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    User,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import delete, update


pytestmark = pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)


@pytest.fixture
def cloud(mirror_board, monkeypatch, tmp_path):
    b = mirror_board
    for model in (AttachmentStorageObject, AttachmentStorageReference):
        model.__table__.create(b.engine, checkfirst=True)
    for model in (CardAttachment, EmployeeMembershipPolicy, ProjectWiki, ProjectWikiAttachment, ProjectWikiAssignedUser):
        model.__table__.create(b.engine, checkfirst=True)
    ProjectAttachment.__table__.create(b.engine)
    ProjectAttachmentUploadReceipt.__table__.create(b.engine)
    ProjectAttachmentDocument.__table__.create(b.engine)
    InternalBot.__table__.create(b.engine)
    DocumentExtractionCache.__table__.create(b.engine)
    DocumentExtractionCacheReference.__table__.create(b.engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    domain = DomainService()
    b.cloud = domain.project_attachment
    b.http = SimpleNamespace(project_attachment=b.cloud)
    monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: b.actor)
    yield b
    domain.close()


def upload(b, name="report.pdf", data=b"first", request_uid=None, **kwargs):
    return b.cloud.upload(
        b.actor,
        b.source_board,
        UploadFile(BytesIO(data), filename=name),
        request_uid=request_uid or str(uuid4()),
        **kwargs,
    )


def rows(model, **kwargs):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model, **kwargs)).all()


def choose(b, proposal, action="replace", data=b"second", **kwargs):
    return upload(
        b,
        data=data,
        request_uid=proposal["request_uid"],
        duplicate_action=action,
        confirmation_token=proposal["confirmation_token"],
        replacement_uid=proposal["conflicts"][0]["uid"] if action == "replace" else None,
        **kwargs,
    )


def download(b, file):
    parts = file.path.split("/")
    response = FileApi.get_file(SimpleNamespace(scope={}), *parts[-3:], b.http)
    spool = response.background.func.__self__
    try:
        return spool.read(), response
    finally:
        spool.close()


def test_native_upload_exact_delivery_and_no_context(cloud):
    result = upload(cloud, folder_id="documents")
    assert result["folder_id"] == "documents" and result["project_uid"] == cloud.source_board.get_uid()
    assert not {"card_uid", "wiki_uid", "content_sha256", "file"} & result.keys()
    attachment = rows(ProjectAttachment)[0]
    data, response = download(cloud, attachment.file)
    assert data == b"first" and response.headers["cache-control"] == "private, no-store"
    assert (
        cloud.cloud.resolve_readable_file(cloud.actor, "local", StorageName.ProjectAttachment.value, "orphan") is None
    )


def test_replace_stable_id_receipt_snapshot_and_old_key_follows_current(cloud):
    first = upload(cloud)
    original = rows(ProjectAttachment)[0].file
    proposal = upload(cloud, data=b"second")
    assert proposal["confirmation_required"] and proposal["conflicts"][0]["can_replace"]
    replaced = choose(cloud, proposal)
    assert replaced["uid"] == first["uid"] and replaced["url"] != first["url"]
    assert download(cloud, rows(ProjectAttachment)[0].file)[0] == b"second"
    assert download(cloud, original)[0] == b"second"
    retained = BytesIO()
    assert Storage.download_file(original, retained) and retained.getvalue() == b"first"
    later = upload(cloud, data=b"third")
    choose(cloud, later, data=b"third")
    assert download(cloud, original)[0] == b"third"
    replay = choose(cloud, proposal)
    assert replay == {**replaced, "replayed": True}
    with DbSession.use(readonly=False) as db:
        db.delete(rows(ProjectAttachment)[0])
    assert choose(cloud, proposal) == replay
    assert not rows(ProjectAttachment)
    with pytest.raises(ApiException.NotFound_404):
        download(cloud, original)


def test_same_bytes_do_not_allocate_and_explicit_add(cloud, monkeypatch):
    physical = Mock(wraps=Storage._storages["local"].upload_immutable)
    monkeypatch.setattr(Storage._storages["local"], "upload_immutable", physical)
    first = upload(cloud)
    proposal = upload(cloud)
    allocator = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", allocator)
    result = choose(cloud, proposal, data=b"first")
    assert result["url"] == first["url"] and not allocator.called
    other = upload(cloud)
    added = choose(cloud, other, action="add", data=b"first")
    assert added["uid"] != first["uid"] and added["url"] != first["url"]
    assert physical.call_count == len(rows(AttachmentStorageObject)) == 1
    assert len(rows(AttachmentStorageReference)) == 2


def test_terminal_cancel_and_uuid_mismatch(cloud):
    uid = str(uuid4())
    cancelled = upload(cloud, request_uid=uid, duplicate_action="cancel")
    assert upload(cloud, request_uid=uid, duplicate_action="cancel") == {**cancelled, "replayed": True}
    with pytest.raises(ValueError):
        upload(cloud, request_uid=uid)
    assert not rows(ProjectAttachment)
    assert len(rows(ProjectAttachmentUploadReceipt)) == 1


@pytest.mark.parametrize("kwargs", [{"request_uid": "bad"}, {"folder_id": "foreign"}, {"duplicate_action": "auto"}])
def test_validation(cloud, kwargs):
    with pytest.raises(ValueError):
        upload(cloud, **kwargs)


def test_missing_uuid(cloud):
    with pytest.raises(ValueError):
        cloud.cloud.upload(cloud.actor, cloud.source_board, UploadFile(BytesIO(b"x"), filename="x"))


def test_stale_conflict_requires_new_confirmation(cloud):
    upload(cloud)
    proposal = upload(cloud, data=b"second")
    another = upload(cloud, data=b"third")
    choose(cloud, another, data=b"third")
    stale = choose(cloud, proposal)
    assert stale["confirmation_required"] and stale["confirmation_token"] != proposal["confirmation_token"]
    assert len(rows(ProjectAttachmentUploadReceipt)) == 2


@pytest.mark.parametrize("revocation", ["membership", "read", "inactive", "project"])
def test_current_authority_rechecked_on_retry_and_download(cloud, revocation):
    first = upload(cloud)
    attachment = rows(ProjectAttachment)[0]
    with DbSession.use(readonly=False) as db:
        if revocation == "membership":
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == cloud.actor.id))
        elif revocation == "read":
            db.exec(update(ProjectRole).where(ProjectRole.user_id == cloud.actor.id).values(actions=[]))
        elif revocation == "inactive":
            db.exec(update(User).where(User.id == cloud.actor.id).values(activated_at=None))
        else:
            db.delete(cloud.source_board)
    assert upload(cloud, request_uid=first["request_uid"]) is None
    assert not cloud.cloud.can_upload(cloud.actor, cloud.source_board)
    with pytest.raises(ApiException.NotFound_404):
        download(cloud, attachment.file)


def test_storage_rollback_on_receipt_failure(cloud, monkeypatch):
    from langboard_shared.core.db.DbSession import DbSession as Session

    native = Session.insert

    def fail_receipt(self, model, *args, **kwargs):
        if isinstance(model, ProjectAttachmentUploadReceipt):
            raise RuntimeError("receipt failure")
        return native(self, model, *args, **kwargs)

    monkeypatch.setattr(Session, "insert", fail_receipt)
    cleanup = Mock(wraps=Storage.delete)
    monkeypatch.setattr(Storage, "delete", cleanup)
    with pytest.raises(RuntimeError):
        upload(cloud)
    assert not rows(ProjectAttachment) and not rows(ProjectAttachmentUploadReceipt)
    cleanup.assert_called_once()


def test_concurrent_identical_request_allocates_once(cloud):
    request_uid = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(cloud, request_uid=request_uid), range(2)))
    assert results[0]["uid"] == results[1]["uid"]
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert len(rows(ProjectAttachment)) == len(rows(ProjectAttachmentUploadReceipt)) == 1


def test_compensation_cannot_release_current_or_historical_native_ownership(cloud):
    upload(cloud)
    original = rows(ProjectAttachment)[0].file
    proposal = upload(cloud, data=b"second")
    choose(cloud, proposal, data=b"second")
    current = rows(ProjectAttachment)[0]
    assert Storage.delete(original) and Storage.delete(original)
    assert Storage.get_file(original) == b"first"
    assert Storage.delete(current.file) and Storage.get_file(current.file) == b"second"
    with DbSession.atomic() as db:
        db.delete(current)
    assert Storage.delete(original) and Storage.delete(current.file)
    assert Storage.get_file(original) == b"first" and Storage.get_file(current.file) == b"second"
    assert not any(reference.released for reference in rows(AttachmentStorageReference))


def test_download_rechecks_authority_after_spool(cloud, monkeypatch):
    upload(cloud)
    native = Storage.download_file

    def revoke(file, spool):
        result = native(file, spool)
        with DbSession.use(readonly=False) as db:
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == cloud.actor.id))
        return result

    monkeypatch.setattr(Storage, "download_file", revoke)
    with pytest.raises(ApiException.NotFound_404):
        download(cloud, rows(ProjectAttachment)[0].file)


def test_ambiguous_or_deleted_provenance_denied(cloud):
    upload(cloud)
    attachment = rows(ProjectAttachment)[0]
    with DbSession.use(readonly=False) as db:
        db.insert(
            ProjectAttachment(
                project_id=attachment.project_id,
                user_id=attachment.user_id,
                filename=attachment.filename,
                file=attachment.file,
                content_sha256=attachment.content_sha256,
            )
        )
    assert (
        cloud.cloud.resolve_readable_file(
            cloud.actor, attachment.file.storage_type, attachment.file.storage_name, attachment.file.filename
        )
        is None
    )


def test_replacement_requires_owner_admin_or_update_and_current_membership(cloud):
    upload(cloud)
    attachment = rows(ProjectAttachment)[0]
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(ProjectAttachment).where(ProjectAttachment.id == attachment.id).values(user_id=cloud.outsider.id)
        )
    proposal = upload(cloud, data=b"second")
    assert not proposal["conflicts"][0]["can_replace"]
    with pytest.raises(PermissionError):
        choose(cloud, proposal)
    with DbSession.use(readonly=False) as db:
        db.exec(update(ProjectRole).where(ProjectRole.user_id == cloud.actor.id).values(actions=["read", "update"]))
    stale = choose(cloud, proposal)
    assert stale["confirmation_required"] and stale["conflicts"][0]["can_replace"]
    replaced = choose(cloud, stale)
    assert replaced["uid"] == attachment.get_uid()
    with DbSession.use(readonly=False) as db:
        db.exec(update(ProjectRole).where(ProjectRole.user_id == cloud.actor.id).values(actions=["read"]))
    with pytest.raises(PermissionError):
        choose(cloud, stale)


def test_foreign_owner_admin_without_membership_denied(cloud):
    with DbSession.use(readonly=False) as db:
        db.exec(update(User).where(User.id == cloud.actor.id).values(is_admin=True))
        db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == cloud.actor.id))
    assert not cloud.cloud.can_upload(cloud.actor, cloud.source_board)
    assert upload(cloud) is None


def test_route_status_headers_and_validation(cloud):
    from langboard.routes.board.BoardCloudAttachmentApi import upload_project_attachment

    request = SimpleNamespace(scope={})

    def call(**kwargs):
        return upload_project_attachment(
            cloud.source_board.get_uid(),
            request,
            UploadFile(BytesIO(b"first"), filename="report.pdf"),
            cloud.actor,
            cloud.http,
            **kwargs,
        )

    with pytest.raises(ApiException.BadRequest_400):
        call(request_uid="bad")
    result = call(request_uid=str(uuid4()))
    assert result.status_code == 201 and result.headers["cache-control"] == "private, no-store"
    proposal = call(request_uid=str(uuid4()))
    assert proposal.status_code == 409 and proposal.headers["cache-control"] == "private, no-store"


def test_migration_up_down_and_populated_guard(cloud):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import BigInteger, Column

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022160000-4f25ab639e87.py"
    spec = spec_from_file_location("project_cloud_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "3e14fa528d76"
    with cloud.engine.begin() as connection:
        ProjectAttachmentDocument.__table__.drop(connection)
        ProjectAttachmentUploadReceipt.__table__.drop(connection)
        ProjectAttachment.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            # Restore the additive alias field for current-model use of this old migration fixture.
            Operations(MigrationContext.configure(connection)).add_column(
                "project_attachment", Column("replaced_by_id", BigInteger(), nullable=True)
            )
        ProjectAttachmentDocument.__table__.create(connection)
    upload(cloud)
    with cloud.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError):
            module.downgrade()
    with cloud.engine.begin() as connection:
        ProjectAttachmentDocument.__table__.drop(connection)
        connection.execute(delete(ProjectAttachmentUploadReceipt))
        connection.execute(delete(ProjectAttachment))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            module.upgrade()
            # Restore the additive alias field for current-model use of this old migration fixture.
            Operations(MigrationContext.configure(connection)).add_column(
                "project_attachment", Column("replaced_by_id", BigInteger(), nullable=True)
            )
        ProjectAttachmentDocument.__table__.create(connection)
    assert upload(cloud)["uid"]
